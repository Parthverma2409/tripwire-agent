"""Connecting a Google account: the OAuth redirect flow, with CSRF state.

    browser  -> GET /auth/google/start      app makes a one-time ``state``
    browser  -> accounts.google.com          user consents to the two scopes
    Google   -> GET /auth/google/callback    ?code=...&state=...
    app      -> oauth2.googleapis.com/token  code -> access + refresh token
    app      -> ~/.booking-sync/google-token.json   (AES-GCM, key in the OS keyring)

``state`` is single-use and expires after ten minutes. A callback without a
matching one is refused before the code is even looked at -- otherwise a page
on any site could send the browser to our callback with a code for the
*attacker's* Google account, and the agent would start syncing their calendar.

The scopes are the least-privilege pair already fixed in ``oauth.SCOPES``:
``calendar.events`` and ``gmail.readonly``. Nothing here widens them.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
import urllib.parse
import urllib.request

from packages.providers.oauth import (
    SCOPES,
    GoogleApiClient,
    HttpTransport,
    OAuthCredentials,
    OAuthError,
    OAuthSession,
    authorization_url,
)
from packages.providers.tokens import FORMAT_V2, EncryptedTokenStore, TokenStoreError

from .config import Settings
from .keystore import SecretUnavailable, token_passphrase

STATE_TTL_SEC = 600
REVOKE_URI = "https://oauth2.googleapis.com/revoke"


class AuthFlowError(RuntimeError):
    pass


class GoogleAuth:
    def __init__(self, settings: Settings, transport: HttpTransport | None = None, passphrase: str | None = None):
        self.settings = settings
        self.transport = transport
        self._passphrase = passphrase
        self._states: dict[str, float] = {}
        self._lock = threading.Lock()

    # -- flow --------------------------------------------------------------

    def start_url(self) -> str:
        if not self.settings.google_configured:
            raise AuthFlowError("Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env, then restart the app.")
        state = secrets.token_urlsafe(24)
        with self._lock:
            self._prune()
            self._states[state] = time.monotonic() + STATE_TTL_SEC
        return authorization_url(self.settings.google_client_id, self.settings.redirect_uri, state=state)

    def complete(self, code: str | None, state: str | None, error: str | None = None) -> None:
        if not self._consume(state):
            raise AuthFlowError("This sign-in link is stale or was not started here. Start again from the app.")
        if error:
            raise AuthFlowError("Google did not grant access (%s)." % error)
        if not code:
            raise AuthFlowError("Google sent no authorisation code.")
        store = self._store()
        session = OAuthSession(
            OAuthCredentials(self.settings.google_client_id, self.settings.google_client_secret),
            transport=self.transport,
            store=store,
        )
        try:
            session.exchange_code(code, self.settings.redirect_uri)
        except OAuthError as exc:
            raise AuthFlowError("Google rejected the sign-in: %s" % exc) from None
        if not session.credentials.refresh_token:
            # Without one, access dies in an hour and never comes back.
            store.delete()
            raise AuthFlowError(
                "Google returned no refresh token. Remove the app's access at "
                "myaccount.google.com/permissions and connect again."
            )

    def disconnect(self) -> None:
        store = self._store(create=False)
        if store is None:
            return
        try:
            refresh = store.load().get("refresh_token")
        except TokenStoreError:
            refresh = None
        store.delete()
        if refresh:
            _revoke(refresh)  # best effort; the local token is gone either way

    # -- runtime -----------------------------------------------------------

    def connected(self) -> bool:
        return self.settings.token_path.exists()

    def api_client(self) -> GoogleApiClient | None:
        """A client for the stored credential, or None when not connected."""
        if not self.settings.google_configured or not self.connected():
            return None
        store = self._store()
        payload = store.load()
        if store.file_format() != FORMAT_V2:
            store.save(payload)  # migrate a v1 file the first time it is read
        credentials = OAuthCredentials.from_dict(payload)
        if credentials.client_id != self.settings.google_client_id:
            raise AuthFlowError("The saved Google token belongs to a different OAuth client. Disconnect and connect again.")
        credentials.client_secret = self.settings.google_client_secret
        return GoogleApiClient(OAuthSession(credentials, transport=self.transport, store=store))

    def describe(self) -> dict:
        return {
            "configured": self.settings.google_configured,
            "connected": self.connected(),
            "scopes": list(SCOPES),
            "redirect_uri": self.settings.redirect_uri,
        }

    # -- internals ---------------------------------------------------------

    def _store(self, create: bool = True) -> EncryptedTokenStore | None:
        if not create and not self.settings.token_path.exists():
            return None
        try:
            passphrase = self._passphrase or token_passphrase(self.settings.token_passphrase)
        except SecretUnavailable as exc:
            raise AuthFlowError(str(exc)) from None
        return EncryptedTokenStore(self.settings.token_path, passphrase, fmt=FORMAT_V2)

    def _consume(self, state: str | None) -> bool:
        if not state:
            return False
        with self._lock:
            self._prune()
            for known in list(self._states):
                if hmac.compare_digest(known, state):
                    del self._states[known]
                    return True
        return False

    def _prune(self) -> None:
        now = time.monotonic()
        for key, expires in list(self._states.items()):
            if expires < now:
                del self._states[key]


def _revoke(token: str) -> None:
    body = urllib.parse.urlencode({"token": token}).encode("utf-8")
    request = urllib.request.Request(
        REVOKE_URI, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        urllib.request.urlopen(request, timeout=10).close()
    except Exception:  # noqa: BLE001 -- revocation is a courtesy, not a requirement
        pass

