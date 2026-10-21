"""Demo mode: a seeded calendar and an inbox of realistic booking mail.

The fixture files under data/emails/ carry fixed August 2026 dates, which is
right for tests (deterministic) and wrong for a demo (everything is in the past
by the time anyone opens it). So the demo builds its mail here, from the same
shapes, dated relative to today.

Each email exists to show one behaviour on the Today screen:
  * IndiGo round trip (schema.org JSON-LD, DKIM verified) -> two flights added
  * Uber Reserve (.ics attachment)                         -> a ride added
  * Swiggy Dineout (microdata)                             -> a table added
  * IRCTC (vendor profile, ten-digit PNR)                  -> a train added
  * Air India cancellation (prose)                         -> an approval card
  * forged IndiGo "reschedule" (DMARC fail)                -> an approval card
                                                              with a warning
  * fare sale                                              -> ignored
  * itinerary with no date                                 -> quarantined

Everything is marked SYNTHETIC in the message itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from packages.core.models import BookingType, CalendarEvent
from packages.providers.eml import parse_eml
from packages.providers.fake_calendar import FakeCalendar
from packages.providers.fake_inbox import FakeInbox
from packages.providers.mail import RawEmail

VERIFIED_INDIGO = (
    "mx.google.com; dkim=pass header.i=@goindigo.in; spf=pass smtp.mailfrom=bounce@goindigo.in;"
    " dmarc=pass (p=REJECT) header.from=goindigo.in"
)
FORGED_INDIGO = "mx.google.com; dkim=none; spf=fail smtp.mailfrom=goindigo.in; dmarc=fail (p=REJECT) header.from=goindigo.in"


def _at(base: datetime, days: int, hour: int, minute: int = 0) -> datetime:
    return (base + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def demo_calendar(now: datetime) -> FakeCalendar:
    calendar = FakeCalendar()
    seeds = [
        CalendarEvent("E1", "Flight AI440 BLR to MAA", _at(now, 3, 9, 15), booking_ref="AI440-XY12",
                      booking_type=BookingType.FLIGHT, location="Kempegowda International"),
        CalendarEvent("E2", "Dentist checkup Dr Rao", _at(now, 4, 17, 30), booking_type=BookingType.APPOINTMENT,
                      location="Adyar Dental"),
        CalendarEvent("E3", "Hotel Taj Coromandel", _at(now, 6, 14, 0), booking_ref="NH7123456789",
                      booking_type=BookingType.HOTEL, location="Nungambakkam, Chennai"),
    ]
    for event in seeds:
        calendar.seed(event.with_alarm_offset(60, "AL-%s" % event.event_id))
    return calendar


def demo_emails(now: datetime) -> list[RawEmail]:
    d = lambda days, h, m=0: _at(now, days, h, m)  # noqa: E731 -- local shorthand
    out_dep, out_arr = d(9, 6, 0), d(9, 8, 50)
    in_dep, in_arr = d(12, 19, 30), d(12, 22, 15)
    ride = d(2, 6, 15)
    table = d(5, 20, 0)
    train = d(7, 22, 40)

    raw = [
        _mime(
            "IndiGo <noreply@customer.goindigo.in>", "Your IndiGo itinerary - PNR QX7K2M", "demo-indigo-rt",
            text="SYNTHETIC DEMO EMAIL. Your booking is confirmed. PNR: QX7K2M",
            html=_flight_ld([
                ("QX7K2M", "211", "MAA", "Chennai International", "DEL", "Indira Gandhi International", out_dep, out_arr),
                ("QX7K2M-R", "212", "DEL", "Indira Gandhi International", "MAA", "Chennai International", in_dep, in_arr),
            ]),
            auth=VERIFIED_INDIGO,
        ),
        _mime(
            "Uber <noreply@uber.com>", "Your Uber Reserve ride is scheduled", "demo-uber",
            text="SYNTHETIC DEMO EMAIL. Your ride is confirmed. Your driver will arrive at the pickup time.",
            ics=(
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:reserve-8841@uber.example\r\n"
                "DTSTART;TZID=Asia/Kolkata:%s\r\nDTEND;TZID=Asia/Kolkata:%s\r\n"
                "SUMMARY:Uber Reserve pickup to Kempegowda Airport\r\nLOCATION:Indiranagar\\, Bengaluru\r\n"
                "STATUS:CONFIRMED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
            ) % (ride.strftime("%Y%m%dT%H%M%S"), (ride + timedelta(minutes=50)).strftime("%Y%m%dT%H%M%S")),
        ),
        _mime(
            "Swiggy Dineout <no-reply@swiggy.in>", "Table reserved at Toit", "demo-swiggy",
            html=(
                '<html><body><p>SYNTHETIC DEMO EMAIL.</p>'
                '<div itemscope itemtype="http://schema.org/FoodEstablishmentReservation">'
                '<meta itemprop="reservationNumber" content="DO-55120"/>'
                '<link itemprop="reservationStatus" href="http://schema.org/ReservationConfirmed"/>'
                '<meta itemprop="startTime" content="%s+05:30"/><meta itemprop="partySize" content="4"/>'
                '<div itemprop="reservationFor" itemscope itemtype="http://schema.org/FoodEstablishment">'
                '<span itemprop="name">Toit</span></div></div></body></html>'
            ) % table.strftime("%Y-%m-%dT%H:%M:%S"),
        ),
        _mime(
            "IRCTC <ticketadmin@irctc.co.in>", "Booking Confirmation on IRCTC, Train: 12658", "demo-irctc",
            text=(
                "SYNTHETIC DEMO EMAIL.\nYour e-ticket is confirmed.\nPNR No. : 4521873390\n"
                "Train: 12658 Chennai Mail - Chennai Central to Bengaluru City\nDeparture: %s\n"
            ) % train.strftime("%Y-%m-%d %H:%M"),
            auth="mx.google.com; dkim=pass header.d=irctc.co.in; dmarc=pass header.from=irctc.co.in",
        ),
        _mime(
            "Air India <bookings@airindia.com>", "Cancellation: AI440", "demo-ai-cancel",
            text=(
                "SYNTHETIC DEMO EMAIL.\nYour flight has been cancelled.\nBooking reference: AI440-XY12\n"
                "Flight: AI440 Bengaluru (BLR) to Chennai (MAA)\nDeparture: %s\n"
            ) % d(3, 9, 15).strftime("%Y-%m-%d %H:%M"),
        ),
        _mime(
            "IndiGo <noreply@goindigo.in>", "Schedule change for PNR QX7K2M", "demo-indigo-forged",
            text="SYNTHETIC DEMO EMAIL. A forged schedule change, to show the sender check.",
            html=_flight_ld([
                ("QX7K2M", "211", "MAA", "Chennai International", "DEL", "Indira Gandhi International",
                 out_dep - timedelta(hours=4), out_arr - timedelta(hours=4)),
            ]),
            auth=FORGED_INDIGO,
        ),
        _mime(
            "Deals <deals@travel-offers.example>", "Fare sale - book now and save 30%", "demo-promo",
            text="SYNTHETIC DEMO EMAIL. Limited-time fare sale! Book now and save 30%. Unsubscribe here.",
        ),
        _mime(
            "Bookings <bookings@travelagent.example>", "Your itinerary is attached", "demo-unparseable",
            text="SYNTHETIC DEMO EMAIL.\nYour booking is confirmed. Please see the attached PDF for timings.",
        ),
    ]
    return [parse_eml(message) for message in raw]


def demo_inbox(now: datetime) -> FakeInbox:
    inbox = FakeInbox()
    for email in demo_emails(now):
        inbox.deliver(email)
    return inbox


# -- builders ----------------------------------------------------------------


def _flight_ld(legs: list[tuple]) -> str:
    import json

    items = []
    for ref, number, origin, origin_name, dest, dest_name, dep, arr in legs:
        items.append({
            "@context": "http://schema.org",
            "@type": "FlightReservation",
            "reservationNumber": ref,
            "reservationStatus": "http://schema.org/ReservationConfirmed",
            "reservationFor": {
                "@type": "Flight",
                "flightNumber": number,
                "airline": {"@type": "Airline", "name": "IndiGo", "iataCode": "6E"},
                "departureAirport": {"@type": "Airport", "name": origin_name, "iataCode": origin},
                "departureTime": dep.strftime("%Y-%m-%dT%H:%M:%S") + "+05:30",
                "arrivalAirport": {"@type": "Airport", "name": dest_name, "iataCode": dest},
                "arrivalTime": arr.strftime("%Y-%m-%dT%H:%M:%S") + "+05:30",
            },
        })
    return (
        '<html><head><script type="application/ld+json">%s</script></head>'
        "<body><p>SYNTHETIC DEMO EMAIL.</p></body></html>" % json.dumps(items)
    )


def _mime(sender: str, subject: str, msg_id: str, text: str = "", html: str = "", ics: str = "",
          auth: str = "") -> str:
    headers = [
        "From: %s" % sender,
        "To: you@example.com",
        "Subject: %s" % subject,
        "Message-ID: <%s@demo.booking-sync>" % msg_id,
        "MIME-Version: 1.0",
    ]
    if auth:
        headers.append("Authentication-Results: %s" % auth)
    parts = []
    if text:
        parts.append("Content-Type: text/plain; charset=utf-8\r\n\r\n%s" % text)
    if html:
        parts.append("Content-Type: text/html; charset=utf-8\r\n\r\n%s" % html)
    if ics:
        parts.append(
            'Content-Type: text/calendar; charset=utf-8; method=REQUEST\r\n'
            'Content-Disposition: attachment; filename="invite.ics"\r\n\r\n%s' % ics
        )
    if len(parts) == 1:
        return "\r\n".join(headers) + "\r\n" + parts[0] + "\r\n"
    body = "".join("--demo\r\n%s\r\n" % part for part in parts) + "--demo--\r\n"
    return "\r\n".join(headers) + '\r\nContent-Type: multipart/mixed; boundary="demo"\r\n\r\n' + body
