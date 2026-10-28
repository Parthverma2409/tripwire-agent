"""Swiggy in the app: connecting the account, and the two things it is used for.

``SwiggyAuth`` is the OAuth 2.1 + PKCE flow (packages/providers/swiggy.py has the
protocol). Same shape as ``GoogleAuth``: a single-use, ten-minute ``state``, and
the token encrypted at rest with the key in the OS keyring.

``SwiggyDesk`` holds the behaviour:

  * **Order updates.** Each poll reads the active Food orders; a new order or a
    changed status becomes one notification. Nothing is written anywhere.
  * **Table bookings.** "book a table at Toit for 4 tomorrow at 8pm on Swiggy"
    finds the restaurant and a free slot, then *asks*. Only a yes calls
    ``book_table``; the reservation then reaches the calendar through
    ``SyncAgent.handle_booking`` like any other booking. Reservations made this
    way are re-checked each poll, and a cancellation by the restaurant goes
    through the agent's confirmation gate before the event is removed.

A proposal is never stored on disk: a restart forgets it, which is the safe
direction for something that commits the user to a table.
"""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from packages.core.models import BookingStatus, BookingType, RestaurantBooking, Source
from packages.nlp.baseline import BaselineExtractor
from packages.nlp.resolve import resolve_datetime
from packages.nlp.zones import zone_info
from packages.providers.geocode import GeocodeError, nominatim
from packages.providers.swiggy import (
    Swiggy,
    SwiggyAuthError,
    SwiggyError,
    SwiggySession,
    Transport,
    authorization_url,
    exchange_code,
    free_deal,
    order_id_in,
    pkce_pair,
    register_client,
    urllib_transport,
)
from packages.providers.tokens import FORMAT_V2, EncryptedTokenStore

from .config import Settings
from .keystore import SecretUnavailable, token_passphrase

STATE_TTL_SEC = 600
#: Rotate the Swiggy token daily while the app runs (see SwiggySession.max_age).
TOKEN_MAX_AGE_SEC = 24 * 3600
PROPOSAL_TTL = timedelta(minutes=15)
#: How far a free slot may be from the time asked for and still be offered.
SLOT_TOLERANCE = timedelta(minutes=45)


class SwiggyFlowError(RuntimeError):
    """Something the user has to fix (sign in, set an area); safe to show."""


# -- auth ----------------------------------------------------------------------


class SwiggyAuth:
    def __init__(self, settings: Settings, kv, transport: Transport | None = None, passphrase: str | None = None):
        self.settings = settings
        #: AppState: remembers the registered client_id per redirect URI.
        self.kv = kv
        self.transport = transport or urllib_transport
        self._passphrase = passphrase
        self._states: dict[str, tuple[float, str, str]] = {}
        self._lock = threading.Lock()

    def start_url(self) -> str:
        client_id = self._client_id()
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        with self._lock:
            self._prune()
            self._states[state] = (time.monotonic() + STATE_TTL_SEC, verifier, client_id)
        return authorization_url(client_id, self.settings.swiggy_redirect_uri, state, challenge)

    def complete(self, code: str | None, state: str | None, error: str | None = None) -> None:
        pending = self._consume(state)
        if pending is None:
            raise SwiggyFlowError("This sign-in link is stale or was not started here. Start again from the app.")
        if error:
            raise SwiggyFlowError("Swiggy did not grant access (%s)." % error)
        if not code:
            raise SwiggyFlowError("Swiggy sent no authorisation code.")
        verifier, client_id = pending
        try:
            payload = exchange_code(client_id, code, self.settings.swiggy_redirect_uri, verifier, self.transport)
        except SwiggyError as exc:
            raise SwiggyFlowError(str(exc)) from None
        self._store().save(payload)

    def disconnect(self) -> None:
        # Swiggy documents no revocation endpoint; the token dies with the file,
        # and unused sessions lapse after 30 days.
        if self.settings.swiggy_token_path.exists():
            self._store().delete()

    def connected(self) -> bool:
        return self.settings.swiggy_token_path.exists()

    def client(self) -> Swiggy | None:
        if not self.connected():
            return None
        return Swiggy(SwiggySession(self._store(), self.transport, max_age=TOKEN_MAX_AGE_SEC), self.transport)

    def describe(self) -> dict:
        return {"connected": self.connected(), "redirect_uri": self.settings.swiggy_redirect_uri}

    # -- internals -----------------------------------------------------------

    def _client_id(self) -> str:
        registered = self.kv.get_value("swiggy_client", {}) or {}
        uri = self.settings.swiggy_redirect_uri
        if not registered.get(uri):
            try:
                registered[uri] = register_client(uri, self.transport)
            except SwiggyError as exc:
                raise SwiggyFlowError(str(exc)) from None
            self.kv.set_value("swiggy_client", registered)
        return registered[uri]

    def _store(self) -> EncryptedTokenStore:
        try:
            passphrase = self._passphrase or token_passphrase(self.settings.token_passphrase)
        except SecretUnavailable as exc:
            raise SwiggyFlowError(str(exc)) from None
        return EncryptedTokenStore(self.settings.swiggy_token_path, passphrase, fmt=FORMAT_V2)

    def _consume(self, state: str | None) -> tuple[str, str] | None:
        if not state:
            return None
        with self._lock:
            self._prune()
            for known in list(self._states):
                if hmac.compare_digest(known, state):
                    _, verifier, client_id = self._states.pop(known)
                    return verifier, client_id
        return None

    def _prune(self) -> None:
        now = time.monotonic()
        for key, (expires, _, _) in list(self._states.items()):
            if expires < now:
                del self._states[key]


