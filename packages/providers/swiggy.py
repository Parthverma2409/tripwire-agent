"""Swiggy, through its official MCP servers at mcp.swiggy.com.

Swiggy publishes Food, Instamart and Dineout as MCP servers (streamable HTTP,
JSON-RPC) behind OAuth 2.1. Everything here goes through those documented
tools -- no scraping, no private app API, no password ever seen by Tripwire:

    browser -> /auth/swiggy/start        app registers itself once (RFC 7591)
    browser -> mcp.swiggy.com/auth       user signs in with phone + OTP
    Swiggy  -> /auth/swiggy/callback     ?code=...&state=...
    app     -> mcp.swiggy.com/auth/token code + PKCE verifier -> tokens
    app     -> ~/.booking-sync/swiggy-token.json   (AES-GCM, key in the keyring)

Two things Tripwire uses it for:

  * **Food order tracking** (``track_food_order``): a change in an active
    order's status becomes a notification. Read-only.
  * **Dineout tables** (search -> slots -> ``book_table``, then
    ``get_booking_status``): booking commits the user to a reservation, so it
    only ever happens after an explicit yes, and only for free reservations.
    Paid deals need a UPI payment, which belongs in the Swiggy app.

Every tool answers with the envelope ``{success, data, message}`` or
``{success: false, error: {message}}``; ``call`` unwraps it and raises
``SwiggyError`` on failure, so callers only ever see ``data``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

BASE = "https://mcp.swiggy.com"
AUTHORIZE_URL = BASE + "/auth/authorize"
TOKEN_URL = BASE + "/auth/token"
REGISTER_URL = BASE + "/auth/register"
SERVERS = {"food": BASE + "/food", "dineout": BASE + "/dineout"}
SCOPE = "mcp:tools"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "Tripwire (local)"

#: (method, url, headers, body) -> (status, headers, body). Injected by tests.
Transport = Callable[[str, str, dict, bytes | None], tuple[int, dict, bytes]]


class SwiggyError(RuntimeError):
    """A tool or the transport failed. The message is safe to show the user."""


class SwiggyAuthError(SwiggyError):
    """The sign-in is gone (expired, revoked): connect Swiggy again."""


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, exc.read() or b""
    except (urllib.error.URLError, OSError) as exc:
        raise SwiggyError("Swiggy is unreachable (%s)" % getattr(exc, "reason", exc)) from None


# -- OAuth ---------------------------------------------------------------------


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge), the way Swiggy's docs specify: 32 random bytes."""
    verifier = _b64(secrets.token_bytes(32))
    return verifier, _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def register_client(redirect_uri: str, transport: Transport = urllib_transport) -> str:
    """Dynamic client registration. Returns the new public client_id."""
    body = json.dumps({
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }).encode("utf-8")
    status, _, raw = transport("POST", REGISTER_URL, {"Content-Type": "application/json"}, body)
    data = _json(raw)
    if status not in (200, 201) or not data.get("client_id"):
        raise SwiggyError("Swiggy refused to register Tripwire (%s): %s" % (status, _oauth_reason(data, raw)))
    return str(data["client_id"])


