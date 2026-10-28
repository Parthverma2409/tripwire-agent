"""SyncAgent -- the orchestrator (UC1, UC2, UC3, UC6-query).

Wires NLP -> ReconciliationEngine -> CalendarProvider, with the FR5 confirmation
gate in the middle. Everything it depends on is a protocol: swap `FakeCalendar`
for Google, or the baseline extractor for Ollama, without touching this file.

It emits a trajectory per interaction in the frozen schema, which is what the
Phase 4 capture SDK will forward rather than reinvent.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from packages.core.confirmation import ConfirmationRequest, ConfirmationStatus, ConfirmationStore
from packages.core.decisions import ActionType, Decision, Reason
from packages.core.matching import find_by_phrase
from packages.core.models import (
    BOOKING_CLASSES,
    BookingEvent,
    BookingStatus,
    BookingType,
    CalendarEvent,
    Source,
    Traveller,
)
from packages.nlp.baseline import BaselineExtractor
from packages.nlp.email_extract import EmailExtraction, extract_bookings
from packages.nlp.extractor import Extractor
from packages.nlp.html_text import html_to_text
from packages.nlp.resolve import resolve_datetime
from packages.nlp.schema import RESPONSE_INTENTS, Frame, Intent, Slot
from packages.nlp.trust import SenderTrust, assess
from packages.providers.calendar import CalendarProvider, RateLimitError
from packages.providers.mail import RawEmail
from packages.sync_agent.activation import (
    ActivationWindow,
    find_open_confirmation,
    queue_outside_window,
    release_queued,
)
from packages.sync_agent.reconciliation import ReconciliationEngine, decide

_BOOKING_CLASSES = BOOKING_CLASSES

#: Spoken words for the two app-only types. Checked before the enum values, so
#: "book a cab" is a ride and not -- as the old fallback would have it -- a flight.
_TYPE_WORDS: list[tuple[BookingType, tuple[str, ...]]] = [
    (BookingType.RIDE, ("cab", "taxi", "ride", "uber", "ola")),
    (BookingType.RESTAURANT, ("table", "restaurant", "dinner", "lunch", "dineout")),
]

_INTENT_STATUS = {
    Intent.CREATE_BOOKING: BookingStatus.CONFIRMED,
    Intent.RESCHEDULE_BOOKING: BookingStatus.RESCHEDULED,
    Intent.CANCEL_BOOKING: BookingStatus.CANCELLED,
}


@dataclass
class AgentResult:
    """What the agent did, and what to say to the user.

    `status` mirrors the trajectory schema's outcome vocabulary so the two never
    drift: success, refused, awaiting_confirmation, failure, error.
    """

    status: str
    message: str
    decision: Decision | None = None
    confirmation: ConfirmationRequest | None = None
    needs_clarification: bool = False
    trajectory: dict = field(default_factory=dict)
    #: What the app shows beside the message: extraction stage, vendor, sender
    #: trust, the booking as understood. Not part of the trajectory contract.
    meta: dict = field(default_factory=dict)
    #: Further bookings from the same email (a round trip's return leg). Each
    #: has its own trajectory; the first booking's result carries the rest.
    followups: list["AgentResult"] = field(default_factory=list)

    def all(self) -> list["AgentResult"]:
        return [self, *self.followups]


class SyncAgent:
    def __init__(
        self,
        calendar: CalendarProvider,
        extractor: Extractor | None = None,
        traveller: Traveller | None = None,
        confirmations: ConfirmationStore | None = None,
        activation: ActivationWindow | None = None,
        zone: str | None = None,
        email_fallback: EmailFallback | None = None,
        alarm_minutes: int | None = None,
    ):
        self.calendar = calendar
        self.extractor = extractor or BaselineExtractor()
        self.traveller = traveller or Traveller("u1", "Traveller", "traveller@example.com")
        self.confirmations = confirmations or ConfirmationStore()
        self.engine = ReconciliationEngine(calendar)
        self.quarantine: list[dict] = []
        #: NFR4. None means always active, which is the pre-Phase-5 behaviour and
        #: the default: an agent that silently acquires quiet hours because a
        #: parameter appeared is a worse surprise than one that never sleeps.
        self.activation = activation
        #: IANA zone that vendor timestamps are converted into. None keeps the
        #: vendor's own wall-clock time, which is the pre-app behaviour.
        self.zone = zone
        #: Optional reader of last resort (an LLM) for booking mail the rules
        #: could not parse. Only ever consulted for mail that would otherwise be
        #: quarantined, so it can rescue a booking but never override a rule.
        self.email_fallback = email_fallback
        #: Reminder lead time for new events. None keeps BookingEvent's default.
        self.alarm_minutes = alarm_minutes

    # -- UC1 / UC3 / query: a spoken or typed command ---------------------

    def handle_command(self, text: str, now: datetime) -> AgentResult:
        tape = _Tape(source="voice", text=text, now=now, model=self.extractor.name)

        # NFR4. Checked before extraction: outside the window there is no answer
        # worth computing, and nothing the agent could do with one.
        refusal = self._outside_window(now, tape)
        if refusal is not None:
            return refusal

        frame = self.extractor.extract(text)
        tape.tool("nlp.extract", {"text": text}, frame.intent.value)
        tape.think("intent=%s slots=%s" % (frame.intent.value, [(s.name, s.value) for s in frame.slots]))

        if frame.intent in RESPONSE_INTENTS:
            return self._answer_pending(frame, now, tape)

        if frame.intent is Intent.OUT_OF_SCOPE:
            return tape.done(
                AgentResult("refused", "That does not look like a booking request, so I have not changed anything.")
            )

        if frame.intent is Intent.QUERY_STATUS:
            return self._answer_query(frame, now, tape)

        frame = _with_ride_destination(frame, text, tape)
        return self._reconcile_from_frame(frame, Source.VOICE, now, tape)

    # -- UC2: an inbound email --------------------------------------------

    def handle_email(self, email: RawEmail, now: datetime) -> AgentResult:
        # NFR4 governs when the user can be *asked*, not when mail can be read:
        # an airline sends a cancellation at 03:00 whatever the window says, and
        # refusing to read it would lose the change. So email is never refused
        # here -- only the confirmation prompt is deferred (see _decide_and_act).
        extractions = extract_bookings(
            email.subject,
            email.body,
            now,
            html=email.html,
            attachments=email.attachments,
            sender=email.sender,
            zone=self.zone,
        )
        trust = assess(email.sender, email.headers)
        results = [self._handle_extraction(email, extraction, trust, now) for extraction in extractions]
        first = results[0]
        first.followups = results[1:]
        return first

    def _handle_extraction(
        self, email: RawEmail, extraction: EmailExtraction, trust: SenderTrust, now: datetime
    ) -> AgentResult:
        if (
            extraction.is_booking_related
            and not extraction.is_actionable
            and self.email_fallback is not None
        ):
            rescued = self._try_fallback(email, now)
            if rescued is not None:
                extraction = rescued

        # Prose rules keep their historical model name so existing trajectories
        # and the new ones stay comparable; other stages are named for the stage.
        prose = extraction.stage == "rules" or extraction.stage.startswith("vendor:")
        model = "rule-based-email-v1" if prose else "email:%s" % extraction.stage
        tape = _Tape(source="email", text=email.subject, now=now, model=model)
        self._release_queued(now, tape)
        tape.tool("email.extract", {"message_id": email.message_id}, extraction.reason or "actionable")
        tape.think("extracted by %s; sender %s (%s)" % (extraction.stage, trust.level, trust.reason))
        meta = {
            "message_id": email.message_id,
            "subject": email.subject,
            "sender": email.sender,
            "stage": extraction.stage,
            "vendor_id": extraction.vendor_id or trust.vendor_id,
            "trust": trust.as_dict(),
            "booking": _describe_extraction(extraction),
        }

        if not extraction.is_booking_related:
            tape.think("not a booking email (%s)" % extraction.reason)
            return _with_meta(
                tape.done(AgentResult("refused", "Ignored a non-booking email (%s)." % extraction.reason)), meta
            )

        if not extraction.is_actionable:
            # UC2 E1 -- quarantined and surfaced, never silently dropped.
            self.quarantine.append({"message_id": email.message_id, "subject": email.subject, "reason": extraction.reason})
            tape.think("quarantined: %s" % extraction.reason)
            return _with_meta(
                tape.done(
                    AgentResult(
                        "failure", "Could not read this booking email; flagged it for review (%s)." % extraction.reason
                    )
                ),
                meta,
            )

        booking = _build_booking(
            booking_type=extraction.booking_type or BookingType.APPOINTMENT,
            status=extraction.status,
            when=extraction.date_time,
            title=extraction.title,
            source=Source.EMAIL,
            booking_ref=extraction.booking_ref,
            location=extraction.location,
            end_time=extraction.end_time,
            details=extraction.details,
            alarm_minutes=self.alarm_minutes,
        )
        tape.think("email says %s for %s at %s" % (extraction.status.value, extraction.title, extraction.date_time))
        return _with_meta(self._decide_and_act(booking, now, tape, warning=trust.warning()), meta)

    def _try_fallback(self, email: RawEmail, now: datetime) -> EmailExtraction | None:
        text = email.body
        if email.html and len(text.strip()) < 80:
            text = html_to_text(email.html)
        try:
            rescued = self.email_fallback.extract(email.subject, text, now)
        except Exception:  # noqa: BLE001 -- a dead model must degrade to quarantine, not crash ingestion
            return None
        if rescued is None or not rescued.is_actionable:
            return None
        return rescued

    # -- UC3: resolving a confirmation ------------------------------------

    # -- the user removes an event themselves ------------------------------

    def remove_event(self, event_id: str, now: datetime) -> AgentResult:
        """The user pressed Cancel on one event in the app.

        That press is the consent, so the request is opened and confirmed in one
        step -- but still through ``ConfirmationRequest.confirm()``, the only way
        to a ConfirmedDecision. The trajectory records it as confirmed by the
        user, which is what it is.
        """
        tape = _Tape(source="system", text="remove event %s" % event_id, now=now, model="user-action")
        events = self.calendar.list_events()
        tape.tool("calendar.list_events", {}, len(events))
        target = next((e for e in events if e.event_id == event_id), None)
        if target is None:
            tape.think("event %s is not on the calendar" % event_id)
            return tape.done(AgentResult("refused", "That event isn't on your calendar any more."))
        decision = Decision(ActionType.DELETE, Reason.CANCELLATION, target_event_id=event_id, payload=target,
                            note="removed by the user")
        request = self.confirmations.open(decision, now)
        tape.think("user removed %r themselves; their click is the confirmation (%s)" % (target.title, request.request_id))
        result = self._apply_confirmation(request, True, now, tape)
        if result.status == "success":
            result.message = "Removed %s on %s from your calendar." % (target.title, _on(target.start_time, now))
            result.trajectory["outcome"]["user_visible"] = result.message
        return result

    # -- a booking from a connected account (Swiggy) -----------------------

    def handle_booking(self, booking: BookingEvent, now: datetime, label: str, model: str = "connected-account") -> AgentResult:
        """A structured booking from a connected service rather than prose.

        Same path as a command or an email: reconciliation decides, and a
        destructive outcome (the restaurant cancelled) stops at the gate.
        """
        tape = _Tape(source="system", text=label, now=now, model=model)
        self._release_queued(now, tape)
        tape.think("structured %s booking %s (%s)" % (booking.booking_type.value, booking.booking_ref, booking.status.value))
        return self._decide_and_act(booking, now, tape)

    def resolve_confirmation(self, request_id: str, confirmed: bool | None, now: datetime) -> AgentResult:
        tape = _Tape(source="voice", text="confirmation:%s" % request_id, now=now, model=self.extractor.name)
        try:
            request = self.confirmations.get(request_id)
        except KeyError:
            return tape.done(AgentResult("error", "No such confirmation request."))
        return self._apply_confirmation(request, confirmed, now, tape)

    def _answer_query(self, frame: Frame, now: datetime, tape: "_Tape") -> AgentResult:
        """FR6 -- answer a status question from reconciled calendar state.

        Strictly read-only. A question is not an instruction, so no path out of
        here touches the calendar or opens a confirmation.
        """
        slots = {s.name: s.value for s in frame.slots}
        events = self.calendar.list_events()
        tape.tool("calendar.list_events", {}, len(events))

        if not events:
            return tape.done(AgentResult("success", "You have nothing booked at the moment."))

        matches = self._candidates_for(slots, events)

        if not matches and slots.get("date"):
            wanted = resolve_datetime(slots["date"], None, now)
            if wanted.ok:
                matches = [e for e in events if e.start_time.date() == wanted.value.date()]
                if not matches:
                    return tape.done(
                        AgentResult("success", "Nothing is booked on %s." % _day(wanted.value, now))
                    )

        if not matches and slots.get("destination"):
            matches = find_by_phrase(events, slots["destination"])

        if not matches:
            tape.think("no calendar event matches the query")
            return tape.done(
                AgentResult(
                    "success",
                    "I could not find a booking matching that. You currently have: %s." % _summarise(_upcoming_first(events, now), now),
                )
            )

        tape.think("query matched %d event(s)" % len(matches))
        return tape.done(AgentResult("success", _summarise(_upcoming_first(matches, now), now) + "."))

    def _answer_pending(self, frame: Frame, now: datetime, tape: "_Tape") -> AgentResult:
        pending = self.confirmations.pending(now)
        if not pending:
            # Nothing was asked, so there is nothing to consent to. A stray "yes"
            # must never find something to apply itself to.
            return tape.done(AgentResult("refused", "There is nothing waiting for your confirmation."))
        if len(pending) > 1:
            return tape.done(
                AgentResult(
                    "refused",
                    "There are %d changes waiting. Which one do you mean? %s"
                    % (len(pending), "; ".join(r.prompt for r in pending)),
                    needs_clarification=True,
                )
            )
        confirmed = frame.intent is Intent.CONFIRM_ACTION
        return self._apply_confirmation(pending[0], confirmed, now, tape)

    def _apply_confirmation(
        self, request: ConfirmationRequest, confirmed: bool | None, now: datetime, tape: "_Tape"
    ) -> AgentResult:
        try:
            token = request.resolve(confirmed, now)
        except TimeoutError:
            return tape.done(
                AgentResult("refused", "That request timed out, so I did nothing. Tell me again if you still want it.")
            )
        except ValueError as exc:
            return tape.done(AgentResult("error", str(exc)))

        if token is None:
            tape.think("declined -- no destructive action taken")
            return tape.done(
                AgentResult("refused", "Left it unchanged.", decision=request.decision, confirmation=request)
            )

        try:
            self.engine.apply_destructive(token)
        except RateLimitError:
            return tape.done(AgentResult("failure", "The calendar is rate-limited; I queued the change."))

        action = request.decision.action
        tape.tool("calendar.%s" % action.value.lower(), {"event_id": request.decision.target_event_id}, "ok")
        tape.act(action, request.decision.target_event_id, request.request_id)
        verb = "Deleted" if action is ActionType.DELETE else "Updated"
        return tape.done(
            AgentResult(
                "success",
                "%s the calendar event and its alarm." % verb,
                decision=request.decision,
                confirmation=request,
            )
        )

    # -- NFR4 activation window --------------------------------------------

    def _outside_window(self, now: datetime, tape: "_Tape") -> AgentResult | None:
        """Refuse if the user is outside their window; otherwise release queues.

        Returns a finished refusal, or None to mean "carry on". With
        ``activation=None`` it is a single None-check and the agent behaves
        exactly as it did before Phase 5.
        """
        if self.activation is None:
            return None
        if self.activation.contains(now):
            self._release_queued(now, tape)
            return None
        tape.think("outside the active window %s" % self.activation.describe())
        return tape.done(
            AgentResult(
                "refused",
                "I am only active between %s. I have not changed anything -- tell me again after %s."
                % (self.activation.describe(), self.activation.next_start(now).strftime("%H:%M")),
            )
        )

    def _release_queued(self, now: datetime, tape: "_Tape") -> None:
        """Back inside the window, anything parked by UC3 E2 becomes askable again.

        Without this a queued request is invisible to ``pending()`` for good, and
        a later "yes" would find nothing to attach itself to.
        """
        for request in release_queued(self.confirmations, self.activation, now):
            tape.think("released queued confirmation %s for delivery (UC3 E2)" % request.request_id)

    # -- shared path -------------------------------------------------------

    def _reconcile_from_frame(self, frame: Frame, source: Source, now: datetime, tape: "_Tape") -> AgentResult:
        slots = {s.name: s.value for s in frame.slots}
        status = _INTENT_STATUS[frame.intent]
        events = self.calendar.list_events()

        when: datetime | None = None
        title = slots.get("relative_ref") or _title_from(slots)

        resolution = resolve_datetime(slots.get("date"), slots.get("time"), now)
        if resolution.ok:
            when = resolution.value
            if resolution.note:
                tape.think(resolution.note)
        elif "relative_ref" in slots or "booking_ref" in slots:
            # "Cancel my Chennai flight" carries no date -- the calendar supplies it.
            candidates = self._candidates_for(slots, events)
            # A real calendar holds years of history: last year's Chennai flight
            # is not what "cancel my Chennai flight" means today.
            upcoming = [c for c in candidates if c.start_time >= now - _RECENT]
            if candidates and not upcoming:
                tape.think("every event matching %r is in the past" % title)
                return tape.done(
                    AgentResult(
                        "refused",
                        "That has already happened (%s), so there is nothing to change."
                        % ", ".join("%s on %s" % (c.title, _on(c.start_time, now)) for c in candidates[-3:]),
                    )
                )
            if len(upcoming) < len(candidates):
                tape.think("ignored %d past event(s) matching %r" % (len(candidates) - len(upcoming), title))
            candidates = upcoming
            if len(candidates) == 1:
                when = candidates[0].start_time
                tape.think("resolved %r to event %s at %s" % (title, candidates[0].event_id, when))
            elif len(candidates) > 1:
                tape.think("%d events match %r -- asking rather than guessing" % (len(candidates), title))
                return tape.done(
                    AgentResult(
                        "refused",
                        "More than one booking matches that: %s. Which one?"
                        % ", ".join("%s on %s" % (c.title, _on(c.start_time, now)) for c in candidates),
                        needs_clarification=True,
                    )
                )

        if when is None:
            # UC1 E2 -- ask, never guess. A wrong date is a missed flight.
            tape.think(resolution.note or "no date available")
            return tape.done(
                AgentResult(
                    "refused",
                    "I need a date for that. When is it?",
                    needs_clarification=True,
                )
            )

        if not title:
            return tape.done(
                AgentResult("refused", "I could not tell which booking you meant.", needs_clarification=True)
            )

        booking = _build_booking(
            booking_type=_type_from(slots),
            status=status,
            when=when,
            title=title,
            source=source,
            booking_ref=slots.get("booking_ref"),
            destination=slots.get("destination"),
            location=slots.get("location"),
            alarm_minutes=self.alarm_minutes,
        )
        return self._decide_and_act(booking, now, tape)

    def _candidates_for(self, slots: dict, events: list[CalendarEvent]) -> list[CalendarEvent]:
        ref = slots.get("booking_ref")
        if ref:
            exact = [e for e in events if e.booking_ref and e.booking_ref.upper() == ref.upper()]
            if exact:
                return exact
        phrase = slots.get("relative_ref")
        return find_by_phrase(events, phrase) if phrase else []

    def _decide_and_act(
        self, booking: BookingEvent, now: datetime, tape: "_Tape", warning: str | None = None
    ) -> AgentResult:
        events = self.calendar.list_events()
        tape.tool("calendar.list_events", {}, len(events))
        decision = decide(events, booking)
        tape.think("%s: %s" % (decision.reason.value, decision.note or decision.describe()))

        if decision.reason is Reason.AMBIGUOUS_MATCH:
            return tape.done(
                AgentResult(
                    "refused",
                    "More than one calendar event could be that booking, so I have not touched any of them.",
                    decision=decision,
                    needs_clarification=True,
                )
            )

        if not decision.requires_confirmation:
            try:
                created = self.engine.apply(decision)
            except RateLimitError:
                return tape.done(AgentResult("failure", "The calendar is rate-limited; I queued the change."))
            if decision.action is ActionType.CREATE and created is not None:
                tape.tool("calendar.create_event", {"title": booking.title}, created.event_id)
                tape.act(ActionType.CREATE, created.event_id, None)
                return tape.done(
                    AgentResult(
                        "success",
                        "Added %s on %s to your calendar." % (booking.title, _on(booking.date_time, now)),
                        decision=decision,
                    )
                )
            tape.act(ActionType.NOOP, decision.target_event_id, None)
            return tape.done(AgentResult("success", "Your calendar already reflects that.", decision=decision))

        # Destructive. NFR2: reuse an open request rather than asking twice --
        # including one parked outside the window, which `find_pending_for`
        # cannot see because a queued request is no longer PENDING.
        existing = find_open_confirmation(self.confirmations, booking.booking_ref, decision.target_event_id, now)
        if existing is not None:
            if existing.status is ConfirmationStatus.QUEUED:
                # Saying "I already asked" would be false: the user was never
                # asked, because they were unreachable when it came up.
                message = "I already have that waiting to ask you: %s" % existing.prompt
            else:
                message = "I already asked about that: %s" % existing.prompt
            return tape.done(
                AgentResult("awaiting_confirmation", message, decision=decision, confirmation=existing)
            )

        request = self.confirmations.open(decision, now)
        if warning:
            # Sender trust is advisory: it changes what the user is told before
            # they decide, never whether they are asked.
            request.prompt = "%s %s" % (request.prompt, warning)
            tape.think("sender warning attached to the prompt: %s" % warning)

        # UC3 E2. The user cannot be reached, so the prompt is not delivered --
        # and the change is neither applied nor dropped. Both of those would be
        # defects: applying it is an FR5 violation, dropping it leaves a
        # cancelled booking on the calendar.
        if queue_outside_window(request, self.activation, now):
            opens = self.activation.next_start(now)
            tape.think(
                "user is outside their active window (%s); queued %s until %s (UC3 E2)"
                % (self.activation.describe(), request.request_id, opens.isoformat())
            )
            return tape.done(
                AgentResult(
                    "awaiting_confirmation",
                    "This needs your confirmation, but it is outside your active hours. "
                    "I have changed nothing and will ask you after %s: %s"
                    % (opens.strftime("%H:%M"), request.prompt),
                    decision=decision,
                    confirmation=request,
                )
            )

        tape.tool("user.prompt_confirmation", {"request_id": request.request_id}, request.prompt)
        return tape.done(
            AgentResult("awaiting_confirmation", request.prompt, decision=decision, confirmation=request)
        )


# -- helpers --------------------------------------------------------------


#: How long after it starts an event still counts as current: "cancel my cab"
#: said just after pickup time still means that cab.
_RECENT = timedelta(hours=6)


def _on(when: datetime, now: datetime) -> str:
    """'27 Oct at 14:20', with the year when it is not this year's."""
    return when.strftime("%d %b at %H:%M" if when.year == now.year else "%d %b %Y at %H:%M")


