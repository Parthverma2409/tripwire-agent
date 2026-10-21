"""Swiggy through its MCP servers: the protocol, the auth, and the two features.

A fake transport plays Swiggy: OAuth endpoints plus MCP streamable HTTP, with
tool payloads shaped like the documented ones (mcp.swiggy.com/builders/docs).
Nothing here reaches the network.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime

import pytest

from packages.app.config import Settings
from packages.app.state import AppState
from packages.app.swiggy import SwiggyAuth, SwiggyDesk, SwiggyFlowError, TableRequest, parse_table_request
from packages.core.models import BookingStatus
from packages.providers.swiggy import (
    McpClient,
    Swiggy,
    SwiggyAuthError,
    SwiggyError,
    SwiggySession,
    exchange_code,
    free_deal,
    pkce_pair,
    register_client,
)

NOW = datetime(2026, 10, 21, 18, 0)
EIGHT_PM = int(datetime(2026, 10, 22, 20, 0).timestamp())


class MemoryStore:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {}
        self.saves = 0

    def load(self) -> dict:
        return dict(self.payload)

    def save(self, payload: dict) -> None:
        self.payload = dict(payload)
        self.saves += 1

    def delete(self) -> None:
        self.payload = {}


class FakeSwiggy:
    """OAuth + MCP, answering like Swiggy. Tool results are a JSON text block."""

    def __init__(self, sse: bool = False):
        self.sse = sse
        self.calls: list[tuple[str, str, dict]] = []
        self.tokens = {"good-token"}
        #: False plays a revoked sign-in: no bearer token is accepted.
        self.accept = True
        self.orders: list[dict] = []
        self.booking_status = {"status": "CONFIRMED"}
        self.slots = [
            {"slotId": 11, "reservationTime": EIGHT_PM - 3600, "displayTime": "7:00 PM",
             "deals": [{"itemId": "R1-T1", "isFree": True, "bookingPrice": 0}]},
            {"slotId": 12, "reservationTime": EIGHT_PM, "displayTime": "8:00 PM",
             "deals": [{"itemId": "R1-T2", "isFree": False, "bookingPrice": 500}, {"itemId": "R1-T3", "isFree": True}]},
        ]
        self.session_id = "sess-1"

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None):
        if url.endswith("/auth/register"):
            return 201, {}, json.dumps({"client_id": "cid-1", **json.loads(body)}).encode()
        if url.endswith("/auth/token"):
            text = body.decode()
            data = json.loads(text) if text.startswith("{") else dict(urllib.parse.parse_qsl(text))
            self.calls.append(("token", data.get("grant_type"), data))
            if data.get("code") == "bad" or data.get("refresh_token") == "dead":
                return 400, {}, b'{"error":"invalid_grant"}'
            self.tokens.add("fresh-token")
            return 200, {}, json.dumps({"access_token": "fresh-token", "refresh_token": "r2", "expires_in": 432000}).encode()
        auth = headers.get("Authorization", "")
        if not self.accept or auth.removeprefix("Bearer ") not in self.tokens:
            return 401, {}, b'{"error":"invalid_token"}'
        message = json.loads(body)
        self.calls.append(("rpc", message.get("method"), message.get("params") or {}))
        if "id" not in message:
            return 202, {}, b""
        if message["method"] == "initialize":
            return self._reply(message["id"], {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                               {"mcp-session-id": self.session_id})
        if headers.get("Mcp-Session-Id") != self.session_id:
            return 404, {}, b"unknown session"
        if message["method"] == "tools/list":
            return self._reply(message["id"], {"tools": [{"name": "track_food_order"}, {"name": "book_table"}]})
        name, args = message["params"]["name"], message["params"]["arguments"]
        return self._reply(message["id"], {"content": [{"type": "text", "text": json.dumps(self._tool(name, args))}]})

    def _tool(self, name: str, args: dict) -> dict:
        if name == "track_food_order":
            return {"success": True, "data": {"orders": self.orders}}
        if name == "recommend_address":
            return {"success": True, "data": {"latitude": 12.97, "longitude": 77.64, "formattedAddress": "Indiranagar"}}
        if name == "search_restaurants_dineout":
            return {"success": True, "data": {"restaurants": [
                {"id": "R9", "name": "Toit Lite", "locality": "Koramangala"},
                {"id": "R1", "name": "Toit", "locality": "Indiranagar"},
            ]}}
        if name == "get_available_slots":
            return {"success": True, "data": {"slots": self.slots}}
        if name == "book_table":
            return {"success": True, "data": {"orderId": "900123", "status": "CONFIRMED", "isDineout": True}}
        if name == "get_booking_status":
            return {"success": True, "data": self.booking_status}
        return {"success": False, "error": {"message": "unknown tool %s" % name}}

    def _reply(self, message_id: int, result: dict, extra_headers: dict | None = None):
        payload = {"jsonrpc": "2.0", "id": message_id, "result": result}
        if self.sse:
            body = ("event: message\ndata: %s\n\n" % json.dumps(payload)).encode()
            return 200, {"content-type": "text/event-stream", **(extra_headers or {})}, body
        return 200, {"content-type": "application/json", **(extra_headers or {})}, json.dumps(payload).encode()


def _client(fake: FakeSwiggy, token: str = "good-token", expires_at: float = 9e12) -> Swiggy:
    store = MemoryStore({"client_id": "cid-1", "access_token": token, "refresh_token": "r1", "expires_at": expires_at})
    return Swiggy(SwiggySession(store, fake, clock=lambda: 1_000.0), fake)


# -- protocol --------------------------------------------------------------------


def test_pkce_verifier_and_challenge_follow_s256() -> None:
    import base64
    import hashlib

    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_registration_and_code_exchange() -> None:
    fake = FakeSwiggy()
    assert register_client("http://127.0.0.1:8765/auth/swiggy/callback", fake) == "cid-1"
    payload = exchange_code("cid-1", "good", "http://127.0.0.1:8765/auth/swiggy/callback", "v" * 43, fake, now=100.0)
    assert payload["access_token"] == "fresh-token" and payload["expires_at"] == 100.0 + 432000
    with pytest.raises(SwiggyAuthError):
        exchange_code("cid-1", "bad", "x", "v" * 43, fake)


@pytest.mark.parametrize("sse", [False, True])
def test_tools_are_called_over_an_initialised_session(sse: bool) -> None:
    fake = FakeSwiggy(sse=sse)
    fake.orders = [{"orderId": "o1", "title": "Meghana Foods", "subtitle": "", "orderStatus": "Preparing"}]
    assert _client(fake).active_orders()[0]["orderStatus"] == "Preparing"
    methods = [m for kind, m, _ in fake.calls if kind == "rpc"]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]


def test_an_expired_token_is_refreshed_and_the_rotation_saved() -> None:
    fake = FakeSwiggy()
    swiggy = _client(fake, token="old", expires_at=0)
    swiggy.active_orders()
    assert ("token", "refresh_token") in [(k, m) for k, m, _ in fake.calls]
    assert swiggy.session.store.load()["refresh_token"] == "r2"


def test_a_401_mid_session_refreshes_once_then_gives_up() -> None:
    fake = FakeSwiggy()
    swiggy = _client(fake, token="revoked")  # not expired locally, but Swiggy says no
    swiggy.active_orders()  # refreshed and retried
    fake.accept = False
    with pytest.raises(SwiggyAuthError):
        _client(fake, token="revoked").active_orders()
    refreshes = [c for c in fake.calls if c[:2] == ("token", "refresh_token")]
    assert len(refreshes) == 2  # one per client: retried once, not in a loop


def test_a_stale_mcp_session_is_reopened() -> None:
    fake = FakeSwiggy()
    swiggy = _client(fake)
    swiggy.active_orders()
    fake.session_id = "sess-2"  # Swiggy restarted
    swiggy.active_orders()
    assert [m for k, m, _ in fake.calls if k == "rpc"].count("initialize") == 2


def test_a_failed_tool_envelope_raises_with_swiggys_message() -> None:
    fake = FakeSwiggy()
    with pytest.raises(SwiggyError, match="unknown tool"):
        McpClient("https://mcp.swiggy.com/food", _client(fake).session, fake).call("nope")


def test_paid_deals_are_never_treated_as_free() -> None:
    assert free_deal({"deals": [{"itemId": "a", "isFree": True, "bookingPrice": 250}]}) is None
    assert free_deal({"deals": [{"itemId": "a", "isFree": False}]}) is None
    assert free_deal({"deals": [{"itemId": "b", "isFree": True, "soldOut": True}]}) is None
    assert free_deal({"deals": [{"itemId": "c"}]})["itemId"] == "c"


# -- commands --------------------------------------------------------------------


def test_table_requests_need_swiggy_named() -> None:
    assert parse_table_request("book a table at Toit tomorrow at 8pm", NOW) is None
    request = parse_table_request("book a table at Toit Indiranagar for 4 tomorrow at 8pm on Swiggy", NOW)
    assert isinstance(request, TableRequest)
    assert (request.venue, request.guests, request.when) == ("Toit Indiranagar", 4, datetime(2026, 10, 22, 20, 0))


def test_table_request_defaults_and_refusals() -> None:
    request = parse_table_request("reserve a table at Truffles via dineout tomorrow at 1:30 pm", NOW)
    assert isinstance(request, TableRequest) and request.guests == 2 and request.venue == "Truffles"
    assert "What time" in parse_table_request("book a table at Toit tomorrow on swiggy", NOW)
    assert "Which restaurant" in parse_table_request("book a swiggy table tomorrow at 8pm", NOW)


# -- the desk ----------------------------------------------------------------------


def _desk(fake: FakeSwiggy, area: str = "Indiranagar, Bangalore", pin: str = "560038") -> SwiggyDesk:
    settings = Settings(dining_area=area, dining_pin=pin)
    return SwiggyDesk(_client(fake), AppState(":memory:"), settings)


def test_a_proposal_picks_the_best_name_and_the_closest_free_slot() -> None:
    fake = FakeSwiggy()
    desk = _desk(fake)
    proposal = desk.propose(TableRequest("Toit", 4, datetime(2026, 10, 22, 20, 0)), NOW, "app")
    assert proposal.restaurant["id"] == "R1"
    assert proposal.slot["slotId"] == 12 and proposal.deal["itemId"] == "R1-T3"  # the free deal, not the paid one
    assert "Book a table for 4 at Toit (Indiranagar)" in proposal.prompt
    assert not [c for c in fake.calls if c[0] == "rpc" and c[2].get("name") == "book_table"]


def test_no_free_slot_near_the_time_is_a_clear_refusal() -> None:
    fake = FakeSwiggy()
    fake.slots = [{"slotId": 1, "reservationTime": EIGHT_PM, "deals": [{"itemId": "x", "isFree": False, "bookingPrice": 900}]}]
    with pytest.raises(SwiggyFlowError, match="no free table"):
        _desk(fake).propose(TableRequest("Toit", 2, datetime(2026, 10, 22, 20, 0)), NOW, "app")


def test_tables_need_a_dining_area_first() -> None:
    with pytest.raises(SwiggyFlowError, match="dining area"):
        _desk(FakeSwiggy(), area="").propose(TableRequest("Toit", 2, datetime(2026, 10, 22, 20, 0)), NOW, "app")


def test_order_updates_report_changes_once() -> None:
    fake = FakeSwiggy()
    desk = _desk(fake)
    fake.orders = [{"orderId": "o1", "title": "Meghana Foods", "orderStatus": "Preparing", "etaText": "25 mins"}]
    assert desk.order_updates() == ["Swiggy: Meghana Foods -- Preparing (25 mins)"]
    assert desk.order_updates() == []
    fake.orders[0]["orderStatus"] = "Out for delivery"
    assert desk.order_updates() == ["Swiggy: Meghana Foods -- Out for delivery (25 mins)"]
    fake.orders = []
    assert desk.order_updates() == ["Swiggy: Meghana Foods is no longer in progress."]


def test_a_restaurant_cancellation_becomes_a_cancelled_booking() -> None:
    fake = FakeSwiggy()
    desk = _desk(fake)
    proposal = desk.propose(TableRequest("Toit", 2, datetime(2026, 10, 22, 20, 0)), NOW, "app")
    booking = desk.book(proposal)
    assert booking.booking_ref == "900123" and booking.party_size == 2
    assert desk.table_changes(NOW) == []
    fake.booking_status = {"status": "CANCELLED"}
    [cancelled] = desk.table_changes(NOW)
    assert cancelled.status is BookingStatus.CANCELLED and cancelled.booking_ref == "900123"


# -- auth flow -----------------------------------------------------------------------


def test_sign_in_registers_once_and_the_state_is_single_use(tmp_path) -> None:
    fake = FakeSwiggy()
    settings = Settings(home=tmp_path)
    auth = SwiggyAuth(settings, AppState(":memory:"), transport=fake, passphrase="test-passphrase")
    url = auth.start_url()
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert query["client_id"] == "cid-1" and query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == "http://127.0.0.1:8765/auth/swiggy/callback"
    auth.start_url()
    assert sum(1 for c in fake.calls if c[0] == "token") == 0

    auth.complete("good", query["state"])
    assert auth.connected() and settings.swiggy_token_path.exists()
    assert "fresh-token" not in settings.swiggy_token_path.read_text()  # encrypted at rest
    with pytest.raises(SwiggyFlowError, match="stale"):
        auth.complete("good", query["state"])
    auth.disconnect()
    assert not auth.connected()
