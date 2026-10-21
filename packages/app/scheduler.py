"""Background loops, run on the server's event loop.

Each loop does blocking work (HTTP to Google, IMAP, Telegram long-polls) in a
worker thread via ``asyncio.to_thread``, so a slow mail server never stalls the
API. Each loop survives its own failures: an exception is recorded on the
Connections page and the loop backs off and tries again, because a network blip
at 3am must not silently end inbox polling until someone restarts the app.
"""

from __future__ import annotations

import asyncio
import contextlib

from packages.notify.telegram import TelegramError

from .runtime import NotReady, Runtime

TICK_SEC = 15
ERROR_BACKOFF_SEC = 30
#: Swiggy order states move in minutes; table bookings far slower.
SWIGGY_SEC = 120


class Scheduler:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime
        self._tasks: list[asyncio.Task] = []
        self._wake_inbox = asyncio.Event()

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._inbox_loop(), name="inbox"),
            asyncio.create_task(self._tick_loop(), name="tick"),
            asyncio.create_task(self._telegram_loop(), name="telegram"),
            asyncio.create_task(self._swiggy_loop(), name="swiggy"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def poke_inbox(self) -> None:
        """Scan now rather than at the next interval (after a rebuild, say)."""
        self._wake_inbox.set()

    async def _inbox_loop(self) -> None:
        while True:
            try:
                if self.runtime.mail is not None and self.runtime.agent is not None:
                    await asyncio.to_thread(self.runtime.scan)
            except NotReady:
                pass
            except Exception:  # noqa: BLE001 -- recorded in status by Runtime.scan
                await asyncio.sleep(ERROR_BACKOFF_SEC)
            minutes = max(1, int(self.runtime.settings.poll_minutes or 5))
            self._wake_inbox.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_inbox.wait(), timeout=minutes * 60)

    async def _tick_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self.runtime.tick)
            await asyncio.sleep(TICK_SEC)

    async def _telegram_loop(self) -> None:
        while True:
            bot = self.runtime.bot
            if bot is None:
                await asyncio.sleep(TICK_SEC)
                continue
            try:
                events = await asyncio.to_thread(bot.poll, 25)
                if not self.runtime.status.telegram.get("ok"):
                    # Reachable again after an outage: say so on the Connections page.
                    self.runtime.status.telegram = {
                        "ok": True, "detail": "Approvals go to chat %s" % self.runtime.settings.telegram_chat_id,
                    }
                for event in events:
                    await asyncio.to_thread(self.runtime.handle_bot_event, event)
            except TelegramError as exc:
                self.runtime.status.telegram = {"ok": False, "detail": str(exc)}
                await asyncio.sleep(ERROR_BACKOFF_SEC)
            except Exception:  # noqa: BLE001 -- keep the loop alive
                await asyncio.sleep(ERROR_BACKOFF_SEC)

    async def _swiggy_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):  # Runtime records Swiggy failures in status
                await asyncio.to_thread(self.runtime.swiggy_poll)
            await asyncio.sleep(SWIGGY_SEC)