def _day(when: datetime, now: datetime) -> str:
    return when.strftime("%d %b" if when.year == now.year else "%d %b %Y")


def _upcoming_first(events: list[CalendarEvent], now: datetime, limit: int = 8) -> list[CalendarEvent]:
    """What is still ahead; only when nothing is, the most recent past."""
    upcoming = [e for e in events if e.start_time >= now - _RECENT]
    return upcoming[:limit] if upcoming else events[-limit:]


def _summarise(events: list[CalendarEvent], now: datetime) -> str:
    return "; ".join("%s on %s" % (e.title, _on(e.start_time, now)) for e in events)


#: Words in an event title that say what kind of booking it is. For events
#: Tripwire did not create (Gmail adds flights on its own) and so carry no type.
_TITLE_WORDS: list[tuple[BookingType, tuple[str, ...]]] = [
    (BookingType.FLIGHT, ("flight", "airlines", "airways", "indigo", "air india", "akasa", "spicejet", "vistara")),
    (BookingType.TRAIN, ("train", "irctc", "rail", "pnr", "express", "vande bharat", "shatabdi", "rajdhani")),
    (BookingType.HOTEL, ("hotel", "stay", "check-in", "check in", "resort", "airbnb", "oyo", "hostel")),
    (BookingType.RIDE, ("cab", "taxi", "ride", "uber", "ola", "rapido", "pickup")),
    (BookingType.RESTAURANT, ("table", "restaurant", "dinner", "lunch", "dineout", "brunch")),
    (BookingType.APPOINTMENT, ("appointment", "dentist", "doctor", "checkup", "check-up", "clinic", "consultation",
                               "physio", "practo", "salon")),
]