def authorization_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def exchange_code(client_id: str, code: str, redirect_uri: str, verifier: str,
                  transport: Transport = urllib_transport, now: float | None = None) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }).encode("utf-8")
    status, _, raw = transport("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    return _token_payload(client_id, status, raw, now)


def refresh_tokens(client_id: str, refresh_token: str, transport: Transport = urllib_transport,
                   now: float | None = None) -> dict:
    # Swiggy documents a JSON body for refresh. Refresh tokens rotate: the
    # caller must store the new one, the old one is dead after this call.
    body = json.dumps({"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}).encode()
    status, _, raw = transport("POST", TOKEN_URL, {"Content-Type": "application/json"}, body)
    return _token_payload(client_id, status, raw, now)


def _token_payload(client_id: str, status: int, raw: bytes, now: float | None) -> dict:
    data = _json(raw)
    if status != 200 or not data.get("access_token"):
        if data.get("error") == "invalid_grant":
            raise SwiggyAuthError("Swiggy sign-in expired. Connect Swiggy again.")
        raise SwiggyError("Swiggy sign-in failed (%s): %s" % (status, _oauth_reason(data, raw)))
    issued = time.time() if now is None else now
    return {
        "client_id": client_id,
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "issued_at": issued,
        "expires_at": issued + float(data.get("expires_in") or 3600),
    }


def _oauth_reason(data: dict, raw: bytes) -> str:
    return str(data.get("error_description") or data.get("error") or raw[:200].decode("utf-8", "replace"))


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


# -- tokens --------------------------------------------------------------------


class TokenStore:
    """What ``SwiggySession`` needs from storage: load / save / delete."""

    def load(self) -> dict: ...
    def save(self, payload: dict) -> None: ...
    def delete(self) -> None: ...


@dataclass
class SwiggySession:
    """A bearer token that refreshes itself, saved after every rotation."""

    store: TokenStore
    transport: Transport = urllib_transport
    clock: Callable[[], float] = time.time
    #: Rotate a token older than this even while it is valid. Swiggy's refresh
    #: token does not outlive the access token by much (one was dead six days after
    #: a sign-in whose access token lasted five),
    #: so waiting for expiry to refresh can mean refreshing with a dead token.
    max_age: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def token(self, force_refresh: bool = False) -> str:
        with self._lock:
            payload = self.store.load()
            # Refresh a minute early: a token that dies mid-call is a failed call.
            expired = force_refresh or payload.get("expires_at", 0) - 60 <= self.clock()
            aged = self.max_age is not None and payload.get("issued_at", 0) + self.max_age <= self.clock()
            if expired or aged:
                if not payload.get("refresh_token"):
                    raise SwiggyAuthError("Swiggy sign-in expired. Connect Swiggy again.")
                try:
                    payload = refresh_tokens(payload["client_id"], payload["refresh_token"], self.transport, self.clock())
                except SwiggyAuthError:
                    raise
                except SwiggyError:
                    if expired:
                        raise
                    return payload["access_token"]  # an early rotation can wait for the next call
                self.store.save(payload)
            return payload["access_token"]


# -- MCP client ----------------------------------------------------------------


@dataclass
class McpClient:
    """Just enough of MCP streamable HTTP to list and call tools.

    initialize -> notifications/initialized -> tools/*, carrying the server's
    ``Mcp-Session-Id``. A 401 refreshes the token once and retries; a 404 on a
    stale session starts a new one.
    """

    url: str
    session: SwiggySession
    transport: Transport = urllib_transport
    _mcp_session: str | None = field(default=None, repr=False)
    _ids: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def list_tools(self) -> list[dict]:
        return self._request("tools/list", {}).get("tools", [])

    def call(self, tool: str, arguments: dict | None = None) -> dict:
        result = self._request("tools/call", {"name": tool, "arguments": arguments or {}})
        envelope = _envelope(result)
        if result.get("isError") or envelope.get("success") is False:
            error = envelope.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else error
            raise SwiggyError("Swiggy %s failed: %s" % (tool, message or envelope.get("message") or "unknown error"))
        data = envelope.get("data")
        return data if isinstance(data, dict) else {"value": data}

    # -- protocol ------------------------------------------------------------

    def _request(self, method: str, params: dict) -> dict:
        with self._lock:
            if self._mcp_session is None:
                self._initialize()
            try:
                return self._rpc(method, params)
            except _StaleSession:
                self._mcp_session = None
                self._initialize()
                return self._rpc(method, params)

    def _initialize(self) -> None:
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tripwire", "version": "1.0"},
        }, capture_session=True)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _rpc(self, method: str, params: dict, capture_session: bool = False) -> dict:
        self._ids += 1
        message_id = self._ids
        status, headers, raw = self._post({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        if status == 404 and self._mcp_session and not capture_session:
            raise _StaleSession()
        if status >= 400:
            raise SwiggyError("Swiggy answered %s to %s: %s" % (status, method, raw[:200].decode("utf-8", "replace")))
        if capture_session and headers.get("mcp-session-id"):
            self._mcp_session = headers["mcp-session-id"]
        reply = _find_reply(raw, headers.get("content-type", ""), message_id)
        if "error" in reply:
            raise SwiggyError("Swiggy %s error: %s" % (method, (reply["error"] or {}).get("message", reply["error"])))
        return reply.get("result") or {}

    def _post(self, message: dict) -> tuple[int, dict, bytes]:
        body = json.dumps(message).encode("utf-8")
        for attempt in (0, 1):
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer %s" % self.session.token(force_refresh=attempt == 1),
                "MCP-Protocol-Version": PROTOCOL_VERSION,
            }
            if self._mcp_session:
                headers["Mcp-Session-Id"] = self._mcp_session
            status, response_headers, raw = self.transport("POST", self.url, headers, body)
            if status != 401:
                return status, response_headers, raw
        raise SwiggyAuthError("Swiggy sign-in expired. Connect Swiggy again.")


class _StaleSession(Exception):
    pass


def _find_reply(raw: bytes, content_type: str, message_id: int) -> dict:
    """The JSON-RPC response for ``message_id``, from a JSON body or an SSE stream."""
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        for block in text.split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
            if not data:
                continue
            try:
                message = json.loads(data)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == message_id:
                return message
        raise SwiggyError("Swiggy sent no answer")
    try:
        message = json.loads(text or "{}")
    except ValueError:
        raise SwiggyError("Swiggy sent an unreadable answer") from None
    if isinstance(message, list):
        message = next((m for m in message if isinstance(m, dict) and m.get("id") == message_id), {})
    return message if isinstance(message, dict) else {}


def _envelope(result: dict) -> dict:
    """Swiggy's {success, data} envelope, from structuredContent or the text block."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and ("success" in structured or "data" in structured):
        return structured
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                parsed = json.loads(block.get("text") or "")
            except ValueError:
                return {"success": not result.get("isError"), "data": {"text": block.get("text")}}
            if isinstance(parsed, dict):
                return parsed if ("success" in parsed or "data" in parsed) else {"success": True, "data": parsed}
    return {"success": not result.get("isError"), "data": structured if isinstance(structured, dict) else {}}


# -- the two features ------------------------------------------------------------


@dataclass
class Swiggy:
    """The Swiggy tools Tripwire uses, with the arguments the docs specify."""

    session: SwiggySession
    transport: Transport = urllib_transport
    food: McpClient = field(init=False)
    dineout: McpClient = field(init=False)

    def __post_init__(self) -> None:
        self.food = McpClient(SERVERS["food"], self.session, self.transport)
        self.dineout = McpClient(SERVERS["dineout"], self.session, self.transport)

    # Food -------------------------------------------------------------------

    def active_orders(self) -> list[dict]:
        """In-progress delivery orders: id, title, status, ETA text."""
        return list(self.food.call("track_food_order").get("orders") or [])

    def geocode(self, address: str, postal_code: str) -> dict:
        data = self.food.call("recommend_address", {"address": address, "postalCode": postal_code})
        if data.get("latitude") is None or data.get("longitude") is None:
            raise SwiggyError("Swiggy could not place %r (PIN %s)" % (address, postal_code))
        return {"latitude": float(data["latitude"]), "longitude": float(data["longitude"]),
                "address": data.get("formattedAddress") or address}

    # Dineout ----------------------------------------------------------------

    def search_restaurants(self, query: str, latitude: float, longitude: float, limit: int = 5) -> list[dict]:
        data = self.dineout.call("search_restaurants_dineout", {
            "query": query, "latitude": latitude, "longitude": longitude, "limit": limit,
        })
        return list(data.get("restaurants") or [])

    def slots(self, restaurant_id: str, day: str, latitude: float, longitude: float) -> list[dict]:
        data = self.dineout.call("get_available_slots", {
            "restaurantId": restaurant_id, "date": day, "latitude": latitude, "longitude": longitude,
        })
        return list(data.get("slots") or [])

    def book_table(self, *, restaurant_id: str, slot_id: int, item_id: str, reservation_time: int,
                   guests: int, latitude: float, longitude: float) -> dict:
        """Free reservations only: no paymentMethod beyond Cash, no cart."""
        return self.dineout.call("book_table", {
            "restaurantId": restaurant_id, "slotId": slot_id, "itemId": item_id,
            "reservationTime": reservation_time, "guestCount": guests,
            "latitude": latitude, "longitude": longitude, "paymentMethod": "Cash",
        })

    def booking_status(self, order_id: str) -> dict:
        return self.dineout.call("get_booking_status", {"orderId": order_id})


def free_deal(slot: dict) -> dict | None:
    """The first free deal on a slot. A deal with a booking price is a paid prebook,
    whatever its title says (Swiggy's docs are explicit about that)."""
    for deal in slot.get("deals") or []:
        price = deal.get("bookingPrice") or 0
        try:
            price = float(price)
        except (TypeError, ValueError):
            price = 1.0
        if deal.get("isFree") is not False and price <= 0 and not deal.get("soldOut"):
            return deal
    return None
