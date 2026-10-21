"""The app features added for real use: change notifications, booking details,
type guessing for events Tripwire did not create, the live research data, and
Swiggy tables through the HTTP API.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from packages.app.config import Settings  # noqa: E402
from packages.app.swiggy import SwiggyDesk  # noqa: E402
from packages.core.models import BookingType, CalendarEvent  # noqa: E402
from tests.test_app_api import Clock, H, _client, _telegram_settings  # noqa: E402
from tests.test_connectors import FakeTelegram  # noqa: E402
from tests.test_swiggy import FakeSwiggy  # noqa: E402
from tests.test_swiggy import _client as swiggy_client  # noqa: E402


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 21, 10, 0))


def _texts(fake: FakeTelegram) -> list[str]:
    return [p["text"] for m, p in fake.sent if m == "sendMessage" and "reply_markup" not in p]


# -- notifications -----------------------------------------------------------------


def test_every_calendar_change_is_sent_to_telegram(clock) -> None:
    fake = FakeTelegram()
    client = _client(clock, _telegram_settings(), telegram_transport=fake)
    client.post("/api/command", json={"text": "book a cab tomorrow at 7 am"}, headers=H)
    assert any(t.startswith("Added") for t in _texts(fake))


def test_a_question_alone_is_not_a_change(clock) -> None:
    fake = FakeTelegram()
    client = _client(clock, _telegram_settings(), telegram_transport=fake)
    client.post("/api/command", json={"text": "what is booked tomorrow"}, headers=H)
    assert _texts(fake) == []


def test_change_notifications_can_be_switched_off(clock) -> None:
    fake = FakeTelegram()
    settings = _telegram_settings()
    settings.notify_changes = False
    client = _client(clock, settings, telegram_transport=fake)
    client.post("/api/command", json={"text": "book a cab tomorrow at 7 am"}, headers=H)
    assert _texts(fake) == []


def test_a_change_made_from_telegram_is_not_echoed_back(clock) -> None:
    fake = FakeTelegram()
    client = _client(clock, _telegram_settings(), telegram_transport=fake)
    client.app.state.runtime.command("book a cab tomorrow at 7 am", channel="telegram")
    assert _texts(fake) == []


def test_settings_save_notifications_and_dining_area(clock) -> None:
    client = _client(clock)
    body = {"notify_changes": False, "dining_area": "Indiranagar, Bangalore", "dining_pin": "560038"}
    saved = client.post("/api/settings", json=body, headers=H).json()["settings"]
    assert (saved["notify_changes"], saved["dining_area"], saved["dining_pin"]) == (False, "Indiranagar, Bangalore", "560038")
    assert client.post("/api/settings", json={"dining_pin": "5600"}, headers=H).status_code == 422


# -- booking details -------------------------------------------------------------


def test_a_ride_has_its_details_history_and_an_uber_link(clock) -> None:
    client = _client(clock)
    client.post("/api/command", json={"text": "book a cab to Kempegowda Airport tomorrow at 5 am"}, headers=H)
    [ride] = [e for e in client.get("/api/events").json()["events"] if e["type"] == "ride" and e["start"].endswith("05:00:00")]
    detail = client.get("/api/events/%s" % ride["id"]).json()
    assert detail["event"]["id"] == ride["id"] and not detail["gone"]
    assert detail["history"][0]["type"] == "CREATE" and detail["history"][0]["channel"] == "app"
    assert detail["links"]["uber"].startswith("https://m.uber.com/ul/?action=setPickup")
    assert "Kempegowda" in detail["links"]["uber"]


def test_an_unknown_booking_is_a_404(clock) -> None:
    assert _client(clock).get("/api/events/nope").status_code == 404


def test_events_tripwire_did_not_create_get_a_guessed_type(clock) -> None:
    client = _client(clock)
    runtime = client.app.state.runtime
    runtime.calendar.seed(CalendarEvent(event_id="g1", title="Flight to Chennai (6E 971)", start_time=datetime(2026, 11, 2, 7, 20)))
    runtime.calendar.seed(CalendarEvent(event_id="g2", title="Team standup", start_time=datetime(2026, 11, 2, 9, 0)))
    runtime.calendar.seed(CalendarEvent(event_id="g3", title="Dinner", start_time=datetime(2026, 11, 2, 20, 0),
                                        booking_type=BookingType.HOTEL))
    events = {e["id"]: e for e in client.get("/api/events").json()["events"]}
    assert (events["g1"]["type"], events["g1"]["type_guessed"]) == ("flight", True)
    assert (events["g2"]["type"], events["g2"]["type_guessed"]) == (None, False)
    assert (events["g3"]["type"], events["g3"]["type_guessed"]) == ("hotel", False)  # stored type wins


# -- research view -------------------------------------------------------------


def test_the_research_dashboard_reads_the_apps_own_activity(clock) -> None:
    client = _client(clock)
    client.post("/api/command", json={"text": "book a cab tomorrow at 7 am"}, headers=H)
    body = client.get("/research/data/index.js").text
    data = json.loads(body.split("=", 1)[1].rstrip().rstrip(";"))
    assert data["mode"] == "live"
    assert data["summary"]["valid"] == 1
    assert data["trajectories"][0]["family"] == "typed or spoken command"
    assert any(e["scenario_id"] == "Tripwire" for e in data["calendar"]["events"])


# -- Swiggy tables -----------------------------------------------------------------


def _with_swiggy(clock, fake: FakeSwiggy):
    settings = Settings(dining_area="Indiranagar, Bangalore", dining_pin="560038")
    client = _client(clock, settings)
    runtime = client.app.state.runtime
    runtime.swiggy = SwiggyDesk(swiggy_client(fake), runtime.store, settings)
    return client, runtime


def _booked(fake: FakeSwiggy) -> bool:
    return any(c[0] == "rpc" and c[2].get("name") == "book_table" for c in fake.calls)


def test_a_swiggy_table_is_booked_only_after_yes(clock) -> None:
    fake = FakeSwiggy()
    client, _ = _with_swiggy(clock, fake)
    asked = client.post("/api/command", json={"text": "book a table at Toit for 4 tomorrow at 8pm on Swiggy"}, headers=H).json()
    assert asked["status"] == "awaiting_confirmation" and asked["request_id"].startswith("SW-")
    [card] = [c for c in client.get("/api/confirmations").json()["confirmations"] if c["action"] == "BOOK"]
    assert card["after"]["title"] == "Table at Toit" and card["meta"]["guests"] == 4
    assert not _booked(fake)

    done = client.post("/api/confirmations/%s" % asked["request_id"], json={"approve": True}, headers=H).json()
    assert _booked(fake)
    assert done["message"].startswith("Booked on Swiggy (order 900123).")
    [table] = [e for e in client.get("/api/events").json()["events"] if e["title"] == "Table at Toit"]
    assert table["ref"] == "900123" and table["type"] == "restaurant"
    detail = client.get("/api/events/%s" % table["id"]).json()
    assert detail["booking"]["party_size"] == 4 and detail["links"]["swiggy"]


def test_declining_a_swiggy_table_books_nothing(clock) -> None:
    fake = FakeSwiggy()
    client, _ = _with_swiggy(clock, fake)
    asked = client.post("/api/command", json={"text": "book a table at Toit tomorrow at 8pm on swiggy"}, headers=H).json()
    client.post("/api/confirmations/%s" % asked["request_id"], json={"approve": False}, headers=H)
    assert not _booked(fake)
    assert not any(e["title"] == "Table at Toit" for e in client.get("/api/events").json()["events"])
    assert client.post("/api/confirmations/%s" % asked["request_id"], json={"approve": True}, headers=H).status_code == 422


def test_a_restaurant_cancellation_asks_before_removing_the_table(clock) -> None:
    fake = FakeSwiggy()
    client, runtime = _with_swiggy(clock, fake)
    asked = client.post("/api/command", json={"text": "book a table at Toit tomorrow at 8pm on swiggy"}, headers=H).json()
    client.post("/api/confirmations/%s" % asked["request_id"], json={"approve": True}, headers=H)

    fake.booking_status = {"status": "CANCELLED"}
    runtime.swiggy_poll()
    pending = client.get("/api/confirmations").json()["confirmations"]
    assert [c["action"] for c in pending] == ["DELETE"]
    assert any(e["title"] == "Table at Toit" for e in client.get("/api/events").json()["events"])


def test_swiggy_order_updates_are_notified(clock) -> None:
    fake = FakeSwiggy()
    telegram = FakeTelegram()
    settings = _telegram_settings()
    client = _client(clock, settings, telegram_transport=telegram)
    runtime = client.app.state.runtime
    runtime.swiggy = SwiggyDesk(swiggy_client(fake), runtime.store, settings)
    fake.orders = [{"orderId": "o1", "title": "Meghana Foods", "orderStatus": "Out for delivery", "etaText": "9 mins"}]
    runtime.swiggy_poll()
    assert _texts(telegram) == ["Swiggy: Meghana Foods -- Out for delivery (9 mins)"]
    assert client.get("/api/swiggy/orders").json()["orders"][0]["eta"] == "9 mins"


def test_without_swiggy_a_table_request_says_how_to_connect(clock) -> None:
    result = _client(clock).post("/api/command", json={"text": "book a table at Toit tomorrow at 8pm on swiggy"}, headers=H).json()
    assert result["status"] == "refused" and "Connect Swiggy" in result["message"]