# -- table requests --------------------------------------------------------------

_SWIGGY_WORDS = re.compile(r"\b(swiggy|dineout|dine ?out)\b", re.I)
_VENUE = re.compile(
    r"\btable\s+(?:at|in)\s+(?P<venue>.+?)"
    r"(?=\s+for\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b|\s+tomorrow\b|\s+today\b|\s+tonight\b"
    r"|\s+on\s+(?!swiggy|dineout)\w+day\b|\s+on\s+\d|\s+at\s+\d|\s+(?:via|on|through|with|using)\s+(?:swiggy|dineout)\b|[.,!?]|$)",
    re.I,
)
_GUESTS = re.compile(r"\bfor\s+(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b(?!\s*(?:am|pm|:))", re.I)
_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


@dataclass
class TableRequest:
    venue: str
    guests: int
    when: datetime


def parse_table_request(text: str, now: datetime) -> TableRequest | None | str:
    """A Swiggy table request, a string saying what is missing, or None if this
    is not a Swiggy table request at all."""
    if not _SWIGGY_WORDS.search(text) or "table" not in text.lower():
        return None
    venue = _VENUE.search(text)
    if not venue:
        return "Which restaurant? Try: book a table at Toit for 4 tomorrow at 8pm on Swiggy."
    guests_match = _GUESTS.search(text)
    raw = guests_match.group("n").lower() if guests_match else "2"
    guests = int(raw) if raw.isdigit() else _NUMBERS[raw]
    if not 1 <= guests <= 20:
        return "Swiggy takes 1 to 20 guests per table."
    slots = {s.name: s.value for s in BaselineExtractor().extract_slots(text)}
    if not slots.get("time"):
        return "What time? Say something like: at 8pm."
    resolved = resolve_datetime(slots.get("date") or "today", slots.get("time"), now)
    if not resolved.ok:
        return resolved.note or "I couldn't work out the date for that."
    if resolved.value < now:
        return "That time has already passed."
    return TableRequest(venue=venue.group("venue").strip(" ."), guests=guests, when=resolved.value)