def guess_type(title: str) -> BookingType | None:
    """Best guess from a title, or None when it does not look like a booking."""
    lowered = " %s " % re.sub(r"[^a-z0-9-]+", " ", (title or "").lower())
    for booking_type, words in _TITLE_WORDS:
        if any(" %s " % word in lowered for word in words):
            return booking_type
    return None


def _type_from(slots: dict) -> BookingType:
    raw = (slots.get("booking_type") or "").lower()
    for booking_type, words in _TYPE_WORDS:
        if any(word in raw for word in words):
            return booking_type
    for booking_type in BookingType:
        if booking_type.value in raw:
            return booking_type
    if "appointment" in raw or slots.get("provider"):
        return BookingType.APPOINTMENT
    return BookingType.FLIGHT


#: "to Kempegowda Airport", "to the station": where a ride goes is any place,
#: not only the cities the NLU lexicon knows. Stops before the date or time.
_RIDE_TO = re.compile(
    r"\bto\s+(?P<place>(?:the\s+)?[\w'.-]+(?:\s+(?!tomorrow\b|today\b|tonight\b|on\b|at\b|by\b|for\b|from\b)[\w'.-]+){0,4}?)"
    r"(?=\s+(?:tomorrow|today|tonight|on|at|by|for|from)\b|[.,!?]|$)",
    re.I,
)


