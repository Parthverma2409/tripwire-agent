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
     question did not come from someone already looking at the app.

It never decides anything about a booking. Every path into the calendar goes
through ``SyncAgent``; every destructive path through its confirmation gate.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable

from packages.core.confirmation import ConfirmationStatus
from packages.core.decisions import ActionType
from packages.core.models import CalendarEvent
from packages.eval_ingest.pipeline import IngestPipeline
from packages.nlp.baseline import BaselineExtractor
from packages.nlp.extractor import FallbackExtractor, OllamaExtractor
from packages.nlp.zones import zone_info
from packages.notify.telegram import BotEvent, TelegramBot, TelegramError
from packages.providers.calendar import CalendarError, CalendarProvider
from packages.providers.eml import parse_eml
from packages.providers.mail import MailProvider, RawEmail
from packages.sync_agent.activation import ActivationWindow
from packages.sync_agent.agent import AgentResult, SyncAgent

from .config import Settings
from .state import AppState


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


class Runtime:
    def __init__(
        self,
        settings: Settings,
        state: AppState,
        clock: Callable[[], datetime] | None = None,
        google=None,
        telegram_transport=None,
    ):
        self.settings = settings
        self.state = state
        self.bus = EventBus()
        self.pipeline = IngestPipeline(store=state)
        self.lock = threading.RLock()
        self._clock = clock
        #: GoogleAuth (google_auth.py); None in demo-only setups and tests.
        self.google = google
        self._telegram_transport = telegram_transport
        self.calendar: CalendarProvider | None = None
        self.mail: MailProvider | None = None
        self.mail_channel: str = "none"
        self.agent: SyncAgent | None = None
        self.bot: TelegramBot | None = None
        self.status = ConnectionStatus()
        #: request_id -> what the app shows beside a pending question.
        self._request_meta: dict[str, dict] = {}
        self._announced_expiry: set[str] = set()
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
            self.status = ConnectionStatus()
            self._request_meta.clear()
            self._announced_expiry.clear()
            if self.settings.mode == "demo":
                self._build_demo()
            else:
                self._build_live()
            self.bot = self._build_telegram()
            extractor, fallback = self._build_extractors()
            self.status.voice = {"ok": True, "detail": "Browser speech recognition (Chrome / Edge)"}
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
                )
        self.bus.publish("rebuilt", self.status_dict())

    def _apply_saved_settings(self) -> None:
        overrides = self.state.get_value("settings", {}) or {}
        for key in ("zone", "active_window", "poll_minutes", "alarm_minutes", "mode"):
            if key in overrides and overrides[key] not in (None, ""):
                setattr(self.settings, key, overrides[key])

    def _build_demo(self) -> None:
        from .demo import demo_calendar, demo_inbox

        now = self.now()
        self.calendar = demo_calendar(now)
        inbox = demo_inbox(now)
        seen = self.state.seen_set()
        # The demo inbox is rebuilt on every start; mail seen in an earlier run
        # stays seen, so a restart does not re-ask the demo's questions.
        inbox._seen = seen  # FakeInbox keeps a plain set; any container works
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

    def _require_agent(self) -> SyncAgent:
        if self.agent is None:
            raise NotReady("Connect a calendar first (Connections page).")
        return self.agent

    # -- interactions ------------------------------------------------------

    def command(self, text: str, channel: str = "app") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("Say or type something first.")
        with self.lock:
            agent = self._require_agent()
            result = self._guard(lambda: agent.handle_command(text, self.now()))
            payload = self._record(result, channel=channel, kind="command", input=text)
        self._after(result, channel)
        return payload

    def resolve(self, request_id: str, approve: bool, channel: str = "app") -> dict:
        with self.lock:
            agent = self._require_agent()
            result = self._guard(lambda: agent.resolve_confirmation(request_id, approve, self.now()))
            meta = self._request_meta.pop(request_id, {})
            result.meta = {**meta, "request_id": request_id, "approved": approve}
            payload = self._record(result, channel=channel, kind="decision", input="%s %s" % ("approve" if approve else "decline", request_id))
        self.bus.publish("calendar", {})
        self.bus.publish("confirmations", {})
        return payload

    def ingest_email(self, email: RawEmail, channel: str) -> list[dict]:
        with self.lock:
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
        if self.mail is None:
            raise NotReady("No inbox connected.")
        self._require_agent()
        try:
            emails = self.mail.fetch_new()
        except Exception as exc:  # noqa: BLE001 -- shown to the user, retried next poll
            self.status.mail = {**self.status.mail, "ok": False, "detail": "Last scan failed: %s" % exc}
            self.bus.publish("status", self.status_dict())
            raise
        handled = 0
        for email in emails:
            self.ingest_email(email, channel=self.mail_channel)
            self.mail.mark_seen(email.message_id)
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
        self.state.log(
            channel=channel, kind=kind, input=input, status=result.status, message=result.message,
            meta={k: v for k, v in result.meta.items() if k != "booking"} or None,
            trajectory_id=trajectory_id, request_id=request_id,
        )
        return {
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


def _event_dict(event: CalendarEvent) -> dict:
    return {
        "id": event.event_id,
        "title": event.title,
        "start": event.start_time.isoformat(),
        "end": event.end_time.isoformat() if event.end_time else None,
        "type": event.booking_type.value if event.booking_type else None,
        "ref": event.booking_ref,
        "location": event.location,
        "alarm": event.alarm.trigger_time.isoformat() if event.alarm and event.alarm.is_active else None,
    }


def _ollama_models(host: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", [])]
    except Exception:  # noqa: BLE001 -- any failure means "not available"
        return None
