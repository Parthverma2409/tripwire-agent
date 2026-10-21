"""The staged email pipeline: schema.org markup, .ics, vendor profiles, trust.

Fixtures under data/emails/fixtures/vendors/ are SYNTHETIC -- written to the
published shapes (schema.org Email Markup, RFC 5545), not captured from the
vendors named. They prove the stages work; they do not prove any one vendor's
real mail parses. `python -m packages.nlp check-email` is how that gets checked.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from packages.core.models import BookingStatus, BookingType, CalendarEvent
from packages.nlp.email_extract import extract_bookings
from packages.nlp.html_text import html_to_text
from packages.nlp.ics import parse_ics
from packages.nlp.llm_email import parse_answer
from packages.nlp.trust import FAILED, UNKNOWN, VERIFIED, assess
from packages.nlp.vendors import identify
from packages.providers.eml import MAX_ATTACHMENT_BYTES, parse_eml
from packages.providers.fake_calendar import FakeCalendar
from packages.sync_agent.agent import SyncAgent

ROOT = Path(__file__).resolve().parents[1]
VENDORS = ROOT / "data" / "emails" / "fixtures" / "vendors"
LEGACY = ROOT / "data" / "emails" / "fixtures"
NOW = datetime(2026, 8, 18, 8, 0)
ZONE = "Asia/Kolkata"


def _load(name: str, folder: Path = VENDORS):
    return parse_eml((folder / name).read_bytes())


def _extract(name: str):
    email = _load(name)
    return extract_bookings(
        email.subject, email.body, NOW, html=email.html, attachments=email.attachments, sender=email.sender, zone=ZONE
    )


# -- stages ---------------------------------------------------------------


def test_json_ld_round_trip_yields_both_legs() -> None:
    outbound, inbound = _extract("indigo_roundtrip_jsonld.eml")

    assert outbound.stage == inbound.stage == "schema.org"
    assert outbound.booking_type is BookingType.FLIGHT
    assert outbound.status is BookingStatus.CONFIRMED
    assert outbound.booking_ref == "QX7K2M"
    assert outbound.date_time == datetime(2026, 8, 25, 6, 0)
    assert outbound.end_time == datetime(2026, 8, 25, 8, 50)
    assert outbound.title == "Flight 6E211 MAA to DEL"
    assert outbound.details["flight_number"] == "6E211"
    assert inbound.date_time == datetime(2026, 8, 28, 19, 30)


def test_markup_beats_the_unsubscribe_footer() -> None:
    """Real booking mail has an unsubscribe link; markup is data, not an advert."""
    [first, _] = _extract("indigo_roundtrip_jsonld.eml")
    assert first.is_actionable


def test_microdata_restaurant_reservation() -> None:
    [table] = _extract("swiggy_dineout_microdata.eml")

    assert table.stage == "schema.org"
    assert table.booking_type is BookingType.RESTAURANT
    assert table.booking_ref == "DO-55120"
    assert table.date_time == datetime(2026, 8, 22, 20, 0)
    assert table.title == "Table at Toit"
    assert table.details == {"venue": "Toit", "party_size": 4}


def test_ics_attachment_with_tzid() -> None:
    [ride] = _extract("uber_reserve_ics.eml")

    assert ride.stage == "ics"
    assert ride.booking_type is BookingType.RIDE  # from the Uber vendor profile
    assert ride.date_time == datetime(2026, 8, 25, 4, 15)
    assert ride.location == "VIT Chennai, Vandalur-Kelambakkam Road"


def test_ics_method_cancel_in_utc_is_converted_to_local_time() -> None:
    [appointment] = _extract("practo_cancel_ics.eml")

    assert appointment.status is BookingStatus.CANCELLED
    assert appointment.date_time == datetime(2026, 8, 22, 17, 30)  # 12:00Z in IST


def test_vendor_profile_reads_a_ten_digit_pnr() -> None:
    [train] = _extract("irctc_pnr_plain.eml")

    assert train.stage == "vendor:irctc"
    assert train.booking_type is BookingType.TRAIN
    assert train.booking_ref == "4521873390"


def test_html_only_mail_is_read_through_the_html() -> None:
    [hotel] = _extract("makemytrip_hotel_html_only.eml")

    assert hotel.stage == "vendor:makemytrip"
    assert hotel.booking_type is BookingType.HOTEL
    assert hotel.booking_ref == "NH7123456789"
    assert hotel.date_time == datetime(2026, 8, 29, 14, 0)


def test_unknown_sender_promo_is_still_dropped() -> None:
    email = _load("promo_fare_sale.eml", LEGACY)
    [result] = extract_bookings(email.subject, email.body, NOW, sender=email.sender)
    assert not result.is_booking_related
    assert result.reason == "promotional"


def test_hidden_preheader_is_not_read() -> None:
    text = html_to_text('<div style="display: none">Save 20% now</div><p>Booking confirmed</p>')
    assert "Save" not in text
    assert "Booking confirmed" in text


def test_table_cells_do_not_run_together() -> None:
    text = html_to_text("<table><tr><td>Departure</td><td>09:15</td></tr></table>")
    assert "Departure09:15" not in text


# -- ics reader -----------------------------------------------------------


def test_ics_folded_lines_dates_and_nested_alarm() -> None:
    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:Hotel Taj\r\n  Coromandel\r\n"
        "DTSTART;VALUE=DATE:20260829\r\nDESCRIPTION:the stay\r\n"
        "BEGIN:VALARM\r\nDESCRIPTION:reminder\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    [event] = parse_ics(text)
    assert event.summary == "Hotel Taj Coromandel"
    assert event.start == datetime(2026, 8, 29, 0, 0)
    assert event.status is BookingStatus.CONFIRMED


# -- trust ----------------------------------------------------------------


def test_trust_verdicts() -> None:
    good = _load("indigo_roundtrip_jsonld.eml")
    forged = _load("indigo_cancel_spoofed.eml")
    unsigned = _load("uber_reserve_ics.eml")

    assert assess(good.sender, good.headers).level == VERIFIED
    assert assess(forged.sender, forged.headers).level == FAILED
    assert assess(unsigned.sender, unsigned.headers).level == UNKNOWN


@pytest.mark.parametrize(
    "sender, expected",
    [
        ("IndiGo <a@goindigo.in>", "indigo"),
        ("IndiGo <a@mail.goindigo.in>", "indigo"),
        ("IndiGo <a@goindigo.in.evil.example>", None),
        ("IndiGo <a@notgoindigo.in>", None),
    ],
)
def test_vendor_domains_match_on_label_boundaries(sender: str, expected: str | None) -> None:
    vendor = identify(sender)
    assert (vendor.id if vendor else None) == expected


def test_dkim_for_an_unrelated_domain_does_not_verify() -> None:
    headers = {"Authentication-Results": "mx.google.com; dkim=pass header.d=attacker.example; dmarc=none"}
    assert assess("IndiGo <noreply@goindigo.in>", headers).level == UNKNOWN


# -- agent ----------------------------------------------------------------


def test_round_trip_email_puts_both_flights_on_the_calendar() -> None:
    calendar = FakeCalendar()
    agent = SyncAgent(calendar, zone=ZONE)

    result = agent.handle_email(_load("indigo_roundtrip_jsonld.eml"), NOW)

    assert [r.status for r in result.all()] == ["success", "success"]
    assert [e.title for e in calendar.list_events()] == ["Flight 6E211 MAA to DEL", "Flight 6E212 DEL to MAA"]
    assert result.meta["stage"] == "schema.org"
    assert result.meta["trust"]["level"] == VERIFIED
    # One trajectory per booking, each a complete record.
    assert len({r.trajectory["trajectory_id"] for r in result.all()}) == 2


def test_forged_cancellation_is_gated_and_flagged() -> None:
    calendar = FakeCalendar()
    agent = SyncAgent(calendar, zone=ZONE)
    agent.handle_email(_load("indigo_roundtrip_jsonld.eml"), NOW)

    prompt = agent.handle_email(_load("indigo_cancel_spoofed.eml"), NOW)

    assert prompt.status == "awaiting_confirmation"
    assert "failed sender authentication" in prompt.message
    assert len(calendar.list_events()) == 2  # nothing deleted on a forged email's say-so
    reasoning = " ".join(step["text"] for step in prompt.trajectory["reasoning"])
    assert "sender failed" in reasoning


def test_verified_cancellation_carries_no_warning() -> None:
    calendar = FakeCalendar()
    calendar.seed(
        CalendarEvent(
            event_id="E1", title="Train 12658", start_time=datetime(2026, 8, 27, 22, 40),
            booking_ref="4521873390", booking_type=BookingType.TRAIN,
        )
    )
    agent = SyncAgent(calendar, zone=ZONE)
    email = _load("irctc_pnr_plain.eml")
    cancelled = parse_eml(
        (VENDORS / "irctc_pnr_plain.eml").read_text(encoding="utf-8")
        .replace("Your e-ticket is confirmed.", "Your ticket has been cancelled.")
        .replace("<synthetic-irctc-1@", "<synthetic-irctc-2@")
    )
    assert email.message_id != cancelled.message_id

    prompt = agent.handle_email(cancelled, NOW)

    assert prompt.status == "awaiting_confirmation"
    assert "Warning" not in prompt.message and "could not verify" not in prompt.message


class _StubFallback:
    name = "stub"

    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def extract(self, subject, text, now):
        self.calls += 1
        return self.answer


def test_llm_fallback_is_only_asked_about_mail_the_rules_quarantined() -> None:
    from packages.nlp.email_extract import EmailExtraction

    rescue = EmailExtraction(
        True, BookingStatus.CONFIRMED, BookingType.FLIGHT, None, datetime(2026, 8, 30, 7, 0), "Flight to Goa",
        stage="llm:stub",
    )
    fallback = _StubFallback(rescue)
    calendar = FakeCalendar()
    agent = SyncAgent(calendar, email_fallback=fallback)

    agent.handle_email(_load("indigo_roundtrip_jsonld.eml"), NOW)
    assert fallback.calls == 0  # parsed by markup; the model is never consulted

    rescued = agent.handle_email(_load("unparseable_itinerary.eml", LEGACY), NOW)
    assert fallback.calls == 1
    assert rescued.status == "success"
    assert rescued.meta["stage"] == "llm:stub"
    assert rescued.trajectory["agent"]["model"] == "email:llm:stub"


def test_a_dead_fallback_degrades_to_quarantine() -> None:
    class _Broken:
        name = "broken"

        def extract(self, subject, text, now):
            raise ConnectionError("ollama is not running")

    agent = SyncAgent(FakeCalendar(), email_fallback=_Broken())
    result = agent.handle_email(_load("unparseable_itinerary.eml", LEGACY), NOW)
    assert result.status == "failure"
    assert agent.quarantine


def test_llm_answer_validation() -> None:
    source = "Your flight is confirmed. PNR AB12CD. Departs 30 Aug 07:00."
    good = parse_answer(
        '{"status":"confirmed","type":"flight","title":"Flight to Goa","start":"2026-08-30T07:00","ref":"AB12CD"}',
        source,
        "m",
    )
    invented = parse_answer(
        '{"status":"confirmed","type":"flight","title":"Flight to Goa","start":"2026-08-30T07:00","ref":"ZZ99ZZ"}',
        source,
        "m",
    )
    bad_status = parse_answer('{"status":"maybe","type":"flight","title":"x","start":"2026-08-30T07:00"}', source, "m")

    assert good is not None and good.booking_ref == "AB12CD"
    assert invented is not None and invented.booking_ref is None
    assert bad_status is None
    assert parse_answer("not json", source, "m") is None


# -- mime -----------------------------------------------------------------


def test_eml_without_message_id_is_content_addressed() -> None:
    raw = b"From: a@b.c\r\nSubject: hi\r\n\r\nbody\r\n"
    assert parse_eml(raw).message_id == parse_eml(raw).message_id
    assert parse_eml(raw).message_id.startswith("sha256:")


def test_oversized_calendar_parts_are_dropped() -> None:
    big = "X" * (MAX_ATTACHMENT_BYTES + 10)
    raw = (
        "From: a@b.c\r\nSubject: s\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=z\r\n\r\n"
        "--z\r\nContent-Type: text/plain\r\n\r\nhello\r\n"
        "--z\r\nContent-Type: text/calendar\r\n\r\n%s\r\n--z--\r\n" % big
    )
    assert parse_eml(raw).attachments == ()


def test_agent_converts_into_the_configured_zone() -> None:
    calendar = FakeCalendar()
    SyncAgent(calendar, zone="UTC").handle_email(_load("indigo_roundtrip_jsonld.eml"), NOW)
    assert calendar.list_events()[0].start_time == datetime(2026, 8, 25, 0, 30)  # 06:00 IST


def test_confirmation_resolves_after_followups() -> None:
    calendar = FakeCalendar()
    agent = SyncAgent(calendar, zone=ZONE)
    agent.handle_email(_load("practo_cancel_ics.eml"), NOW)  # nothing to cancel yet
    assert calendar.list_events() == []
    assert agent.confirmations.pending(NOW + timedelta(minutes=1)) == []
