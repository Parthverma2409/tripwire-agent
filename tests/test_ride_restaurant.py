"""The two app-only booking types: scheduled rides and table reservations.

They are covered here rather than as an eleventh scenario family because the
66-scenario suite is a frozen research benchmark: FR10 comparisons, RQ1/RQ2 and
the published numbers are all pinned to it. These tests hold the new types to the
same bar the suite holds the old ones to -- creation is free, anything destructive
waits for consent.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from packages.core.models import BOOKING_CLASSES, BookingType, CalendarEvent, RestaurantBooking, RideBooking
from packages.providers.fake_calendar import FakeCalendar
from packages.providers.mail import RawEmail
from packages.sync_agent.agent import SyncAgent

NOW = datetime(2026, 8, 18, 8, 0)


def _email(subject: str, body: str, message_id: str = "m1") -> RawEmail:
    return RawEmail(message_id=message_id, sender="noreply@example.com", subject=subject, body=body, received_at=NOW)


def test_every_booking_type_has_a_class() -> None:
    assert set(BOOKING_CLASSES) == set(BookingType)
    assert BOOKING_CLASSES[BookingType.RIDE] is RideBooking
    assert BOOKING_CLASSES[BookingType.RESTAURANT] is RestaurantBooking


def test_spoken_cab_booking_is_a_ride_not_a_flight() -> None:
    calendar = FakeCalendar()
    agent = SyncAgent(calendar)

    result = agent.handle_command("book a cab to Chennai tomorrow at 6:30 am", NOW)

    assert result.status == "success", result.message
    [event] = calendar.list_events()
    assert event.booking_type is BookingType.RIDE
    assert event.start_time == datetime(2026, 8, 19, 6, 30)


def test_spoken_table_booking_is_a_restaurant() -> None:
    calendar = FakeCalendar()
    agent = SyncAgent(calendar)

    result = agent.handle_command("reserve a table in Bangalore on 22 August at 8:00 pm", NOW)

    assert result.status == "success", result.message
    [event] = calendar.list_events()
    assert event.booking_type is BookingType.RESTAURANT


def test_ride_confirmation_email_creates_an_event() -> None:
    calendar = FakeCalendar()
    agent = SyncAgent(calendar)

    result = agent.handle_email(
        _email(
            "Your ride is confirmed",
            "Your scheduled ride is confirmed.\nBooking ref: UBR-7781\nPickup: 2026-08-21 05:45 from Home\n",
        ),
        NOW,
    )

    assert result.status == "success", result.message
    [event] = calendar.list_events()
    assert event.booking_type is BookingType.RIDE
    assert event.booking_ref == "UBR-7781"


def test_cancelled_table_waits_for_consent() -> None:
    calendar = FakeCalendar()
    calendar.seed(
        CalendarEvent(
            event_id="E1",
            title="Table at Toit",
            start_time=datetime(2026, 8, 22, 20, 0),
            booking_ref="DO-55120",
            booking_type=BookingType.RESTAURANT,
        )
    )
    agent = SyncAgent(calendar)

    prompt = agent.handle_email(
        _email(
            "Your table reservation has been cancelled",
            "Your restaurant reservation has been cancelled.\nBooking ref: DO-55120\nDate: 2026-08-22 20:00\n",
        ),
        NOW,
    )

    assert prompt.status == "awaiting_confirmation"
    assert calendar.mutations == []

    done = agent.resolve_confirmation(prompt.confirmation.request_id, True, NOW + timedelta(minutes=1))
    assert done.status == "success"
    assert calendar.list_events() == []
