"""The running system: one agent, its providers, and everything around it.

``Runtime`` is the only object the HTTP layer and the background loops talk to.
It does four things the agent itself does not:

  1. **Builds** the agent for the current mode -- demo (fake calendar, demo
     inbox) or live (Google Calendar, then Gmail or IMAP) -- and rebuilds it when
     an account is connected or disconnected.
  2. **Serialises** calls into it. ``SyncAgent`` and ``ConfirmationStore`` are
     not thread-safe, and the API thread pool, the inbox poll and the Telegram
     loop all reach for them. One re-entrant lock, held for each interaction.
  3. **Records** each interaction: activity log, inbox log, and the trajectory,
     scored by the FR8 evaluators through the same ``IngestPipeline`` the
     benchmark uses.
  4. **Delivers** questions -- to the browser over SSE, and to Telegram when the
     question did not come from someone already looking at the app -- and,
     unless switched off, a note every time the calendar actually changes.
  5. **Remembers** what the calendar cannot hold: where each booking came
     from, its pickup / party size / email, and its history (booking notes),
     for the booking detail view.

It never decides anything about a booking. Every path into the calendar goes
through ``SyncAgent``; every destructive path through its confirmation gate.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from datetime import date, datetime
from enum import Enum
from typing import Callable

from packages.core.confirmation import ConfirmationStatus
from packages.core.decisions import ActionType
from packages.core.models import CalendarEvent
from packages.eval_ingest.pipeline import IngestPipeline
from packages.nlp.baseline import BaselineExtractor
from packages.nlp.extractor import FallbackExtractor, OllamaExtractor
from packages.nlp.zones import zone_info
from packages.notify.telegram import BotEvent, TelegramBot, TelegramError
from packages.providers.swiggy import SwiggyAuthError, SwiggyError
from packages.providers.calendar import CalendarError, CalendarProvider
from packages.providers.eml import parse_eml
from packages.providers.mail import MailProvider, RawEmail
from packages.sync_agent.activation import ActivationWindow
from packages.sync_agent.agent import AgentResult, SyncAgent, guess_type

from .config import Settings
from .state import AppState
from .swiggy import SwiggyDesk, SwiggyFlowError, parse_food_request, parse_table_request


class NotReady(RuntimeError):
    """The requested action needs an account that is not connected yet."""


# -- event bus (server-sent events) ------------------------------------------


class EventBus:
    """Fan-out from worker threads to every open SSE stream.

    Publishers are threads (the API pool, the poll loops); subscribers are
    asyncio queues on the server's loop. ``call_soon_threadsafe`` is the bridge.
    A slow or vanished browser tab drops events rather than backing anything up.
    """

    def __init__(self) -> None:
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = [(loop, q) for loop, q in self._subscribers if q is not queue]

    def publish(self, kind: str, data: dict | None = None) -> None:
        message = {"type": kind, "data": data or {}}
        with self._lock:
            targets = list(self._subscribers)
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(_offer, queue, message)
            except RuntimeError:
                pass  # loop closed; the stream is gone


def _offer(queue: asyncio.Queue, message: dict) -> None:
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        pass


# -- runtime -----------------------------------------------------------------


@dataclass
class ConnectionStatus:
    calendar: dict = field(default_factory=dict)
    mail: dict = field(default_factory=dict)
    telegram: dict = field(default_factory=dict)
    ollama: dict = field(default_factory=dict)
    voice: dict = field(default_factory=dict)
    swiggy: dict = field(default_factory=dict)


class Runtime:
    def __init__(
        self,
        settings: Settings,
        state: AppState,
        clock: Callable[[], datetime] | None = None,
        google=None,
        telegram_transport=None,
        swiggy=None,
    ):
        self.settings = settings
        #: The persistent store: settings, and everything recorded in live mode.
        self.store = state
        #: Where interactions are recorded right now. In live mode this is
        #: ``store``; in demo mode a fresh in-memory database per build, so the
        #: demo's mail log, seen-set and activity always match its in-memory
        #: calendar -- and demo noise never lands in the real history.
        self.state = state
        self.bus = EventBus()
        self.pipeline = IngestPipeline(store=state)
        self.lock = threading.RLock()
        self._clock = clock
        #: GoogleAuth (google_auth.py); None in demo-only setups and tests.
        self.google = google
        self._telegram_transport = telegram_transport
        #: SwiggyAuth (swiggy.py); None in demo-only setups and tests.
        self.swiggy_auth = swiggy
        self.swiggy: SwiggyDesk | None = None
        self.calendar: CalendarProvider | None = None
        self.mail: MailProvider | None = None
        self.mail_channel: str = "none"
        self.agent: SyncAgent | None = None
        self.bot: TelegramBot | None = None
        self.transcriber = None
        self.status = ConnectionStatus()
        #: request_id -> what the app shows beside a pending question.
        self._request_meta: dict[str, dict] = {}
        self._announced_expiry: set[str] = set()
        #: Bumped by every build. An inbox scan remembers the generation it
        #: fetched under and stops if it changes: mail fetched from the demo
        #: inbox must never be handed to the agent that writes to Google.
        self.generation = 0
        self.build()

    # -- clock -------------------------------------------------------------

    def now(self) -> datetime:
        """Naive wall-clock time in the configured zone, like the domain model."""
        if self._clock is not None:
            return self._clock()
        tz = zone_info(self.settings.zone)
        return datetime.now(tz).replace(tzinfo=None) if tz else datetime.now()

    # -- building ----------------------------------------------------------

    def build(self) -> None:
        """(Re)build providers and agent for the current settings.

        Rebuilding forgets pending confirmations: they belong to the agent that
        asked. That is the fail-safe direction -- nothing unapproved can survive
        a rebuild and be applied later.
        """
        with self.lock:
            self._apply_saved_settings()
            self.state = self.store if self.settings.mode == "live" else AppState(":memory:")
            self.pipeline = IngestPipeline(store=self.state)
            self.status = ConnectionStatus()
            self._request_meta.clear()
            self._announced_expiry.clear()
            self.generation += 1
            if self.settings.mode == "demo":
                self._build_demo()
            else:
                self._build_live()
            self.bot = self._build_telegram()
            self.swiggy = self._build_swiggy()
            extractor, fallback = self._build_extractors()
            self.transcriber = self._build_transcriber()
            if self.calendar is None:
                self.agent = None
            else:
                window = self.settings.active_window.strip()
                self.agent = SyncAgent(
                    self.calendar,
                    extractor=extractor,
                    activation=ActivationWindow.parse(window) if window else None,
                    zone=self.settings.zone,
                    email_fallback=fallback,
                    alarm_minutes=int(self.settings.alarm_minutes),
                )
        self.bus.publish("rebuilt", self.status_dict())

    def _apply_saved_settings(self) -> None:
        overrides = self.store.get_value("settings", {}) or {}
        for key in ("zone", "active_window", "poll_minutes", "alarm_minutes", "mode",
                    "notify_changes", "dining_area", "dining_pin"):
            if key in overrides and overrides[key] not in (None, ""):
                setattr(self.settings, key, overrides[key])

    def _build_demo(self) -> None:
        from .demo import demo_calendar, demo_inbox

        now = self.now()
        self.calendar = demo_calendar(now)
        inbox = demo_inbox(now)
        self.mail = inbox
        self.mail_channel = "demo"
        self.status.calendar = {"ok": True, "provider": "demo", "detail": "In-memory demo calendar"}
        self.status.mail = {"ok": True, "provider": "demo", "detail": "Demo inbox with %d sample emails" % len(inbox._queue)}

    def _build_live(self) -> None:
        self.calendar = None
        self.mail = None
        self.mail_channel = "none"
        client = None
        if self.google is not None:
            try:
                client = self.google.api_client()
            except Exception as exc:  # noqa: BLE001 -- surfaced on the Connections page
                self.status.calendar = {"ok": False, "provider": "google", "detail": str(exc)}
        if client is not None:
            from packages.providers.gmail import BOOKING_QUERY, GmailProvider
            from packages.providers.google_calendar import GoogleCalendarProvider

            self.calendar = GoogleCalendarProvider(
                client, calendar_id=self.settings.calendar_id, time_zone=self.settings.zone
            )
            self.status.calendar = {"ok": True, "provider": "google", "detail": "Google Calendar (%s)" % self.settings.calendar_id}
            self.mail = GmailProvider(client, query=self.settings.gmail_query or BOOKING_QUERY, _seen=self.state.seen_set())
            self.mail_channel = "gmail"
            self.status.mail = {"ok": True, "provider": "gmail", "detail": "Gmail, read-only"}
        elif not self.status.calendar:
            self.status.calendar = {
                "ok": False,
                "provider": "google",
                "detail": "Not connected" if self.settings.google_configured else "Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env",
            }

        if self.mail is None and self.settings.imap_configured:
            from packages.providers.imap import ImapProvider

            self.mail = ImapProvider(
                self.settings.imap_host, self.settings.imap_user, self.settings.imap_password,
                port=self.settings.imap_port, _seen=self.state.seen_set(),
            )
            self.mail_channel = "imap"
            self.status.mail = {"ok": True, "provider": "imap", "detail": "IMAP %s (read-only)" % self.settings.imap_host}
        elif self.mail is None:
            self.status.mail = {"ok": False, "provider": None, "detail": "Connect Google, or set IMAP_* in .env"}

    def _build_telegram(self) -> TelegramBot | None:
        if not self.settings.telegram_configured:
            self.status.telegram = {"ok": False, "detail": "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"}
            return None
        from .keystore import SecretUnavailable, bot_signing_key, token_passphrase

        try:
            key = bot_signing_key(token_passphrase(self.settings.token_passphrase))
        except SecretUnavailable as exc:
            self.status.telegram = {"ok": False, "detail": str(exc)}
            return None
        self.status.telegram = {"ok": True, "detail": "Approvals go to chat %s" % self.settings.telegram_chat_id}
        return TelegramBot(
            token=self.settings.telegram_token,
            chat_id=self.settings.telegram_chat_id,
            secret=key,
            transport=self._telegram_transport,
        )

    def _build_swiggy(self) -> SwiggyDesk | None:
        if self.swiggy_auth is None:
            self.status.swiggy = {"ok": False, "detail": "Not available in this setup"}
            return None
        if self.settings.mode != "live":
            self.status.swiggy = {"ok": False, "detail": "Switch to live mode to use Swiggy"}
            return None
        try:
            client = self.swiggy_auth.client()
        except SwiggyFlowError as exc:
            self.status.swiggy = {"ok": False, "detail": str(exc)}
            return None
        if client is None:
            self.status.swiggy = {"ok": False, "detail": "Not connected"}
            return None
        self._swiggy_ok()
        return SwiggyDesk(client, self.store, self.settings)

    def _swiggy_ok(self) -> None:
        where = self.settings.dining_area or "no dining area set yet (Settings)"
        self.status.swiggy = {"ok": True, "detail": "Order updates on; tables near %s" % where}

    def _build_extractors(self):
        baseline = BaselineExtractor()
        host = self.settings.ollama_host.strip()
        if not host:
            self.status.ollama = {"ok": False, "detail": "Off -- rules only. Set OLLAMA_HOST to enable."}
            return baseline, None
        models = _ollama_models(host)
        if models is None:
            self.status.ollama = {"ok": False, "detail": "Ollama is not reachable at %s -- using rules only" % host}
            return baseline, None
        if not any(m == self.settings.ollama_model or m.startswith(self.settings.ollama_model + ":") for m in models):
            self.status.ollama = {
                "ok": False,
                "detail": "Model %s not pulled. Run: ollama pull %s" % (self.settings.ollama_model, self.settings.ollama_model),
            }
            return baseline, None
        from packages.nlp.llm_email import OllamaEmailExtractor

        self.status.ollama = {"ok": True, "detail": "%s at %s" % (self.settings.ollama_model, host)}
        return (
            FallbackExtractor(OllamaExtractor(model=self.settings.ollama_model, host=host), baseline),
            OllamaEmailExtractor(model=self.settings.ollama_model, host=host),
        )

    def _build_transcriber(self):
        model = self.settings.whisper_model.strip()
        if model:
            try:
                import faster_whisper  # noqa: F401 -- availability check only
            except ImportError:
                self.status.voice = {
                    "ok": True, "provider": "browser",
                    "detail": "WHISPER_MODEL is set but faster-whisper is not installed (pip install faster-whisper). "
                              "Using the browser's speech recognition.",
                }
                return None
            from packages.nlp.asr import WhisperTranscriber

            self.status.voice = {"ok": True, "provider": "whisper", "detail": "Local Whisper (%s); audio stays on this computer" % model}
            return WhisperTranscriber(model_size=model)
        self.status.voice = {"ok": True, "provider": "browser", "detail": "Browser speech recognition (Chrome / Edge)"}
        return None

    def voice(self, audio: bytes, suffix: str = ".webm") -> dict:
        """Local speech-to-text, then the ordinary command path.

        Same two refusals as ``VoiceShell``: an empty transcript is not a
        command, and a low-confidence one is quoted back rather than acted on.
        Transcription runs outside the agent lock -- it takes seconds, and the
        inbox poll should not wait for it.
        """
        import os
        import tempfile

        from packages.nlp.tts import DEFAULT_MIN_CONFIDENCE

        if self.transcriber is None:
            raise NotReady("Local voice is off. Set WHISPER_MODEL in .env, or use the browser microphone.")
        if not audio:
            raise ValueError("No audio was recorded.")
        handle, path = tempfile.mkstemp(suffix=suffix if suffix.startswith(".") else ".webm")
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(audio)
            transcript = self.transcriber.transcribe(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if transcript.is_empty:
            return {"status": "refused", "message": "I didn't catch anything. Could you say that again?", "transcript": ""}
        if transcript.confidence is not None and transcript.confidence < DEFAULT_MIN_CONFIDENCE:
            return {
                "status": "refused",
                "message": "I think you said \"%s\", but I'm not sure. Edit it and press Enter." % transcript.text,
                "transcript": transcript.text,
            }
        return self.command(transcript.text, channel="app") | {"transcript": transcript.text}

    def _require_agent(self) -> SyncAgent:
        if self.agent is None:
            raise NotReady("Connect a calendar first (Connections page).")
        return self.agent

    # -- interactions ------------------------------------------------------

    def command(self, text: str, channel: str = "app") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("Say or type something first.")
        table = parse_table_request(text, self.now())
        if table is not None:
            return self._swiggy_command(text, table, channel)
        food = parse_food_request(text)
        if food is not None:
            return self._food_command(text, food, channel)
        with self.lock:
            agent = self._require_agent()
            result = self._guard(lambda: agent.handle_command(text, self.now()))
            payload = self._record(result, channel=channel, kind="command", input=text)
        self._after(result, channel)
        return payload

    def resolve(self, request_id: str, approve: bool, channel: str = "app") -> dict:
        if request_id.startswith("SW-"):
            return self._resolve_swiggy(request_id, approve, channel)
        with self.lock:
            agent = self._require_agent()
            result = self._guard(lambda: agent.resolve_confirmation(request_id, approve, self.now()))
            meta = self._request_meta.pop(request_id, {})
            result.meta = {**meta, "request_id": request_id, "approved": approve}
            payload = self._record(result, channel=channel, kind="decision", input="%s %s" % ("approve" if approve else "decline", request_id))
        self.bus.publish("calendar", {})
        self.bus.publish("confirmations", {})
        self._announce(payload, channel)
        return payload

    def ingest_email(self, email: RawEmail, channel: str, generation: int | None = None) -> list[dict] | None:
        """Handle one email. None, and nothing done, when it was fetched under an
        earlier build (``generation``) -- the inbox it came from is gone."""
        with self.lock:
            if generation is not None and generation != self.generation:
                return None
            if channel == "demo" and self.settings.mode != "demo":
                return None  # belt and braces: sample mail never meets a real calendar
            agent = self._require_agent()
            result = self._guard(lambda: agent.handle_email(email, self.now()))
            payloads = []
            for idx, each in enumerate(result.all()):
                payload = self._record(each, channel=channel, kind="email", input=email.subject)
                self.state.record_mail(
                    message_id=email.message_id, idx=idx, channel=channel, received_at=email.received_at,
                    sender=email.sender, subject=email.subject, stage=each.meta.get("stage"),
                    vendor_id=each.meta.get("vendor_id"), trust=each.meta.get("trust"), status=each.status,
                    message=each.message, booking=each.meta.get("booking"),
                    request_id=each.confirmation.request_id if each.confirmation else None,
                )
                payloads.append(payload)
        for each in result.all():
            self._after(each, channel)
        self.bus.publish("inbox", {})
        return payloads

    def upload_eml(self, raw: bytes) -> list[dict]:
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("That file is over 5 MB -- it is not a single email.")
        email = parse_eml(raw)
        if not email.subject and not email.body and not email.html:
            raise ValueError("That file does not look like an email (.eml).")
        return self.ingest_email(email, channel="upload")

    def scan(self) -> dict:
        """One pass over the inbox. Seen only after handling, like EmailIngestionModule."""
        with self.lock:
            mail, channel, generation = self.mail, self.mail_channel, self.generation
            if mail is None:
                raise NotReady("No inbox connected.")
            self._require_agent()
        try:
            emails = mail.fetch_new()
        except Exception as exc:  # noqa: BLE001 -- shown to the user, retried next poll
            self.status.mail = {**self.status.mail, "ok": False, "detail": "Last scan failed: %s" % exc}
            self.bus.publish("status", self.status_dict())
            raise
        handled = 0
        for email in emails:
            if self.ingest_email(email, channel=channel, generation=generation) is None:
                break  # rebuilt mid-scan (mode switch, new account): the next scan starts clean
            mail.mark_seen(email.message_id)
            handled += 1
        stamp = self.now().isoformat(timespec="seconds")
        self.state.set_value("last_scan", {"at": stamp, "handled": handled})
        self.bus.publish("scan", {"at": stamp, "handled": handled})
        return {"handled": handled, "at": stamp}

    def tick(self) -> None:
        """Expire overdue questions (UC3 E1) and say so, once each."""
        with self.lock:
            if self.agent is None:
                return
            now = self.now()
            self.agent.confirmations.pending(now)  # expires anything overdue
            expired = [
                r for r in self.agent.confirmations.all()
                if r.status is ConfirmationStatus.TIMED_OUT and r.request_id not in self._announced_expiry
            ]
            for request in expired:
                self._announced_expiry.add(request.request_id)
                self._request_meta.pop(request.request_id, None)
                self.state.log(
                    channel="system", kind="timeout", input=None, status="refused",
                    message="No answer in time, so nothing was changed: %s" % request.prompt,
                    request_id=request.request_id,
                )
        if expired:
            self.bus.publish("confirmations", {})
            self.bus.publish("activity", {})

    def handle_bot_event(self, event: BotEvent) -> None:
        if self.bot is None:
            return
        try:
            if event.kind == "decision" and event.request_id:
                outcome = self.resolve(event.request_id, bool(event.confirmed), channel="telegram")
                self.bot.acknowledge(event, outcome["message"])
            elif event.kind == "text" and event.text:
                outcome = self.command(event.text, channel="telegram")
                if outcome["status"] != "awaiting_confirmation":
                    self.bot.send_text(outcome["message"])
        except (NotReady, ValueError) as exc:
            self.bot.send_text(str(exc))
        except TelegramError:
            pass

    # -- reads -------------------------------------------------------------

    def events(self) -> list[dict]:
        with self.lock:
            if self.calendar is None:
                return []
            try:
                return [_event_dict(e) for e in self.calendar.list_events()]
            except CalendarError as exc:
                raise NotReady("Could not read the calendar: %s" % exc) from exc

    def confirmations(self) -> list[dict]:
        with self.lock:
            if self.agent is None:
                return []
            now = self.now()
            self.agent.confirmations.pending(now)
            events = {e.event_id: e for e in self._safe_events()}
            out = []
            for request in self.agent.confirmations.all():
                if request.status not in (ConfirmationStatus.PENDING, ConfirmationStatus.QUEUED):
                    continue
                decision = request.decision
                target = events.get(decision.target_event_id or "")
                out.append({
                    "request_id": request.request_id,
                    "status": request.status.value,
                    "prompt": request.prompt,
                    "action": decision.action.value,
                    "created_at": request.created_at.isoformat(),
                    "timeout_at": request.timeout_at.isoformat(),
                    "seconds_left": max(0, int((request.timeout_at - now).total_seconds())),
                    "before": _event_dict(target) if target else None,
                    "after": _event_dict(decision.payload) if decision.action is ActionType.UPDATE and decision.payload else None,
                    "source": decision.booking.source.value if decision.booking else None,
                    "meta": self._request_meta.get(request.request_id, {}),
                })
            if self.swiggy is not None:
                out.extend(p.as_confirmation(now) for p in self.swiggy.pending(now))
            return out

    def _safe_events(self) -> list[CalendarEvent]:
        try:
            return self.calendar.list_events() if self.calendar else []
        except CalendarError:
            return []

    def status_dict(self) -> dict:
        last = self.state.get_value("last_scan")
        return {
            "mode": self.settings.mode,
            "ready": self.agent is not None,
            "connections": asdict(self.status),
            "google": self.google.describe() if self.google is not None else {"configured": False, "connected": False},
            "swiggy": self.swiggy_auth.describe() if self.swiggy_auth is not None else {"connected": False},
            "mail_channel": self.mail_channel,
            "last_scan": last,
            "settings": self.settings.public(),
            "ingest": self.pipeline.snapshot(),
        }

    # -- recording ---------------------------------------------------------

    def _guard(self, call: Callable[[], AgentResult]) -> AgentResult:
        try:
            return call()
        except CalendarError as exc:
            # A calendar outage is a fact to show the user, not a 500.
            return AgentResult("error", "The calendar could not be reached: %s" % exc)

    def _record(self, result: AgentResult, *, channel: str, kind: str, input: str) -> dict:
        trajectory_id, scores = None, None
        if result.trajectory:
            ingest = self.pipeline.submit(result.trajectory, source=channel)
            trajectory_id, scores = ingest.trajectory_id, ingest.scores
        request_id = result.confirmation.request_id if result.confirmation else None
        if result.status == "awaiting_confirmation" and request_id:
            self._request_meta[request_id] = {
                k: v for k, v in result.meta.items() if k in ("stage", "vendor_id", "trust", "subject", "sender", "booking")
            } | {"channel": channel}
        changes = _changes(result)
        if changes:
            self._remember(result, changes, channel)
        meta = {k: v for k, v in result.meta.items() if k != "booking"}
        if changes:
            meta["targets"] = [c["target"] for c in changes]
        self.state.log(
            channel=channel, kind=kind, input=input, status=result.status, message=result.message,
            meta=meta or None, trajectory_id=trajectory_id, request_id=request_id,
        )
        return {
            "changes": changes,
            "status": result.status,
            "message": result.message,
            "needs_clarification": result.needs_clarification,
            "request_id": request_id,
            "trajectory_id": trajectory_id,
            "scores": scores,
            "meta": result.meta,
        }

    def _after(self, result: AgentResult, channel: str) -> None:
        self.bus.publish("activity", {})
        if _changes(result):
            self._announce({"message": result.message, "changes": _changes(result)}, channel)
        if result.status in ("success",):
            self.bus.publish("calendar", {})
        if result.status == "awaiting_confirmation" and result.confirmation is not None:
            self.bus.publish("confirmations", {"request_id": result.confirmation.request_id})
            # Someone typing in the app is looking at the card already; anything
            # else (email, Telegram) needs the question taken to them.
            if self.bot is not None and channel != "app":
                trust = (result.meta.get("trust") or {}) if result.meta else {}
                detail = "From: %s" % result.meta.get("sender") if result.meta.get("sender") else None
                if trust.get("level") == "failed":
                    detail = (detail + "\n" if detail else "") + "Sender failed authentication."
                try:
                    self.bot.send_confirmation(result.confirmation.request_id, result.message, detail)
                except TelegramError as exc:
                    self.status.telegram = {"ok": False, "detail": str(exc)}


    # -- notifications -----------------------------------------------------

    def _announce(self, payload: dict, channel: str) -> None:
        """A real calendar change, told to the browser and the phone."""
        if payload.get("changes"):
            self.notify(payload["message"], kind="calendar", channel=channel)

    def notify(self, message: str, kind: str = "info", channel: str = "system") -> None:
        """Browser always; Telegram unless it is off, or the change came from
        Telegram itself (the bot has already answered there)."""
        self.bus.publish("notify", {"message": message, "kind": kind, "channel": channel})
        if not self.settings.notify_changes or self.bot is None or channel == "telegram":
            return
        try:
            self.bot.send_text(message)
        except TelegramError as exc:
            self.status.telegram = {"ok": False, "detail": str(exc)}

    # -- Swiggy ------------------------------------------------------------

    def swiggy_failed(self, exc: Exception) -> None:
        """A Swiggy call failed. If Swiggy has signed Tripwire out, forget the
        dead sign-in and say so once, so the app asks to connect again instead
        of showing a connection that fails every call."""
        if not isinstance(exc, SwiggyAuthError):
            return
        message = "Swiggy signed Tripwire out (the sign-in expired). Connect Swiggy again on the Connections page."
        self.status.swiggy = {"ok": False, "detail": message}
        if self.swiggy is None:
            return
        self.swiggy = None
        if self.swiggy_auth is not None:
            self.swiggy_auth.disconnect()
        self.state.log(channel="swiggy", kind="auth", input=None, status="failure", message=message)
        self.notify(message, kind="swiggy")
        self.bus.publish("activity", {})
        self.bus.publish("swiggy", {})

    def _swiggy_command(self, text: str, table, channel: str) -> dict:
        request_id = None
        if self.swiggy is None:
            status, message = "refused", "Connect Swiggy first (Connections page) to book tables there."
        elif isinstance(table, str):
            status, message = "refused", table
        else:
            try:
                proposal = self.swiggy.propose(table, self.now(), channel)
            except (SwiggyFlowError, SwiggyError) as exc:
                self.swiggy_failed(exc)
                status, message = "refused", str(exc)
            else:
                status, message, request_id = "awaiting_confirmation", proposal.prompt, proposal.request_id
                self.bus.publish("confirmations", {"request_id": request_id})
                if self.bot is not None and channel != "app":
                    try:
                        self.bot.send_confirmation(request_id, message, "Swiggy Dineout")
                    except TelegramError as exc:
                        self.status.telegram = {"ok": False, "detail": str(exc)}
        self.state.log(channel=channel, kind="command", input=text, status=status, message=message,
                       meta={"vendor_id": "swiggy"}, request_id=request_id)
        self.bus.publish("activity", {})
        return {"status": status, "message": message, "needs_clarification": status == "refused",
                "request_id": request_id, "trajectory_id": None, "scores": None,
                "meta": {"vendor_id": "swiggy"}, "changes": []}

    def _resolve_swiggy(self, request_id: str, approve: bool, channel: str) -> dict:
        if self.swiggy is None:
            raise NotReady("Swiggy is not connected.")
        proposal = self.swiggy.take(request_id, self.now())
        if proposal is None:
            raise ValueError("That booking offer has expired. Ask again for a fresh slot.")
        decision = "%s %s" % ("approve" if approve else "decline", request_id)
        if not approve:
            message = "Okay, I didn't book %s." % proposal.restaurant.get("name", "the table")
            return self._swiggy_outcome("refused", message, request_id, channel, decision)
        try:
            booking = self.swiggy.book(proposal)
        except (SwiggyFlowError, SwiggyError) as exc:
            self.swiggy_failed(exc)
            return self._swiggy_outcome("failure", "Swiggy couldn't book it: %s" % exc, request_id, channel, decision)
        payload = self.ingest_booking(booking, "Swiggy Dineout %s" % booking.booking_ref, channel,
                                      meta={"vendor_id": "swiggy", "stage": "swiggy"})
        payload["message"] = "Booked on Swiggy (order %s). %s" % (booking.booking_ref, payload["message"])
        self.bus.publish("confirmations", {})
        return payload

    def _swiggy_outcome(self, status: str, message: str, request_id: str, channel: str, decision: str) -> dict:
        self.state.log(channel=channel, kind="decision", input=decision, status=status, message=message,
                       request_id=request_id)
        self.bus.publish("confirmations", {})
        self.bus.publish("activity", {})
        return {"status": status, "message": message, "request_id": request_id, "changes": [], "meta": {}}

    def ingest_booking(self, booking, label: str, channel: str, meta: dict | None = None) -> dict:
        """A structured booking from a connected account, through the agent."""
        with self.lock:
            agent = self._require_agent()
            result = self._guard(lambda: agent.handle_booking(booking, self.now(), label, model="swiggy-mcp"))
            result.meta = {**(meta or {}), **result.meta}
            payload = self._record(result, channel=channel, kind="booking", input=label)
        self._after(result, channel)
        return payload

    def swiggy_poll(self) -> None:
        """Order updates become notifications; table changes go through the agent."""
        if self.swiggy is None:
            return
        try:
            for line in self.swiggy.order_updates():
                self.state.log(channel="swiggy", kind="order", input=None, status="success", message=line)
                self.notify(line, kind="order")
            if self.agent is not None:
                for booking in self.swiggy.table_changes(self.now()):
                    self.ingest_booking(booking, "Swiggy Dineout %s %s" % (booking.booking_ref, booking.status.value),
                                        channel="swiggy", meta={"vendor_id": "swiggy", "stage": "swiggy"})
            if not self.status.swiggy.get("ok"):
                self._swiggy_ok()
        except SwiggyError as exc:
            self.status.swiggy = {"ok": False, "detail": str(exc)}
            self.swiggy_failed(exc)
        self.bus.publish("activity", {})
        self.bus.publish("swiggy", {})

    def _food_command(self, text: str, query: str, channel: str) -> dict:
        """Tripwire suggests food; it never orders it. The reply points at the
        Food page (or at connecting Swiggy, which the ideas come from)."""
        if self.swiggy is None:
            message = "I don't place food orders, but with Swiggy connected I can show ideas and offers near you."
            link, label = "/connections/", "Connect Swiggy"
        else:
            message = "I don't place food orders, but here are ideas%s, with today's offers. You order in Swiggy." % (
                " for “%s”" % query if query else "")
            link = "/food/" + ("?q=%s" % urllib.parse.quote(query) if query else "")
            label = "See food ideas"
        self.state.log(channel=channel, kind="command", input=text, status="success", message=message,
                       meta={"vendor_id": "swiggy", "food_query": query})
        self.bus.publish("activity", {})
        return {"status": "success", "message": message, "needs_clarification": False, "request_id": None,
                "trajectory_id": None, "scores": None, "changes": [],
                "meta": {"link": link, "link_label": label, "food_query": query}}

    def food_ideas(self, query: str) -> dict:
        if self.swiggy is None:
            raise NotReady("Connect Swiggy (Connections page) to see food ideas and offers.")
        try:
            return self.swiggy.food_ideas(query or "popular")
        except SwiggyFlowError as exc:
            raise NotReady(str(exc)) from exc
        except SwiggyError as exc:
            self.swiggy_failed(exc)
            raise NotReady(str(exc)) from exc

    def food_coupons(self, restaurant_id: str) -> list[dict]:
        if self.swiggy is None:
            raise NotReady("Swiggy is not connected.")
        try:
            return self.swiggy.coupons(restaurant_id)
        except (SwiggyFlowError, SwiggyError) as exc:
            self.swiggy_failed(exc)
            raise NotReady(str(exc)) from exc

    # -- manual cancel -----------------------------------------------------

    def cancel_event(self, event_id: str, channel: str = "app", cancel_reservation: bool = False) -> dict:
        """The user removes an event from the app. With ``cancel_reservation`` and
        a Swiggy table Tripwire booked, the table is cancelled at Swiggy first --
        and if Swiggy refuses, the calendar is left alone so the two still agree."""
        note = self.state.get_value("booking:%s" % event_id, {}) or {}
        prefix = ""
        if cancel_reservation:
            order_id = _swiggy_order(note)
            if not order_id:
                raise ValueError("Tripwire can only cancel reservations it booked on Swiggy.")
            if self.swiggy is None:
                raise NotReady("Connect Swiggy to cancel the reservation there.")
            try:
                self.swiggy.cancel_table(order_id)
            except (SwiggyFlowError, SwiggyError) as exc:
                self.swiggy_failed(exc)
                message = ("Swiggy couldn't cancel it (%s). Your calendar is unchanged; cancel in the Swiggy app "
                           "or call Swiggy on 080-67466729." % exc)
                self.state.log(channel=channel, kind="cancel", input=event_id, status="failure", message=message)
                self.bus.publish("activity", {})
                return {"status": "failure", "message": message, "changes": [], "request_id": None, "meta": {}}
            prefix = "Cancelled the table on Swiggy (order %s). " % order_id
        with self.lock:
            agent = self._require_agent()
            result = self._guard(lambda: agent.remove_event(event_id, self.now()))
            if prefix:
                result.message = prefix + result.message
                result.meta = {"vendor_id": "swiggy"}
            payload = self._record(result, channel=channel, kind="cancel", input=event_id)
        self._after(result, channel)
        return payload

    def swiggy_orders(self) -> list[dict]:
        if self.swiggy is None:
            return []
        try:
            return self.swiggy.active_orders()
        except SwiggyError as exc:
            self.swiggy_failed(exc)
            raise NotReady(str(exc)) from exc

    # -- booking detail ----------------------------------------------------

    def _remember(self, result: AgentResult, changes: list[dict], channel: str) -> None:
        booking = result.decision.booking if result.decision is not None else None
        info = _booking_fields(booking) if booking is not None else {}
        email = {k: result.meta.get(k) for k in ("message_id", "subject", "sender", "stage", "vendor_id")
                 if result.meta.get(k)}
        trust = (result.meta.get("trust") or {}).get("level") if result.meta else None
        stamp = self.now().isoformat(timespec="seconds")
        for change in changes:
            key = "booking:%s" % change["target"]
            note = self.state.get_value(key, {}) or {}
            note.update({k: v for k, v in info.items() if v not in (None, "")})
            if email:
                note["email"] = {**email, "channel": channel, **({"trust": trust} if trust else {})}
            note.setdefault("first_seen", stamp)
            note["deleted"] = change["type"] == "DELETE"
            note["history"] = (note.get("history") or [])[-19:] + [
                {"at": stamp, "type": change["type"], "channel": channel, "message": result.message}
            ]
            self.state.set_value(key, note)

    def event_detail(self, event_id: str) -> dict:
        with self.lock:
            event = next((e for e in self._safe_events() if e.event_id == event_id), None)
            note = self.state.get_value("booking:%s" % event_id, {}) or {}
            if event is None and not note:
                raise LookupError("No such booking")
            extra = self.calendar.details(event_id) if hasattr(self.calendar, "details") else {}
        base = _event_dict(event) if event is not None else None
        kind = (base or {}).get("type") or note.get("booking_type")
        return {
            "event": base,
            "gone": event is None,
            "google": extra or {},
            "booking": {k: v for k, v in note.items() if k not in ("history", "email")},
            "email": note.get("email"),
            "history": list(reversed(note.get("history") or [])),
            "links": _links(base, note, extra or {}, kind),
            #: What "Cancel" can do besides removing the calendar event.
            "can_cancel_reservation": bool(_swiggy_order(note)) and self.swiggy is not None,
        }


def _event_dict(event: CalendarEvent) -> dict:
    kind = event.booking_type or guess_type(event.title)
    return {
        "id": event.event_id,
        "title": event.title,
        "start": event.start_time.isoformat(),
        "end": event.end_time.isoformat() if event.end_time else None,
        "type": kind.value if kind else None,
        #: True when the type was read off the title (an event Gmail or the
        #: user created), not stored by Tripwire.
        "type_guessed": event.booking_type is None and kind is not None,
        "ref": event.booking_ref,
        "location": event.location,
        "alarm": event.alarm.trigger_time.isoformat() if event.alarm and event.alarm.is_active else None,
    }


def _changes(result: AgentResult) -> list[dict]:
    """Calendar writes that actually happened in this interaction."""
    actions = (result.trajectory or {}).get("actions") or []
    return [
        {"type": a["type"], "target": a["target"]}
        for a in actions
        if a.get("type") in ("CREATE", "UPDATE", "DELETE") and a.get("target")
    ]


def _booking_fields(booking) -> dict:
    """A booking's own fields (pickup, party size, ...) as plain JSON values."""
    out = {}
    for f in dataclass_fields(booking):
        value = getattr(booking, f.name)
        if f.name == "booking_id" or value is None:
            continue
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        elif not isinstance(value, (str, int, float, bool)):
            continue
        out[f.name] = value
    return out


