"""schema.org reservation markup -- how Gmail itself puts flights on a calendar.

Airlines, OTAs, hotel chains, Uber and OpenTable embed a machine-readable copy of
the booking in the HTML of their confirmation mail, as JSON-LD or microdata,
following Google's "Email Markup" programme. Gmail's own "add to calendar" and
trip cards read exactly this. It is the most reliable signal in a booking email:
typed fields written by the vendor's booking system, not prose written for a
human that we have to guess at.

So this stage runs first. When it finds a reservation, the regexes never see
the email at all.

Mapping (schema.org type -> BookingType):
    FlightReservation           -> flight      reservationFor: Flight
    TrainReservation            -> train       reservationFor: TrainTrip
    LodgingReservation          -> hotel       reservationFor: LodgingBusiness
    TaxiReservation             -> ride        pickupTime / pickupLocation
    FoodEstablishmentReservation-> restaurant  reservationFor: FoodEstablishment
    EventReservation            -> appointment reservationFor: Event

reservationStatus:
    ReservationConfirmed -> confirmed   (a confirmed booking at a new time is a
                                         reschedule; the reconciler works that out)
    ReservationCancelled -> cancelled
    ReservationHold / ReservationPending -> not acted on: nothing is booked yet.

BusReservation and RentalCarReservation are recognised but not mapped -- there is
no booking type for them, and quietly filing a bus as a train would put the wrong
thing on someone's calendar.

Markup can be forged as easily as prose. It is trusted for *parsing*, never for
*authority*: a destructive change still goes through FR5, and the trust module
flags a sender whose DKIM/DMARC did not pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser

from packages.core.models import BookingStatus, BookingType

from .zones import to_local

_TYPE_MAP = {
    "FlightReservation": BookingType.FLIGHT,
    "TrainReservation": BookingType.TRAIN,
    "LodgingReservation": BookingType.HOTEL,
    "TaxiReservation": BookingType.RIDE,
    "FoodEstablishmentReservation": BookingType.RESTAURANT,
    "EventReservation": BookingType.APPOINTMENT,
}
UNSUPPORTED = {"BusReservation", "RentalCarReservation", "BoatReservation"}

_STATUS_MAP = {
    "ReservationConfirmed": BookingStatus.CONFIRMED,
    "ReservationCancelled": BookingStatus.CANCELLED,
}


@dataclass
class Reservation:
    """One reservation lifted out of markup, already mapped to the domain."""

    schema_type: str
    booking_type: BookingType | None
    status: BookingStatus | None
    raw_status: str
    booking_ref: str | None
    start: datetime | None
    end: datetime | None
    title: str
    location: str | None = None
    details: dict = field(default_factory=dict)

    @property
    def problem(self) -> str | None:
        if self.schema_type in UNSUPPORTED:
            return "unsupported reservation type %s" % self.schema_type
        if self.status is None:
            return "reservation is %s, not confirmed or cancelled" % (self.raw_status or "of unknown status")
        if self.start is None:
            return "reservation has no start time"
        return None


def find_reservations(html: str, zone: str | None = None) -> list[Reservation]:
    """Every reservation in the email's markup, JSON-LD first, then microdata."""
    if not html or ("schema.org" not in html and "ld+json" not in html):
        return []
    items = _json_ld_items(html) or _microdata_items(html)
    found = []
    for item in items:
        reservation = _to_reservation(item, zone)
        if reservation is not None:
            found.append(reservation)
    return found


# -- JSON-LD ----------------------------------------------------------------

_LD_BLOCK = re.compile(
    r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S
)


def _json_ld_items(html: str) -> list[dict]:
    items: list[dict] = []
    for block in _LD_BLOCK.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        items.extend(_flatten(data))
    return items


def _flatten(data) -> list[dict]:
    if isinstance(data, list):
        return [item for entry in data for item in _flatten(entry)]
    if isinstance(data, dict):
        if "@graph" in data:
            return _flatten(data["@graph"])
        return [data]
    return []


# -- microdata --------------------------------------------------------------


_VOID = {"meta", "link", "img", "br", "input", "hr", "source", "area", "base", "col", "wbr"}