@dataclass
class Proposal:
    request_id: str
    created_at: datetime
    timeout_at: datetime
    restaurant: dict
    slot: dict
    deal: dict
    guests: int
    when: datetime
    latitude: float
    longitude: float
    channel: str

    @property
    def prompt(self) -> str:
        place = self.restaurant.get("name", "the restaurant")
        area = self.restaurant.get("locality") or self.restaurant.get("area")
        return "Book a table for %d at %s%s on %s through Swiggy Dineout? It's a free reservation." % (
            self.guests, place, " (%s)" % area if area else "", self.when.strftime("%a %d %b at %I:%M %p").replace(" 0", " "),
        )

    def as_confirmation(self, now: datetime) -> dict:
        return {
            "request_id": self.request_id,
            "status": "pending",
            "prompt": self.prompt,
            "action": "BOOK",
            "created_at": self.created_at.isoformat(),
            "timeout_at": self.timeout_at.isoformat(),
            "seconds_left": max(0, int((self.timeout_at - now).total_seconds())),
            "before": None,
            "after": {
                "id": None, "title": "Table at %s" % self.restaurant.get("name", ""), "start": self.when.isoformat(),
                "end": None, "type": "restaurant", "ref": None, "location": _location(self.restaurant), "alarm": None,
            },
            "source": "swiggy",
            "meta": {"channel": self.channel, "vendor_id": "swiggy", "kind": "swiggy_booking", "guests": self.guests},
        }


def _restaurant(r: dict) -> dict:
    minutes = r.get("deliveryTimeMinutes")
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "cuisines": list(r.get("cuisines") or [])[:4],
        "rating": r.get("avgRating"),
        "ratings": r.get("totalRatings"),
        "eta": r.get("deliveryTimeRange") or ("%s min" % minutes if minutes else None),
        "cost": r.get("costForTwo"),
        "offer": r.get("offer"),
        "image": r.get("imageUrl"),
        "area": r.get("areaName"),
        "open": (r.get("availabilityStatus") or "OPEN").upper() == "OPEN",
        "next_open": r.get("nextOpenTime"),
        "veg": r.get("veg"),
        "url": "https://www.swiggy.com/search?query=%s" % urllib.parse.quote(r.get("name") or ""),
    }


_FOOD_WORDS = re.compile(
    r"\b(order|hungry|craving|eat|food|suggest|recommend|offers?|deals?|coupons?|biryani|pizza|burger|"
    r"burrito|dosa|momos?|noodles|sushi|dessert|cake|ice ?cream|lunch|dinner|breakfast)\b",
    re.I,
)
_NOT_FOOD = re.compile(r"\b(table|flight|train|hotel|cab|taxi|uber|ola|ride|appointment|cancel|move|reschedule|book)\b", re.I)
_FILLER = re.compile(
    r"\b(can|could|would|you|u|please|pls|me|i|im|i'm|want|wanna|to|some|something|now|too|also|order|get|find|"
    r"show|suggest|recommend|any|good|best|food|ideas?|for|hungry|craving|eat|what|should|offers?|deals?|"
    r"coupons?|on|swiggy|near|nearby|here|a|an|the)\b",
    re.I,
)


def parse_food_request(text: str) -> str | None:
    """What to search Swiggy for, or None if this isn't about food.

    "order burrito from california burrito" -> "california burrito";
    "suggest biryani" -> "biryani"; "can u order food now" -> "" (popular)."""
    if not _FOOD_WORDS.search(text) or _NOT_FOOD.search(text):
        return None
    source = re.search(r"\bfrom\s+(.+)$", text, re.I)
    query = source.group(1) if source else _FILLER.sub(" ", text)
    return " ".join(re.findall(r"[\w'&-]+", query)).strip()


def _location(restaurant: dict) -> str:
    return ", ".join(p for p in (restaurant.get("name"), restaurant.get("locality") or restaurant.get("area")) if p)


# -- the desk ---------------------------------------------------------------------