_RIDE_BRAND = re.compile(r"\b(?P<brand>uber|ola|rapido|cab|taxi)\b", re.I)


def _with_ride_destination(frame: Frame, text: str, tape: "_Tape") -> Frame:
    """A ride the NLU half-read: "book an uber" (a brand its lexicon lacks) gets
    its type, and a destination outside the city lexicon the words after "to"."""
    if frame.intent is not Intent.CREATE_BOOKING:
        return frame
    slots = {s.name: s.value for s in frame.slots}
    brand = _RIDE_BRAND.search(text)
    if not slots.get("booking_type") and brand:
        kind = Slot(name="booking_type", start=brand.start(), end=brand.end(), value=brand.group("brand").lower())
        frame = replace(frame, slots=frame.slots + (kind,))
        slots["booking_type"] = kind.value
        tape.think("ride type read from the text: %r" % kind.value)
    if slots.get("destination") or _type_from(slots) is not BookingType.RIDE:
        return frame
    match = _RIDE_TO.search(text)
    if not match:
        return frame
    place = match.group("place").strip()
    tape.think("ride destination read from the text: %r" % place)
    slot = Slot(name="destination", start=match.start("place"), end=match.end("place"), value=place)
    return replace(frame, slots=frame.slots + (slot,))


def _title_from(slots: dict) -> str | None:
    parts = [slots.get("booking_type"), "to" if slots.get("destination") else None, slots.get("destination")]
    title = " ".join(p for p in parts if p).strip()
    # "Cab to MG Road", not "cab to MG Road": it is what the calendar shows.
    title = title[:1].upper() + title[1:]
    return title or slots.get("provider")