class _Microdata(HTMLParser):
    """Builds nested dicts from itemscope / itemtype / itemprop attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.roots: list[dict] = []
        self._stack: list[tuple[str, dict | None]] = []  # (tag, the scope it opened, if any)
        self._text_prop: tuple[dict, str] | None = None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        prop = attrs.get("itemprop")
        parent = self._current_scope()
        if "itemscope" in attrs:
            scope: dict = {"@type": attrs.get("itemtype", "").rstrip("/").rsplit("/", 1)[-1]}
            if prop and parent is not None:
                parent[prop] = scope
            elif parent is None:
                self.roots.append(scope)
            self._stack.append((tag, scope))
            return
        self._stack.append((tag, None))
        if prop and parent is not None:
            value = attrs.get("content") or attrs.get("datetime") or attrs.get("href") or attrs.get("src")
            if value:
                parent[prop] = value
            elif tag not in _VOID:
                self._text_prop = (parent, prop)
                parent[prop] = ""
        if tag in _VOID:
            self._stack.pop()  # void elements never close

    def handle_startendtag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        # ``<meta itemprop=... />``. The base class would call handle_endtag
        # next, which -- the element never having been pushed -- would pop the
        # enclosing itemscope off the stack and orphan every later property.
        self.handle_starttag(tag, attrs_list)
        if tag not in _VOID and self._stack and self._stack[-1][0] == tag:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        self._text_prop = None
        if tag in _VOID or all(open_tag != tag for open_tag, _ in self._stack):
            return  # a stray close tag must not unwind scopes it never opened
        while self._stack:
            open_tag, _ = self._stack.pop()
            if open_tag == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._text_prop is not None:
            scope, prop = self._text_prop
            scope[prop] = (scope.get(prop, "") + data).strip()

    def _current_scope(self) -> dict | None:
        for _, scope in reversed(self._stack):
            if scope is not None:
                return scope
        return None


def _microdata_items(html: str) -> list[dict]:
    if "itemscope" not in html:
        return []
    parser = _Microdata()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 -- broken vendor HTML must not crash ingestion
        return []
    return parser.roots


# -- mapping ----------------------------------------------------------------


def _type_of(node) -> str:
    raw = node.get("@type", "") if isinstance(node, dict) else ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw).rsplit("/", 1)[-1]


def _get(node, *path: str):
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _name(node) -> str | None:
    if isinstance(node, str):
        return node.strip() or None
    if isinstance(node, dict):
        return (node.get("name") or node.get("iataCode") or "").strip() or None
    return None


def _code_or_name(node) -> str | None:
    if isinstance(node, dict):
        return (node.get("iataCode") or node.get("name") or "").strip() or None
    return _name(node)


def _status(item: dict) -> tuple[BookingStatus | None, str]:
    raw = str(item.get("reservationStatus") or "").rsplit("/", 1)[-1]
    return _STATUS_MAP.get(raw), raw


def _to_reservation(item: dict, zone: str | None) -> Reservation | None:
    schema_type = _type_of(item)
    if schema_type not in _TYPE_MAP and schema_type not in UNSUPPORTED:
        return None
    status, raw_status = _status(item)
    ref = item.get("reservationNumber") or item.get("reservationId")
    target = item.get("reservationFor") or {}
    booking_type = _TYPE_MAP.get(schema_type)

    start = end = None
    location = None
    title = ""
    details: dict = {}

    if schema_type == "FlightReservation":
        airline = _get(target, "airline", "iataCode") or ""
        number = str(target.get("flightNumber") or "")
        flight = number if number.upper().startswith(airline.upper()) else airline + number
        origin = _code_or_name(target.get("departureAirport"))
        dest = _code_or_name(target.get("arrivalAirport"))
        start = to_local(target.get("departureTime"), zone)
        end = to_local(target.get("arrivalTime"), zone)
        title = "Flight %s %s to %s" % (flight or "", origin or "?", dest or "?")
        location = _name(target.get("departureAirport"))
        details = {"flight_number": flight or None, "departure_airport": origin, "arrival_airport": dest}
    elif schema_type == "TrainReservation":
        number = target.get("trainNumber") or target.get("trainName") or ""
        origin = _name(target.get("departureStation"))
        dest = _name(target.get("arrivalStation"))
        start = to_local(target.get("departureTime"), zone)
        end = to_local(target.get("arrivalTime"), zone)
        title = "Train %s %s to %s" % (number, origin or "?", dest or "?")
        location = origin
        details = {"train_number": number or None, "departure_station": origin, "arrival_station": dest}
    elif schema_type == "LodgingReservation":
        hotel = _name(target) or "Hotel"
        start = to_local(item.get("checkinTime") or item.get("checkinDate"), zone)
        end = to_local(item.get("checkoutTime") or item.get("checkoutDate"), zone)
        title = "Hotel %s" % hotel if not hotel.lower().startswith("hotel") else hotel
        location = _address(target.get("address"))
        details = {"hotel_name": hotel}
    elif schema_type == "TaxiReservation":
        provider = _name(target) or _name(item.get("provider")) or "Cab"
        pickup = _name(item.get("pickupLocation"))
        dropoff = _name(item.get("dropoffLocation"))
        start = to_local(item.get("pickupTime"), zone)
        title = "%s pickup%s" % (provider, (" from %s" % pickup) if pickup else "")
        location = pickup
        details = {"provider": provider, "pickup": pickup, "dropoff": dropoff}
    elif schema_type == "FoodEstablishmentReservation":
        venue = _name(target) or "Restaurant"
        start = to_local(item.get("startTime") or item.get("startDate"), zone)
        party = item.get("partySize")
        title = "Table at %s" % venue
        location = _address(target.get("address")) if isinstance(target, dict) else None
        details = {"venue": venue, "party_size": _int(party)}
    elif schema_type == "EventReservation":
        start = to_local(target.get("startDate"), zone)
        end = to_local(target.get("endDate"), zone)
        title = _name(target) or "Event"
        location = _name(target.get("location"))
    else:
        title = schema_type

    return Reservation(
        schema_type=schema_type,
        booking_type=booking_type,
        status=status,
        raw_status=raw_status,
        booking_ref=str(ref).strip().upper() if ref else None,
        start=start,
        end=end,
        title=re.sub(r"\s+", " ", title).strip(),
        location=location,
        details={k: v for k, v in details.items() if v is not None},
    )


def _address(node) -> str | None:
    if isinstance(node, str):
        return node.strip() or None
    if isinstance(node, dict):
        parts = [node.get(k) for k in ("streetAddress", "addressLocality")]
        joined = ", ".join(str(p) for p in parts if p)
        return joined or None
    return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