@dataclass
class SwiggyDesk:
    """Order tracking and table bookings for one connected Swiggy account."""

    client: Swiggy
    kv: object  # AppState: tracked reservations and last-seen order states
    settings: Settings
    proposals: dict[str, Proposal] = field(default_factory=dict)
    #: (area, pin) -> {latitude, longitude, address}, when Swiggy can't place it.
    geocoder: Callable[[str, str], dict] = nominatim
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # Food orders -------------------------------------------------------------

    def order_updates(self) -> list[str]:
        """One line per new order or changed status since the last poll."""
        orders = self.client.active_orders()
        seen: dict = self.kv.get_value("swiggy_orders", {}) or {}
        notes: list[str] = []
        current: dict = {}
        for order in orders:
            order_id = str(order.get("orderId") or "")
            if not order_id:
                continue
            status = str(order.get("orderStatus") or "").strip()
            current[order_id] = {"status": status, "title": order.get("title") or "your order"}
            if seen.get(order_id, {}).get("status") != status:
                eta = order.get("etaText")
                notes.append("Swiggy: %s -- %s%s" % (order.get("title") or "your order", status or "update",
                                                      " (%s)" % eta if eta else ""))
        for order_id, old in seen.items():
            if order_id not in current:
                notes.append("Swiggy: %s is no longer in progress." % old.get("title", "an order"))
        self.kv.set_value("swiggy_orders", current)
        return notes

    def active_orders(self) -> list[dict]:
        return [
            {"id": o.get("orderId"), "title": o.get("title"), "subtitle": o.get("subtitle"),
             "status": o.get("orderStatus"), "eta": o.get("etaText"), "progress": o.get("progressPercentage")}
            for o in self.client.active_orders()
        ]

    # Food ideas (read-only: Tripwire never places an order) ---------------------

    def food_ideas(self, query: str) -> dict:
        """Delivery restaurants and dishes for ``query``, and dine-out offers."""
        # Delivery is searched from your saved Swiggy address; only Dineout
        # needs coordinates, and it is optional here.
        data = self.client.food.call("search_restaurants", {"query": query, "addressId": self._address_id()})
        restaurants = [_restaurant(r) for r in data.get("restaurants") or [] if r.get("name")]
        dishes = [
            {"name": d.get("name"), "price": d.get("price"), "restaurant": d.get("restaurant_name") or d.get("restaurantName"),
             "rating": d.get("rating"), "veg": d.get("isVeg"), "bestseller": bool(d.get("isBestseller"))}
            for d in (data.get("dishes") or [])[:8] if d.get("name")
        ]
        try:
            latitude, longitude = self._coordinates()
            dineout = [
                {"id": r.get("id"), "name": r.get("name"), "area": r.get("locality") or r.get("area"),
                 "rating": (r.get("rating") or {}).get("value"), "cost": r.get("costForTwo"),
                 "offers": [o.get("offerTitle") for o in (r.get("offers") or []) + (r.get("bankOffers") or []) if o.get("offerTitle")][:3],
                 "deals": [d.get("dealTitle") for d in r.get("availableDeals") or [] if d.get("dealTitle")][:2]}
                for r in self.client.search_restaurants(query, latitude, longitude, limit=6)
            ]
        except (SwiggyError, SwiggyFlowError):
            dineout = []  # delivery ideas are still worth showing without these
        return {"query": query, "area": self.settings.dining_area, "restaurants": restaurants, "dishes": dishes,
                "dineout": dineout}

    def coupons(self, restaurant_id: str) -> list[dict]:
        """Coupons for one restaurant, checked against your saved delivery address."""
        data = self.client.food.call("fetch_food_coupons", {"restaurantId": restaurant_id, "addressId": self._address_id()})
        out = []
        for section in data.get("coupon_sections") or []:
            for coupon in section.get("coupons") or []:
                out.append({"title": coupon.get("title"), "subtitle": coupon.get("subtitle"),
                            "description": coupon.get("description"), "applicable": coupon.get("applicable"),
                            "code": coupon.get("id")})
        return out

    def _address_id(self) -> str:
        cached = self.kv.get_value("swiggy_address", None)
        if cached:
            return cached
        data = self.client.food.call("get_addresses")
        addresses = data.get("addresses") or []
        address_id = (data.get("resolution") or {}).get("defaultAddressId") or (addresses[0].get("id") if addresses else None)
        if not address_id:
            raise SwiggyFlowError("Add a delivery address in the Swiggy app first; offers depend on it.")
        self.kv.set_value("swiggy_address", address_id)
        return address_id

    # Table bookings ------------------------------------------------------------

    def cancel_table(self, order_id: str) -> None:
        """Cancel a Dineout reservation at Swiggy, and stop watching it."""
        self.client.dineout.call("cancel_booking", {"orderId": order_id, "cancellationReason": "plan changed"})
        tracked = self.kv.get_value("swiggy_tables", {}) or {}
        tracked.pop(order_id, None)
        self.kv.set_value("swiggy_tables", tracked)

    def propose(self, request: TableRequest, now: datetime, channel: str) -> Proposal:
        latitude, longitude = self._coordinates()
        restaurants = self.client.search_restaurants(request.venue, latitude, longitude)
        if not restaurants:
            raise SwiggyFlowError("Swiggy Dineout has nothing matching %r near %s." % (request.venue, self.settings.dining_area))
        restaurant = _best_match(restaurants, request.venue)
        slots = self.client.slots(str(restaurant["id"]), request.when.strftime("%Y-%m-%d"), latitude, longitude)
        slot, deal = self._closest_free_slot(slots, request.when)
        if slot is None:
            raise SwiggyFlowError(
                "%s has no free table near %s. Paid deals need the Swiggy app."
                % (restaurant.get("name", "That restaurant"), request.when.strftime("%I:%M %p").lstrip("0"))
            )
        when = self._slot_time(slot) or request.when
        proposal = Proposal(
            request_id="SW-%s" % secrets.token_hex(4), created_at=now, timeout_at=now + PROPOSAL_TTL,
            restaurant=restaurant, slot=slot, deal=deal, guests=request.guests, when=when,
            latitude=latitude, longitude=longitude, channel=channel,
        )
        with self._lock:
            self.proposals[proposal.request_id] = proposal
        return proposal

    def take(self, request_id: str, now: datetime) -> Proposal | None:
        """Remove and return a live proposal; None if unknown or expired."""
        with self._lock:
            self._expire(now)
            return self.proposals.pop(request_id, None)

    def pending(self, now: datetime) -> list[Proposal]:
        with self._lock:
            self._expire(now)
            return list(self.proposals.values())

    def book(self, proposal: Proposal) -> RestaurantBooking:
        """Call ``book_table`` and return the booking to hand to the agent."""
        result = self.client.book_table(
            restaurant_id=str(proposal.restaurant["id"]), slot_id=int(proposal.slot["slotId"]),
            item_id=str(proposal.deal["itemId"]), reservation_time=int(proposal.slot["reservationTime"]),
            guests=proposal.guests, latitude=proposal.latitude, longitude=proposal.longitude,
        )
        order_id = str(result.get("orderId") or order_id_in(result.get("text") or "") or "")
        if str(result.get("status", "")).upper() == "PENDING_PAYMENT":
            raise SwiggyFlowError("Swiggy did not confirm the table (PENDING_PAYMENT).")
        if not order_id:
            # The call went through, so a table may well be booked: never say it wasn't.
            raise SwiggyFlowError("Swiggy accepted the request but sent no order number, so Tripwire can't track it. "
                                  "Check Upcoming bookings in the Swiggy app before asking again.")
        tracked = self.kv.get_value("swiggy_tables", {}) or {}
        tracked[order_id] = {"when": proposal.when.isoformat(), "name": proposal.restaurant.get("name"),
                             "guests": proposal.guests, "location": _location(proposal.restaurant)}
        self.kv.set_value("swiggy_tables", tracked)
        return self.booking_for(order_id, tracked[order_id], BookingStatus.CONFIRMED, proposal.when)

    def booking_for(self, order_id: str, info: dict, status: BookingStatus, when: datetime) -> RestaurantBooking:
        return RestaurantBooking(
            booking_id=order_id[-8:], booking_type=BookingType.RESTAURANT, date_time=when, status=status,
            source=Source.EMAIL, title="Table at %s" % info.get("name"), booking_ref=order_id,
            location=info.get("location"), end_time=when + timedelta(hours=2),
            venue=info.get("name"), party_size=info.get("guests"),
        )

    def table_changes(self, now: datetime) -> list[RestaurantBooking]:
        """Reservations Tripwire made whose status or time changed at Swiggy."""
        tracked: dict = self.kv.get_value("swiggy_tables", {}) or {}
        changes: list[RestaurantBooking] = []
        for order_id, info in list(tracked.items()):
            when = datetime.fromisoformat(info["when"])
            if when < now - timedelta(hours=6):
                tracked.pop(order_id)  # over; nothing left to watch
                continue
            data = self.client.booking_status(order_id)
            status = str(data.get("status") or "").lower()
            if "cancel" in status:
                changes.append(self.booking_for(order_id, info, BookingStatus.CANCELLED, when))
                tracked.pop(order_id)
                continue
            new_when = _reservation_datetime(data, when)
            if new_when and new_when != when:
                info["when"] = new_when.isoformat()
                changes.append(self.booking_for(order_id, info, BookingStatus.RESCHEDULED, new_when))
        self.kv.set_value("swiggy_tables", tracked)
        return changes

    # internals -------------------------------------------------------------------

    def _coordinates(self) -> tuple[float, float]:
        area, pin = self.settings.dining_area.strip(), self.settings.dining_pin.strip()
        if not area or not pin:
            raise SwiggyFlowError("Set your dining area and PIN code in Settings first, so Swiggy knows where to look.")
        cached = self.kv.get_value("swiggy_geo", {}) or {}
        if cached.get("area") == area and cached.get("pin") == pin:
            return cached["latitude"], cached["longitude"]
        try:
            place = self.client.geocode(area, pin)
        except SwiggyAuthError:
            raise
        except SwiggyError:
            # Swiggy no longer offers recommend_address to Tripwire's client.
            try:
                place = self.geocoder(area, pin)
            except GeocodeError as exc:
                raise SwiggyFlowError(str(exc)) from None
        self.kv.set_value("swiggy_geo", {"area": area, "pin": pin, **place})
        return place["latitude"], place["longitude"]

    def _closest_free_slot(self, slots: list[dict], wanted: datetime) -> tuple[dict | None, dict | None]:
        best: tuple[timedelta, dict, dict] | None = None
        for slot in slots:
            deal = free_deal(slot)
            when = self._slot_time(slot)
            if deal is None or when is None:
                continue
            gap = abs(when - wanted)
            if gap <= SLOT_TOLERANCE and (best is None or gap < best[0]):
                best = (gap, slot, deal)
        return (best[1], best[2]) if best else (None, None)

    def _slot_time(self, slot: dict) -> datetime | None:
        raw = slot.get("reservationTime")
        if raw is None:
            return None
        try:
            stamp = float(raw)
        except (TypeError, ValueError):
            return None
        if stamp > 1e12:  # milliseconds
            stamp /= 1000.0
        tz = zone_info(self.settings.zone)
        return datetime.fromtimestamp(stamp, tz).replace(tzinfo=None) if tz else datetime.fromtimestamp(stamp)

    def _expire(self, now: datetime) -> None:
        for key, proposal in list(self.proposals.items()):
            if proposal.timeout_at <= now:
                del self.proposals[key]


def _best_match(restaurants: list[dict], venue: str) -> dict:
    """The exact name if there is one; else most words in common, fewest extra."""
    wanted = re.findall(r"[a-z0-9]+", venue.lower())

    def score(restaurant: dict) -> tuple[bool, int, int]:
        name = re.findall(r"[a-z0-9]+", (restaurant.get("name") or "").lower())
        hits = sum(1 for word in wanted if word in name)
        return name == wanted, hits, -(len(name) - hits)

    return max(restaurants, key=score)


def _reservation_datetime(data: dict, fallback: datetime) -> datetime | None:
    """Swiggy reports date and time as display strings; read what we can."""
    day, clock = str(data.get("reservationDate") or ""), str(data.get("reservationTime") or "")
    for fmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %H:%M", "%d %b %Y %I:%M %p", "%d %B %Y %I:%M %p", "%a, %d %b %Y %I:%M %p"):
        try:
            return datetime.strptime("%s %s" % (day.strip(), clock.strip()), fmt)
        except ValueError:
            continue
    return None if (day or clock) else fallback

