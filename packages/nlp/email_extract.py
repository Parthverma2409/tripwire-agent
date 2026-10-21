"""Extract structured booking data from a booking email (FR2, UC2).

Rule-based, like `baseline.py`, and for the same two reasons: it is the floor an
LLM extractor must beat, and it is the backstop when one returns unusable output.

The first job is not extraction at all -- it is deciding whether the email is a
booking notification in the first place. UC2's stated open issue is telling
marketing mail from genuine change mail, and a false positive here writes into a
real calendar. So classification is conservative: anything that smells
promotional is dropped before a single field is read.

STAGES
------
``extract_bookings`` tries the most reliable source first and stops at the first
one that yields a booking. ``stage`` on the result records which one answered,
and the agent copies it into the trajectory.

  1. ``schema.org``  reservation markup in the HTML (see structured.py). Typed
                     fields written by the vendor's booking system.
  2. ``ics``         an iCalendar attachment (see ics.py).
  3. promotional filter -- everything below reads prose, so prose that is
                     selling something is dropped here.
  4. ``vendor:<id>`` the rules below, with the sender's profile supplying the
                     booking type and reference shape (see vendors.py).
     ``rules``       the same rules for an unknown sender.

The machine-readable stages run before the promotional filter on purpose: real
booking mail carries an "unsubscribe" footer, and a reservation the vendor's
system emitted as data is not an advert however the footer reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from packages.core.models import BookingStatus, BookingType

from .html_text import html_to_text
from .ics import parse_ics
from .resolve import resolve_datetime
from .structured import find_reservations
from .vendors import Vendor, identify

#: Marketing tells on itself. Checked before any prose is read, and a hit means
#: the email is discarded outright rather than parsed and then second-guessed.
_PROMO = re.compile(
    r"(unsubscribe|save \d+%|off your next|limited[- ]time|book now and|fare sale|"
    r"deals? of the (day|week)|earn \d+ points|exclusive offer|don'?t miss out)",
    re.I,
)
#: The same, minus "unsubscribe", for a known vendor's mail. Their transactional
#: emails carry an unsubscribe footer too, and dropping a real cancellation from
#: IndiGo because of its footer would leave a dead flight on the calendar.
_PROMO_KNOWN_SENDER = re.compile(
    r"(save \d+%|off your next|limited[- ]time|book now and|fare sale|"
    r"deals? of the (day|week)|earn \d+ points|exclusive offer|don'?t miss out)",
    re.I,
)

#: Below this much plain text, the text/plain part is a "view this in your
#: browser" stub and the HTML is the real email.
_STUB_BODY_CHARS = 80

_STATUS_RULES: list[tuple[BookingStatus, re.Pattern]] = [
    (BookingStatus.CANCELLED, re.compile(r"\b(cancelled|canceled|cancellation confirmed|has been cancelled)\b", re.I)),
    (BookingStatus.RESCHEDULED, re.compile(r"\b(rescheduled|reschedule|time change|schedule change|new departure|has been moved|revised timing)\b", re.I)),
    (BookingStatus.CONFIRMED, re.compile(r"\b(confirmed|confirmation|booking is confirmed|e-?ticket|itinerary)\b", re.I)),
]

_TYPE_RULES: list[tuple[BookingType, re.Pattern]] = [
    (BookingType.FLIGHT, re.compile(r"\b(flight|airline|departure|boarding|pnr)\b", re.I)),
    (BookingType.TRAIN, re.compile(r"\b(train|rail|irctc|coach)\b", re.I)),
    (BookingType.HOTEL, re.compile(r"\b(hotel|check-?in|room|nights?)\b", re.I)),
    (BookingType.APPOINTMENT, re.compile(r"\b(appointment|clinic|consultation|doctor|dr\.?)\b", re.I)),
    # Last, so a flight email that mentions an "airport cab" stays a flight.
    (BookingType.RIDE, re.compile(r"\b(cab|taxi|ride|pickup|pick-up|driver)\b", re.I)),
    (BookingType.RESTAURANT, re.compile(r"\b(table|restaurant|dineout|dining)\b", re.I)),
]

# The label is matched case-insensitively; the reference itself is NOT. Under a
# blanket re.I the bare-6-character alternative happily matched the word "number"
# out of "Confirmation number:", and a wrong booking_ref is worse than none --
# it produces a false exact match, which is how the wrong event gets deleted.
# The bare form therefore also has to contain a digit, as real references do.
_REF = re.compile(
    r"(?i:\b(?:booking(?:\s+ref(?:erence)?)?|ref|pnr|confirmation)\b\s*(?:#|no\.?|number)?\s*:?\s*)"
    r"([A-Z0-9]{2,8}-[A-Z0-9]{2,8}|(?=[A-Z0-9]*\d)[A-Z0-9]{6})\b"
)
_ISO_DT = re.compile(r"\b(\d{4}-\d{2}-\d{2})[T ](\d{1,2}:\d{2})\b")
_DATE_AT_TIME = re.compile(r"\b(\d{1,2}\s+[A-Za-z]+(?:\s+\d{4})?)\s+(?:at\s+)?(\d{1,2}[:.]\d{2}\s*(?:am|pm)?)\b", re.I)
_TITLE = re.compile(r"^\s*(?:subject:\s*)?(.+?)\s*$", re.I)
# The vendor's own itinerary line, e.g. "Flight: AI440 Bengaluru (BLR) to Chennai (MAA)".
_ITINERARY = re.compile(r"^\s*(flight|train|hotel|appointment)\s*:\s*(.+)$", re.I | re.M)


@dataclass
class EmailExtraction:
    """What UC2 step 2 produces. `is_booking_related=False` stops the pipeline."""

    is_booking_related: bool
    status: BookingStatus | None = None
    booking_type: BookingType | None = None
    booking_ref: str | None = None
    date_time: datetime | None = None
    title: str | None = None
    reason: str | None = None
    notes: list[str] = field(default_factory=list)
    #: Which stage produced this: schema.org, ics, vendor:<id>, rules, or an LLM.
    stage: str = "rules"
    vendor_id: str | None = None
    end_time: datetime | None = None
    location: str | None = None
    #: Subtype fields (flight_number, venue, pickup ...) for the booking class.
    details: dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        """Enough to build a BookingEvent the reconciler can use."""
        return bool(self.is_booking_related and self.status and self.date_time and self.title)


def extract_email(
    subject: str,
    body: str,
    now: datetime,
    *,
    html: str = "",
    attachments: tuple = (),
    sender: str = "",
    zone: str | None = None,
) -> EmailExtraction:
    """The first booking in the email. See ``extract_bookings`` for all of them."""
    return extract_bookings(subject, body, now, html=html, attachments=attachments, sender=sender, zone=zone)[0]


def extract_bookings(
    subject: str,
    body: str,
    now: datetime,
    *,
    html: str = "",
    attachments: tuple = (),
    sender: str = "",
    zone: str | None = None,
) -> list[EmailExtraction]:
    """Every booking the email describes. Never empty.

    A round trip arrives as one email with two reservations, and dropping the
    return leg is a missed flight -- so markup and .ics stages return every
    reservation they find. The prose rules find at most one.
    """
    vendor = identify(sender) if sender else None

    structured = _from_markup(html, zone, vendor)
    if structured:
        return structured

    calendar = _from_ics(attachments, subject, body, zone, vendor)
    if calendar:
        return calendar

    text = body
    if html and (len(body.strip()) < _STUB_BODY_CHARS or body.lstrip().startswith("<")):
        # A "view in browser" stub, or a provider that handed us the HTML as the
        # body because there was no text part: read the rendered HTML instead.
        text = html_to_text(html)
    return [_from_prose(subject, text, now, vendor)]


def _from_markup(html: str, zone: str | None, vendor: Vendor | None) -> list[EmailExtraction]:
    out = []
    for reservation in find_reservations(html, zone):
        problem = reservation.problem
        out.append(
            EmailExtraction(
                is_booking_related=reservation.status is not None and problem is None,
                status=reservation.status,
                booking_type=reservation.booking_type,
                booking_ref=reservation.booking_ref,
                date_time=reservation.start,
                title=reservation.title or None,
                reason=problem,
                notes=["schema.org %s" % reservation.schema_type],
                stage="schema.org",
                vendor_id=vendor.id if vendor else None,
                end_time=reservation.end,
                location=reservation.location,
                details=reservation.details,
            )
        )
    return out


def _from_ics(
    attachments: tuple, subject: str, body: str, zone: str | None, vendor: Vendor | None
) -> list[EmailExtraction]:
    out = []
    blob = "%s\n%s" % (subject, body)
    email_ref = (vendor.find_ref(blob) if vendor else None) or _generic_ref(blob)
    for attachment in attachments:
        if getattr(attachment, "mime_type", "") != "text/calendar":
            continue
        for event in parse_ics(attachment.text(), zone):
            title = event.summary or None
            booking_type = (vendor.booking_type if vendor else None) or next(
                (t for t, pattern in _TYPE_RULES if pattern.search("%s\n%s" % (event.summary, blob))), None
            )
            extraction = EmailExtraction(
                is_booking_related=True,
                status=event.status,
                booking_type=booking_type,
                booking_ref=email_ref or (event.uid.upper() if event.uid else None),
                date_time=event.start,
                title=title,
                notes=["ics SEQUENCE=%d" % event.sequence],
                stage="ics",
                vendor_id=vendor.id if vendor else None,
                end_time=event.end,
                location=event.location,
            )
            if not extraction.is_actionable:
                extraction.reason = "unparseable: calendar attachment has no start time or title"
            out.append(extraction)
    return out


def _generic_ref(blob: str) -> str | None:
    match = _REF.search(blob)
    return match.group(1).upper() if match else None


def _from_prose(subject: str, body: str, now: datetime, vendor: Vendor | None) -> EmailExtraction:
    blob = "%s\n%s" % (subject, body)
    stage = "vendor:%s" % vendor.id if vendor else "rules"
    promo = _PROMO_KNOWN_SENDER if vendor else _PROMO

    if promo.search(blob):
        # UC2 open issue. Dropping a genuine change email is recoverable -- the
        # vendor resends, and the user can still act. Acting on a marketing email
        # is not: it raises a destructive prompt about a booking nothing changed.
        return EmailExtraction(False, reason="promotional", notes=["matched a marketing pattern"], stage=stage)

    status = next((s for s, pattern in _STATUS_RULES if pattern.search(blob)), None)
    if status is None:
        return EmailExtraction(False, reason="no booking status found", stage=stage)

    booking_type = (vendor.booking_type if vendor else None) or next(
        (t for t, pattern in _TYPE_RULES if pattern.search(blob)), None
    )

    booking_ref = (vendor.find_ref(blob) if vendor else None) or _generic_ref(blob)

    date_time, notes = _find_datetime(blob, now)

    title = _build_title(blob, booking_type, booking_ref, subject)

    extraction = EmailExtraction(
        is_booking_related=True,
        status=status,
        booking_type=booking_type,
        booking_ref=booking_ref,
        date_time=date_time,
        title=title or None,
        notes=notes,
        stage=stage,
        vendor_id=vendor.id if vendor else None,
    )
    if not extraction.is_actionable:
        # UC2 E1: flagged for manual review, calendar untouched.
        missing = [
            name
            for name, value in (("status", status), ("date_time", date_time), ("title", title))
            if not value
        ]
        extraction.reason = "unparseable: missing %s" % ", ".join(missing)
    return extraction


def _build_title(blob: str, booking_type: BookingType | None, booking_ref: str | None, subject: str) -> str | None:
    """Name the BOOKING, not the email.

    The subject line describes the notification -- "Schedule change for your
    flight AI440" -- and putting that in a calendar leaves the user with an event
    named after a piece of admin. Prefer the itinerary line the vendor already
    provides, then a type-and-reference label, and only fall back to the subject.
    """
    match = _ITINERARY.search(blob)
    if match:
        detail = match.group(2).strip(" .")
        label = match.group(1).capitalize()
        return detail if detail.lower().startswith(label.lower()) else "%s %s" % (label, detail)

    if booking_type and booking_ref:
        return "%s %s" % (booking_type.value.capitalize(), booking_ref)
    if booking_type:
        return booking_type.value.capitalize() + " booking"

    stripped = _TITLE.match(subject)
    return (stripped.group(1) if stripped else subject.strip()) or None


def _find_datetime(blob: str, now: datetime) -> tuple[datetime | None, list[str]]:
    match = _ISO_DT.search(blob)
    if match:
        resolution = resolve_datetime(match.group(1), match.group(2), now)
        if resolution.ok:
            return resolution.value, []

    match = _DATE_AT_TIME.search(blob)
    if match:
        date_text = re.sub(r"\s+\d{4}$", "", match.group(1))  # resolver infers the year
        resolution = resolve_datetime(date_text, match.group(2), now)
        if resolution.ok:
            return resolution.value, [resolution.note] if resolution.note else []

    return None, ["no resolvable date/time in the email"]