def _swiggy_order(note: dict) -> str | None:
    """The Dineout order id, for a table Tripwire booked on Swiggy."""
    email = note.get("email") or {}
    if email.get("vendor_id") == "swiggy" and note.get("booking_type") == "restaurant":
        return note.get("booking_ref")
    return None


def _links(event: dict | None, note: dict, extra: dict, kind: str | None) -> dict:
    """Ways out to the real thing: the calendar entry, the source email, and
    for a ride, Uber with the drop-off already filled in."""
    links: dict = {}
    if extra.get("html_link"):
        links["calendar"] = extra["html_link"]
    email = note.get("email") or {}
    if email.get("message_id") and email.get("channel") == "gmail":
        links["email"] = "https://mail.google.com/mail/u/0/#all/%s" % urllib.parse.quote(email["message_id"])
    elif str(extra.get("source_url") or "").startswith("https://mail.google.com/"):
        links["email"] = extra["source_url"]  # an event Gmail made links back to its email
    if kind == "ride":
        dropoff = note.get("dropoff") or note.get("destination") or (event or {}).get("location")
        params = {"action": "setPickup"}
        if note.get("pickup"):
            params["pickup[formatted_address]"] = note["pickup"]
        else:
            params["pickup"] = "my_location"
        if dropoff:
            params["dropoff[formatted_address]"] = dropoff
        links["uber"] = "https://m.uber.com/ul/?" + urllib.parse.urlencode(params)
    if kind == "restaurant" and email.get("vendor_id") == "swiggy":
        links["swiggy"] = "https://www.swiggy.com/dineout"
    return links


def _ollama_models(host: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:  # noqa: BLE001 -- any failure means "not available"
        return None
