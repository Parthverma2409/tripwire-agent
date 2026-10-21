"""Telegram bot: confirmation prompts with Approve / Decline buttons.

Why Telegram: free, on every phone, and a bot can be driven entirely by long
polling (``getUpdates``) -- so the app needs no public URL, no tunnel and no
webhook, which matters for something that runs on a laptop.

Security, because this is a second door to the FR5 gate:
  * **One chat.** Updates from any chat other than the configured ``chat_id``
    are dropped unread. Anyone can find and message a bot by its username.
  * **Signed buttons.** Button payloads are ``c:<request_id>:<y|n>:<mac>``,
    where ``mac`` is an HMAC of the request id and the choice under a secret
    only the app holds. Telegram clients can be modified to send arbitrary
    callback data; without the MAC, a crafted "y" for any request id would
    be a remote approve button.
  * **No token in errors.** The bot token is part of every API URL. Errors are
    rebuilt without the URL, so the token cannot leak into a log or the UI.

Text sent to the bot that is not a button press is relayed to the agent as a
command, exactly like typing it into the app -- "cancel my Chennai flight" from
the phone opens a confirmation like any other.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

API = "https://api.telegram.org"

#: Telegram's hard limit on callback_data.
MAX_CALLBACK_BYTES = 64
MAC_CHARS = 16


class TelegramError(RuntimeError):
    pass


#: (method, payload) -> decoded "result". Injected in tests.
Transport = Callable[[str, dict], object]


@dataclass
class BotEvent:
    kind: str  # "decision" | "text"
    request_id: str | None = None
    confirmed: bool | None = None
    text: str | None = None
    callback_id: str | None = None
    message_id: int | None = None


@dataclass
class TelegramBot:
    token: str = field(repr=False)
    chat_id: str
    secret: bytes = field(repr=False)
    transport: Transport | None = None
    timeout: float = 35.0
    name: str = "telegram"
    _offset: int = 0

    # -- outbound ----------------------------------------------------------

    def send_text(self, text: str) -> None:
        self._call("sendMessage", {"chat_id": self.chat_id, "text": text[:4000]})

    def send_confirmation(self, request_id: str, prompt: str, detail: str | None = None) -> None:
        text = "Needs your OK\n\n%s" % prompt
        if detail:
            text += "\n\n%s" % detail
        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text[:4000],
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "Approve", "callback_data": self.sign(request_id, True)},
                            {"text": "Decline", "callback_data": self.sign(request_id, False)},
                        ]
                    ]
                },
            },
        )

    def acknowledge(self, event: BotEvent, text: str) -> None:
        """Close the loop: stop the button spinner and replace the buttons."""
        if event.callback_id:
            self._call("answerCallbackQuery", {"callback_query_id": event.callback_id, "text": text[:190]})
        if event.message_id is not None:
            try:
                self._call(
                    "editMessageReplyMarkup",
                    {"chat_id": self.chat_id, "message_id": event.message_id, "reply_markup": {"inline_keyboard": []}},
                )
            except TelegramError:
                pass  # the message may be too old to edit; the answer still stands
        self.send_text(text)

    def get_me(self) -> dict:
        result = self._call("getMe", {})
        return result if isinstance(result, dict) else {}

    # -- inbound -----------------------------------------------------------

    def poll(self, timeout: int = 25) -> list[BotEvent]:
        """One long-poll round. Returns only events from the configured chat."""
        updates = self._call(
            "getUpdates",
            {"offset": self._offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
        )
        events = []
        for update in updates if isinstance(updates, list) else []:
            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
            event = self.parse_update(update)
            if event is not None:
                events.append(event)
        return events

    def parse_update(self, update: dict) -> BotEvent | None:
        callback = update.get("callback_query")
        if callback:
            message = callback.get("message") or {}
            if str((message.get("chat") or {}).get("id")) != str(self.chat_id):
                return None
            verified = self.verify(callback.get("data") or "")
            if verified is None:
                return None
            request_id, confirmed = verified
            return BotEvent(
                "decision", request_id=request_id, confirmed=confirmed,
                callback_id=callback.get("id"), message_id=message.get("message_id"),
            )
        message = update.get("message")
        if message:
            if str((message.get("chat") or {}).get("id")) != str(self.chat_id):
                return None
            text = (message.get("text") or "").strip()
            if not text or text.startswith("/start"):
                return None
            return BotEvent("text", text=text, message_id=message.get("message_id"))
        return None

    # -- signing -----------------------------------------------------------

    def sign(self, request_id: str, confirmed: bool) -> str:
        choice = "y" if confirmed else "n"
        data = "c:%s:%s:%s" % (request_id, choice, self._mac(request_id, choice))
        if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
            raise TelegramError("request id too long for a Telegram button")
        return data

    def verify(self, data: str) -> tuple[str, bool] | None:
        parts = data.split(":")
        if len(parts) != 4 or parts[0] != "c" or parts[2] not in ("y", "n"):
            return None
        _, request_id, choice, mac = parts
        if not hmac.compare_digest(mac, self._mac(request_id, choice)):
            return None
        return request_id, choice == "y"

    def _mac(self, request_id: str, choice: str) -> str:
        digest = hmac.new(self.secret, ("%s:%s" % (request_id, choice)).encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()[:MAC_CHARS]

    # -- transport ---------------------------------------------------------

    def _call(self, method: str, payload: dict):
        if self.transport is not None:
            return self.transport(method, payload)
        request = urllib.request.Request(
            "%s/bot%s/%s" % (API, self.token, method),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                description = json.loads(exc.read().decode("utf-8")).get("description", "")
            except (ValueError, OSError):
                description = ""
            # Rebuilt from scratch: str(exc) would include the URL, and the URL
            # contains the bot token.
            raise TelegramError("Telegram %s failed (%d %s)" % (method, exc.code, description)) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TelegramError("Telegram %s failed (%s)" % (method, type(exc).__name__)) from None
        if not body.get("ok"):
            raise TelegramError("Telegram %s failed (%s)" % (method, body.get("description", "not ok")))
        return body.get("result")
