"""Domain entities — the class diagram, in code.

Corrections applied against the StarUML export (see docs/diagram-corrections.md):
  * ``booking_ref`` and ``source`` added to BookingEvent: reconciliation matching
    (FR3) and idempotency (NFR2) are both unreliable without a vendor reference.
  * TrainBooking / HotelBooking added — SRS 1.4 names four booking types.
  * RideBooking / RestaurantBooking added for the app: a scheduled cab (Uber
    Reserve, Ola) and a table reservation (Swiggy Dineout, Zomato) are the two
    non-travel bookings that genuinely belong on a calendar. Ordinary food or
    ride *orders* do not -- they are over before a calendar could help.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum


class BookingType(str, Enum):
    FLIGHT = "flight"
    TRAIN = "train"
    HOTEL = "hotel"
    APPOINTMENT = "appointment"
    RIDE = "ride"
    RESTAURANT = "restaurant"


class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"


class Source(str, Enum):
    VOICE = "voice"
    EMAIL = "email"


@dataclass(frozen=True)
class Traveller:
    user_id: str
    name: str
    email: str


@dataclass(frozen=True)
class Alarm:
    """A reminder attached to a CalendarEvent (0..1 per event)."""

    alarm_id: str
    trigger_time: datetime
    is_active: bool = True

    def cancel(self) -> "Alarm":
        return replace(self, is_active=False)


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    title: str
    start_time: datetime
    end_time: datetime | None = None
    booking_ref: str | None = None
    booking_type: BookingType | None = None
    location: str | None = None
    alarm: Alarm | None = None

    def with_alarm_offset(self, minutes: int, alarm_id: str) -> "CalendarEvent":
        return replace(
            self,
            alarm=Alarm(alarm_id, self.start_time - timedelta(minutes=minutes)),
        )


@dataclass(frozen=True)
class BookingEvent:
    """A booking as asserted by one source at one point in time.

    Not a stored aggregate: each voice command or email produces one of these,
    and the ReconciliationEngine decides what it means for the calendar.
    """

    booking_id: str
    booking_type: BookingType
    date_time: datetime
    status: BookingStatus
    source: Source
    title: str
    booking_ref: str | None = None
    end_time: datetime | None = None
    location: str | None = None
    destination: str | None = None
    alarm_offset_min: int = 60
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FlightBooking(BookingEvent):
    flight_number: str | None = None
    departure_airport: str | None = None
    arrival_airport: str | None = None


@dataclass(frozen=True)
class TrainBooking(BookingEvent):
    train_number: str | None = None
    departure_station: str | None = None
    arrival_station: str | None = None


@dataclass(frozen=True)
class HotelBooking(BookingEvent):
    hotel_name: str | None = None
    nights: int | None = None


@dataclass(frozen=True)
class AppointmentBooking(BookingEvent):
    provider: str | None = None


@dataclass(frozen=True)
class RideBooking(BookingEvent):
    pickup: str | None = None
    dropoff: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class RestaurantBooking(BookingEvent):
    venue: str | None = None
    party_size: int | None = None


#: One registry, so the agent, the scenario runner and the app cannot disagree
#: about which subclass a booking type builds.
BOOKING_CLASSES: dict[BookingType, type[BookingEvent]] = {
    BookingType.FLIGHT: FlightBooking,
    BookingType.TRAIN: TrainBooking,
    BookingType.HOTEL: HotelBooking,
    BookingType.APPOINTMENT: AppointmentBooking,
    BookingType.RIDE: RideBooking,
    BookingType.RESTAURANT: RestaurantBooking,
}
