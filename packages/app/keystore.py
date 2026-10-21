"""Where the app's own secrets live: the OS keyring, not a file.

Two secrets, neither of which the user ever needs to type:
  * the **token passphrase** that encrypts the Google refresh token at rest
    (tokens.py v2). Generated once, kept in Windows Credential Manager /
    macOS Keychain / libsecret. The encrypted token file is then useless on its
    own -- a backup or a synced folder holds ciphertext and nothing to open it.
  * the **bot signing key** for Telegram button payloads, derived from the
    passphrase so there is one root secret, not two.

``BSYNC_TOKEN_PASSPHRASE`` in the environment overrides the keyring, for
machines without one (a headless Linux box) and for tests.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as _random

SERVICE = "booking-sync"
PASSPHRASE_KEY = "token-passphrase"


class SecretUnavailable(RuntimeError):
    pass


def token_passphrase(override: str = "", create: bool = True) -> str:
    if override:
        return override
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:
        raise SecretUnavailable(
            "no keyring available; set BSYNC_TOKEN_PASSPHRASE in .env instead"
        ) from exc
    try:
        existing = keyring.get_password(SERVICE, PASSPHRASE_KEY)
        if existing:
            return existing
        if not create:
            raise SecretUnavailable("no token passphrase in the keyring yet")
        generated = _random.token_urlsafe(32)
        keyring.set_password(SERVICE, PASSPHRASE_KEY, generated)
        return generated
    except KeyringError as exc:
        raise SecretUnavailable(
            "the OS keyring refused (%s); set BSYNC_TOKEN_PASSPHRASE in .env instead" % type(exc).__name__
        ) from None


def bot_signing_key(passphrase: str) -> bytes:
    """A separate key for a separate purpose, from the one root secret."""
    return hmac.new(passphrase.encode("utf-8"), b"booking-sync/telegram-callback/v1", hashlib.sha256).digest()