def _build_booking(
    booking_type: BookingType,
    status: BookingStatus,
    when: datetime,
    title: str,
    source: Source,
    booking_ref: str | None = None,
    destination: str | None = None,
    location: str | None = None,
    end_time: datetime | None = None,
    details: dict | None = None,
    alarm_minutes: int | None = None,
) -> BookingEvent:
    cls = _BOOKING_CLASSES[booking_type]
    # Only the fields this subclass declares: a Flight's details handed to a
    # HotelBooking (after a vendor profile overrode the type) must not raise.
    allowed = {f.name for f in fields(cls)}
    extra = {k: v for k, v in (details or {}).items() if k in allowed}
    return cls(
        booking_id=str(uuid.uuid4())[:8],
        booking_type=booking_type,
        date_time=when,
        status=status,
        source=source,
        title=title,
        booking_ref=booking_ref,
        destination=destination,
        location=location,
        end_time=end_time,
        **({"alarm_offset_min": alarm_minutes} if alarm_minutes is not None else {}),
        **extra,
    )


def _with_meta(result: AgentResult, meta: dict) -> AgentResult:
    result.meta = {**meta, **result.meta}
    return result


def _describe_extraction(extraction: EmailExtraction) -> dict:
    return {
        "status": extraction.status.value if extraction.status else None,
        "type": extraction.booking_type.value if extraction.booking_type else None,
        "ref": extraction.booking_ref,
        "title": extraction.title,
        "start": extraction.date_time.isoformat() if extraction.date_time else None,
        "end": extraction.end_time.isoformat() if extraction.end_time else None,
        "location": extraction.location,
        "reason": extraction.reason,
    }


