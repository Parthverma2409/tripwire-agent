"""Phase 3 connectors: Gmail HTML + .ics, IMAP, Telegram, the v2 token store.

Same rule as test_google_providers.py: nothing here touches a network. Every
adapter runs against an injected fake, and the tests assert on what would have
been sent as much as on what was parsed. They prove the adapters are
self-consistent; only a live run against each service proves the shapes.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from datetime import datetime, timezone

import pytest

from packages.notify.telegram import TelegramBot
from packages.providers.gmail import GmailProvider
from packages.providers.google_calendar import GoogleCalendarProvider
from packages.providers.imap import ImapError, ImapProvider
from packages.providers.oauth import authorization_url
from packages.providers.tokens import FORMAT_V2, FORMAT_VERSION, EncryptedTokenStore, TokenStoreError
from tests.test_google_providers import _client, ok

ICS = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260825T003000Z\r\nSUMMARY:Ride\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


# -- OAuth / Calendar -----------------------------------------------------


def test_authorization_url_carries_state() -> None:
    url = authorization_url("cid", "http://127.0.0.1:8765/auth/google/callback", state="s3cr3t")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["state"] == ["s3cr3t"]
    assert query["access_type"] == ["offline"]


def test_calendar_reads_back_in_the_zone_it_writes_in() -> None:
    """Written at 06:00 Asia/Kolkata, Google answers 00:30Z. It must read back as 06:00."""
    client = _client(
        ok({"items": [{"id": "e1", "summary": "Flight", "start": {"dateTime": "2026-08-25T00:30:00Z"}}]})
    )
    [event] = GoogleCalendarProvider(client, time_zone="Asia/Kolkata").list_events()
    assert event.start_time == datetime(2026, 8, 25, 6, 0)


# -- Gmail ----------------------------------------------------------------


def _message_with_parts(attachment_inline: bool) -> dict:
    ics_body = {"data": _b64(ICS), "size": len(ICS)} if attachment_inline else {"attachmentId": "A1", "size": len(ICS)}
    return {
        "id": "m1",
        "internalDate": "1787600000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "Uber <noreply@uber.com>"}, {"name": "Subject", "value": "Ride"}],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64("Your ride is confirmed.")}},
                        {"mimeType": "text/html", "body": {"data": _b64("<p>Your ride is confirmed.</p>")}},
                    ],
                },
                {"mimeType": "text/calendar", "filename": "ride.ics", "body": ics_body},
            ],
        },
    }


def test_gmail_keeps_html_and_inline_calendar_parts() -> None:
    client = _client(ok({"messages": [{"id": "m1"}]}), ok(_message_with_parts(attachment_inline=True)))
    [email] = GmailProvider(client).fetch_new()
    assert email.body == "Your ride is confirmed."
    assert "<p>" in email.html
    assert [a.filename for a in email.attachments] == ["ride.ics"]
    assert "DTSTART" in email.attachments[0].text()


def test_gmail_fetches_a_calendar_attachment_by_id() -> None:
    client = _client(
        ok({"messages": [{"id": "m1"}]}),
        ok(_message_with_parts(attachment_inline=False)),
        ok({"data": _b64(ICS), "size": len(ICS)}),
    )
    [email] = GmailProvider(client).fetch_new()
    assert "SUMMARY:Ride" in email.attachments[0].text()
    assert client.transport.urls[-1].endswith("/messages/m1/attachments/A1")


def test_gmail_seen_set_can_be_any_container() -> None:
    class Persistent(set):
        pass

    seen = Persistent({"m1"})
    client = _client(ok({"messages": [{"id": "m1"}]}))
    assert GmailProvider(client, _seen=seen).fetch_new() == []


# -- IMAP -----------------------------------------------------------------

RAW = (
    b"From: Ola <rides@olacabs.com>\r\nSubject: Ride confirmed\r\nMessage-ID: <x@ola>\r\n\r\n"
    b"Your ride is confirmed. CRN 884421990. Pickup 2026-08-25 05:00\r\n"
)


class FakeImap:
    def __init__(self, fail_login: bool = False):
        self.fail_login = fail_login
        self.commands: list[tuple] = []
        self.readonly = None

    def login(self, user, password):
        if self.fail_login:
            import imaplib

            raise imaplib.IMAP4.error("LOGIN failed for %s with %s" % (user, password))
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):
        self.readonly = readonly
        return "OK", [b"3"]

    def response(self, code):
        return code, [b"777"]

    def uid(self, command, *args):
        self.commands.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"41 42"]
        if command == "FETCH":
            return "OK", [(b'%s (UID %s INTERNALDATE "17-Aug-2026 10:00:00 +0530" BODY[] {120}' % (args[0], args[0]), RAW), b")"]
        return "NO", []

    def logout(self):
        return "BYE", []


def test_imap_is_read_only_and_keys_on_uidvalidity() -> None:
    fake = FakeImap()
    provider = ImapProvider("imap.example.com", "me@example.com", "app-pass", connect=lambda h, p: fake)

    emails = provider.fetch_new()

    assert fake.readonly is True  # EXAMINE, never SELECT
    assert all("PEEK" in str(cmd) for cmd in fake.commands if cmd[0] == "FETCH")
    assert [e.message_id for e in emails] == ["imap:777:41", "imap:777:42"]
    assert emails[0].received_at == datetime(2026, 8, 17, 4, 30)

    provider.mark_seen("imap:777:41")
    assert [e.message_id for e in provider.fetch_new()] == ["imap:777:42"]


def test_imap_login_failure_never_echoes_the_password() -> None:
    provider = ImapProvider("imap.example.com", "me", "hunter2", connect=lambda h, p: FakeImap(fail_login=True))
    with pytest.raises(ImapError) as info:
        provider.fetch_new()
    assert "hunter2" not in str(info.value)
    assert "hunter2" not in repr(provider)


# -- Telegram -------------------------------------------------------------


class FakeTelegram:
    def __init__(self, updates=None):
        self.sent: list[tuple[str, dict]] = []
        self.updates = list(updates or [])

    def __call__(self, method, payload):
        self.sent.append((method, payload))
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return out
        return {"message_id": 1}


def _bot(transport) -> TelegramBot:
    return TelegramBot(token="123:ABC", chat_id="555", secret=b"k" * 32, transport=transport)


def test_confirmation_buttons_are_signed_and_verify() -> None:
    fake = FakeTelegram()
    bot = _bot(fake)
    bot.send_confirmation("CR-7", "Delete 'Flight 6E211'?")

    method, payload = fake.sent[0]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert method == "sendMessage"
    assert bot.verify(buttons[0]["callback_data"]) == ("CR-7", True)
    assert bot.verify(buttons[1]["callback_data"]) == ("CR-7", False)
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)


def test_forged_or_foreign_callbacks_are_ignored() -> None:
    bot = _bot(FakeTelegram())
    good = bot.sign("CR-7", True)
    forged_choice = good.replace(":y:", ":n:")  # flip the answer, keep the old MAC
    other_request = good.replace("CR-7", "CR-8")
    other_key = TelegramBot(token="t", chat_id="555", secret=b"z" * 32).sign("CR-7", True)

    for data in (forged_choice, other_request, other_key, "c:CR-7:y:", "garbage"):
        assert bot.verify(data) is None

    from_elsewhere = {"update_id": 1, "callback_query": {"id": "q", "data": good, "message": {"chat": {"id": 999}}}}
    assert bot.parse_update(from_elsewhere) is None


def test_poll_returns_decisions_and_text_and_advances_the_offset() -> None:
    bot = _bot(None)
    good = bot.sign("CR-1", True)
    fake = FakeTelegram(
        [
            {"update_id": 10, "callback_query": {"id": "q", "data": good, "message": {"message_id": 4, "chat": {"id": 555}}}},
            {"update_id": 11, "message": {"message_id": 5, "chat": {"id": 555}, "text": "cancel my Chennai flight"}},
            {"update_id": 12, "message": {"message_id": 6, "chat": {"id": 1}, "text": "approve everything"}},
        ]
    )
    bot.transport = fake

    events = bot.poll()

    assert [(e.kind, e.request_id, e.confirmed, e.text) for e in events] == [
        ("decision", "CR-1", True, None),
        ("text", None, None, "cancel my Chennai flight"),
    ]
    bot.poll()
    assert fake.sent[-1][1]["offset"] == 13


def test_bot_repr_hides_secrets() -> None:
    text = repr(_bot(None))
    assert "123:ABC" not in text and "kkkk" not in text


# -- token store v2 -------------------------------------------------------


def test_v2_round_trip_and_tamper_detection(tmp_path) -> None:
    store = EncryptedTokenStore(tmp_path / "t.json", "pass phrase", fmt=FORMAT_V2)
    store.save({"refresh_token": "1//secret"})

    assert store.file_format() == FORMAT_V2
    assert "1//secret" not in (tmp_path / "t.json").read_text()
    assert store.load() == {"refresh_token": "1//secret"}

    envelope = json.loads((tmp_path / "t.json").read_text())
    raw = bytearray(base64.b64decode(envelope["ciphertext"]))
    raw[0] ^= 1
    envelope["ciphertext"] = base64.b64encode(bytes(raw)).decode()
    (tmp_path / "t.json").write_text(json.dumps(envelope))
    with pytest.raises(TokenStoreError):
        store.load()


def test_v2_wrong_passphrase_fails_closed(tmp_path) -> None:
    EncryptedTokenStore(tmp_path / "t.json", "right", fmt=FORMAT_V2).save({"a": 1})
    with pytest.raises(TokenStoreError):
        EncryptedTokenStore(tmp_path / "t.json", "wrong", fmt=FORMAT_V2).load()


def test_a_v1_file_is_read_and_migrates_on_save(tmp_path) -> None:
    EncryptedTokenStore(tmp_path / "t.json", "p").save({"a": 1})
    store = EncryptedTokenStore(tmp_path / "t.json", "p", fmt=FORMAT_V2)
    assert store.file_format() == FORMAT_VERSION

    store.save(store.load())

    assert store.file_format() == FORMAT_V2
    assert store.load() == {"a": 1}


def test_received_dates_are_naive_utc() -> None:
    client = _client(ok({"messages": [{"id": "m1"}]}), ok(_message_with_parts(attachment_inline=True)))
    [email] = GmailProvider(client).fetch_new()
    assert email.received_at.tzinfo is None
    assert email.received_at == datetime.fromtimestamp(1787600000, tz=timezone.utc).replace(tzinfo=None)