@runtime_checkable
class EmailFallback(Protocol):
    """Reads a booking email the rules could not. Returns None when it cannot."""

    name: str

    def extract(self, subject: str, text: str, now: datetime) -> EmailExtraction | None: ...


@dataclass
class _Tape:
    """Records one interaction in the frozen trajectory schema (FR7)."""

    source: str
    text: str
    now: datetime
    model: str
    reasoning: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)

    def think(self, note: str) -> None:
        self.reasoning.append({"step": len(self.reasoning) + 1, "text": note, "ts": self.now.isoformat()})

    def tool(self, name: str, args: dict, result) -> None:
        self.tool_calls.append(
            {"name": name, "args": args, "result": result, "ok": True, "error": None, "ts": self.now.isoformat()}
        )

    def act(self, action: ActionType, target: str | None, confirmation_id: str | None) -> None:
        destructive = action in (ActionType.DELETE, ActionType.UPDATE)
        self.actions.append(
            {
                "type": action.value,
                "target": target,
                "destructive": destructive,
                "confirmed_by_user": bool(confirmation_id) if destructive else None,
                "confirmation_id": confirmation_id,
            }
        )

    def done(self, result: AgentResult) -> AgentResult:
        result.trajectory = {
            "trajectory_id": str(uuid.uuid4()),
            "project_id": "booking-sync",
            "archetype": "tool_using_agent",
            "agent": {"model": self.model, "prompt_version": None, "commit": None},
            "task": {"source": self.source, "input": self.text, "use_case": None},
            "reasoning": self.reasoning or None,
            "tool_calls": self.tool_calls,
            "actions": self.actions,
            "outcome": {"status": result.status, "user_visible": result.message},
            "timing": {"started": self.now.isoformat(), "ended": self.now.isoformat(), "total_ms": None},
            "scenario_id": None,
            "scores": None,
            "schema_version": "1.0",
        }
        return result
