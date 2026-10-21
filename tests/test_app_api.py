"""The local app's HTTP API, end to end, in-process.

Skipped when the ``[app]`` extra is not installed, so the core suite still runs
with PyYAML alone. Time is a controllable clock, so confirmation expiry is
tested by moving it, not by sleeping.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from packages.app.config import Settings, load_settings  # noqa: E402
from packages.app.google_auth import GoogleAuth  # noqa: E402
from packages.app.runtime import Runtime  # noqa: E402
from packages.app.server import create_app  # noqa: E402
from packages.app.state import AppState  # noqa: E402
from packages.providers.oauth import HttpResponse  # noqa: E402
from packages.providers.tokens import FORMAT_V2, EncryptedTokenStore  # noqa: E402
from tests.test_connectors import FakeTelegram  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "emails" / "fixtures"
H = {"X-BSync": "1"}


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 10, 21, 10, 0))


def _client(clock: Clock, settings: Settings | None = None, **runtime_kwargs) -> TestClient:
    settings = settings or Settings()
    runtime = Runtime(settings, AppState(":memory:"), clock=clock, **runtime_kwargs)
    return TestClient(create_app(settings, runtime=runtime, start_background=False))


# -- local-only guards ----------------------------------------------------


def test_writes_need_the_custom_header(clock) -> None:
    client = _client(clock)
    assert client.post("/api/command", json={"text": "book a cab tomorrow at 7 am"}).status_code == 403
    assert client.post("/api/command", json={"text": "book a cab tomorrow at 7 am"}, headers=H).status_code == 200


def test_cross_origin_writes_are_refused_even_with_the_header(clock) -> None:
    client = _client(clock)
    response = client.post("/api/scan", headers={**H, "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_foreign_host_header_is_refused(clock) -> None:
    """DNS rebinding: evil.example resolving to 127.0.0.1 still says evil.example."""
    client = _client(clock)
    assert client.get("/api/status", headers={"Host": "evil.example:8765"}).status_code == 421
    assert client.get("/api/status", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_status_never_contains_secrets(clock) -> None:
    settings = Settings(google_client_id="cid", google_client_secret="SUPERSECRET",
                        telegram_token="1:TOKENVALUE", imap_password="IMAPPASS")
    body = json.dumps(_client(clock, settings).get("/api/status").json())
    for secret in ("SUPERSECRET", "TOKENVALUE", "IMAPPASS"):
        assert secret not in body
    assert '"google_client_secret_set": true' in body


def test_settings_refuse_non_loopback_hosts() -> None:
    with pytest.raises(ValueError):
        load_settings(env_file=Path("does-not-exist"), environ={"BSYNC_HOST": "0.0.0.0"})


# -- demo flow ------------------------------------------------------------


def test_demo_scan_fills_the_calendar_and_raises_two_questions(clock) -> None:
    client = _client(clock)
    assert client.post("/api/scan", headers=H).json()["handled"] == 8

    titles = [e["title"] for e in client.get("/api/events").json()["events"]]
    assert "Flight 6E211 MAA to DEL" in titles and "Table at Toit" in titles

    questions = client.get("/api/confirmations").json()["confirmations"]
    assert [q["action"] for q in questions] == ["DELETE", "UPDATE"]
    forged = questions[1]
    assert forged["meta"]["trust"]["level"] == "failed"
    assert forged["before"]["title"] == "Flight 6E211 MAA to DEL"
    assert forged["after"]["start"] < forged["before"]["start"]

    quarantined = client.get("/api/inbox?quarantine=true").json()["mail"]
    assert [m["subject"] for m in quarantined] == ["Your itinerary is attached"]


def test_a_second_scan_reads_nothing_new(clock) -> None:
    client = _client(clock)
    client.post("/api/scan", headers=H)
    assert client.post("/api/scan", headers=H).json()["handled"] == 0


def test_approve_deletes_and_decline_keeps(clock) -> None:
    client = _client(clock)
    client.post("/api/scan", headers=H)
    delete_q, update_q = client.get("/api/confirmations").json()["confirmations"]

    declined = client.post("/api/confirmations/%s" % update_q["request_id"], json={"approve": False}, headers=H).json()
    approved = client.post("/api/confirmations/%s" % delete_q["request_id"], json={"approve": True}, headers=H).json()

    assert declined["status"] == "refused"
    assert approved["status"] == "success"
    titles = [e["title"] for e in client.get("/api/events").json()["events"]]
    assert "Flight AI440 BLR to MAA" not in titles
    assert "Flight 6E211 MAA to DEL" in titles
    assert client.get("/api/confirmations").json()["confirmations"] == []


def test_an_unanswered_question_times_out_and_changes_nothing(clock) -> None:
    client = _client(clock)
    client.post("/api/scan", headers=H)
    runtime = client.app.state.runtime
    before = len(client.get("/api/events").json()["events"])

    clock.now += timedelta(minutes=16)
    runtime.tick()

    assert client.get("/api/confirmations").json()["confirmations"] == []
    assert len(client.get("/api/events").json()["events"]) == before
    assert any(a["kind"] == "timeout" for a in client.get("/api/activity").json()["activity"])
    # A late "yes" for the expired question is not consent.
    late = client.post("/api/confirmations/CR-1", json={"approve": True}, headers=H).json()
    assert late["status"] == "refused"


def test_every_interaction_is_scored(clock) -> None:
    client = _client(clock)
    client.post("/api/command", json={"text": "book a table in Bangalore on 25 October at 8:00 pm"}, headers=H)
    [trajectory] = client.get("/api/trajectories").json()["trajectories"]
    assert set(trajectory["scores"]) >= {"task_success", "safety_compliance"}
    assert client.get("/api/trajectories/%s" % trajectory["trajectory_id"]).status_code == 200


def test_upload_an_eml(clock) -> None:
    client = _client(clock)
    raw = (FIXTURES / "vendors" / "swiggy_dineout_microdata.eml").read_bytes()
    response = client.post("/api/emails/upload", files={"file": ("x.eml", raw, "message/rfc822")}, headers=H)
    [result] = response.json()["results"]
    assert result["meta"]["stage"] == "schema.org"
    assert result["status"] == "success"


def test_upload_rejects_non_email(clock) -> None:
    client = _client(clock)
    response = client.post("/api/emails/upload", files={"file": ("x.eml", b"", "message/rfc822")}, headers=H)
    assert response.status_code == 422


def test_settings_validate_and_apply(clock) -> None:
    client = _client(clock)
    assert client.post("/api/settings", json={"zone": "Mars/Olympus"}, headers=H).status_code == 422
    assert client.post("/api/settings", json={"active_window": "nonsense"}, headers=H).status_code == 422
    body = client.post("/api/settings", json={"active_window": "07:00-23:00", "poll_minutes": 10}, headers=H).json()
    assert body["settings"]["active_window"] == "07:00-23:00"
    assert body["settings"]["poll_minutes"] == 10


def test_live_mode_without_accounts_reports_what_to_do(clock) -> None:
    client = _client(clock, Settings(mode="live"))
    status = client.get("/api/status").json()
    assert status["ready"] is False
    assert "GOOGLE_CLIENT_ID" in status["connections"]["calendar"]["detail"]
    assert client.post("/api/command", json={"text": "book a cab tomorrow at 7 am"}, headers=H).status_code == 409


# -- Telegram relay -------------------------------------------------------


def _telegram_settings() -> Settings:
    return Settings(telegram_token="1:T", telegram_chat_id="555", token_passphrase="test-passphrase")


def test_email_questions_go_to_telegram_and_app_ones_do_not(clock) -> None:
    fake = FakeTelegram()
    client = _client(clock, _telegram_settings(), telegram_transport=fake)
    client.post("/api/scan", headers=H)
    sent = [p for m, p in fake.sent if m == "sendMessage" and "reply_markup" in p]
    assert len(sent) == 2  # the two demo questions, both from email
    assert "failed authentication" in sent[1]["text"]

    client.post("/api/command", json={"text": "cancel the Bangalore trip"}, headers=H)
    assert len([p for m, p in fake.sent if m == "sendMessage" and "reply_markup" in p]) == 2


def test_an_unreachable_telegram_is_reported_not_a_500(clock) -> None:
    from packages.notify.telegram import TelegramError

    def offline(method, payload):
        raise TelegramError("Telegram unreachable: timed out")

    client = _client(clock, _telegram_settings(), telegram_transport=offline)
    response = client.post("/api/connections/test/telegram", headers=H)
    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "Couldn't reach Telegram: Telegram unreachable: timed out"}


def test_a_telegram_button_resolves_the_question(clock) -> None:
    fake = FakeTelegram()
    client = _client(clock, _telegram_settings(), telegram_transport=fake)
    client.post("/api/scan", headers=H)
    runtime = client.app.state.runtime
    button = [p for m, p in fake.sent if "reply_markup" in p][0]["reply_markup"]["inline_keyboard"][0][0]

    [event] = [runtime.bot.parse_update({"update_id": 1, "callback_query": {
        "id": "q1", "data": button["callback_data"], "message": {"message_id": 9, "chat": {"id": 555}}}})]
    runtime.handle_bot_event(event)

    titles = [e["title"] for e in client.get("/api/events").json()["events"]]
    assert "Flight AI440 BLR to MAA" not in titles
    assert any(a["channel"] == "telegram" for a in client.get("/api/activity").json()["activity"])


# -- Google OAuth ---------------------------------------------------------


class ScriptedGoogle:
    """Token endpoint and Calendar/Gmail, as the adapters would see them."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url))
        if url.startswith("https://oauth2.googleapis.com/token"):
            return HttpResponse(200, json.dumps(
                {"access_token": "ya29.x", "refresh_token": "1//r", "expires_in": 3600}).encode())
        if "/calendar/v3/" in url:
            return HttpResponse(200, json.dumps({"items": []}).encode())
        if "gmail.googleapis.com" in url:
            return HttpResponse(200, json.dumps({"messages": []}).encode())
        raise AssertionError("unexpected call %s %s" % (method, url))


def _google_settings(tmp_path) -> Settings:
    return Settings(mode="demo", home=tmp_path, google_client_id="cid.apps.googleusercontent.com",
                    google_client_secret="sec", token_passphrase="pp")


def test_callback_without_our_state_is_refused_before_any_token_call(clock, tmp_path) -> None:
    settings = _google_settings(tmp_path)
    google = ScriptedGoogle()
    client = _client(clock, settings, google=GoogleAuth(settings, transport=google))

    response = client.get("/auth/google/callback?code=attacker-code&state=forged", follow_redirects=False)

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert google.calls == []
    assert not settings.token_path.exists()


def test_full_google_connect_flow(clock, tmp_path) -> None:
    settings = _google_settings(tmp_path)
    google = ScriptedGoogle()
    client = _client(clock, settings, google=GoogleAuth(settings, transport=google))

    start = client.get("/auth/google/start", follow_redirects=False)
    assert start.status_code == 303
    location = start.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    state = location.split("state=")[1].split("&")[0]

    done = client.get("/auth/google/callback?code=good&state=%s" % state, follow_redirects=False)
    assert done.headers["location"] == "/connections/?connected=google"

    store = EncryptedTokenStore(settings.token_path, "pp")
    assert store.file_format() == FORMAT_V2
    assert store.load()["refresh_token"] == "1//r"

    status = client.get("/api/status").json()
    assert status["mode"] == "live"
    assert status["connections"]["calendar"]["provider"] == "google"
    assert status["mail_channel"] == "gmail"
    assert client.get("/api/events").json()["events"] == []

    # The state is single-use.
    again = client.get("/auth/google/callback?code=good&state=%s" % state, follow_redirects=False)
    assert "error=" in again.headers["location"]


# -- local voice ------------------------------------------------------------


class _ScriptedTranscriber:
    name = "scripted"

    def __init__(self, text: str, confidence: float | None):
        self.text, self.confidence = text, confidence

    def transcribe(self, audio):
        from packages.nlp.asr import Transcript

        return Transcript(self.text, self.confidence, model=self.name)


def _voice(client: TestClient):
    return client.post("/api/voice", files={"file": ("a.webm", b"\x1aE\xdf\xa3fake", "audio/webm")}, headers=H)


def test_voice_is_off_until_whisper_is_configured(clock) -> None:
    assert _voice(_client(clock)).status_code == 409


def test_a_clear_recording_runs_as_a_command(clock) -> None:
    client = _client(clock)
    client.app.state.runtime.transcriber = _ScriptedTranscriber("book a cab to Chennai tomorrow at 7 am", 0.9)
    body = _voice(client).json()
    assert body["status"] == "success"
    assert body["transcript"].startswith("book a cab")


def test_a_mumbled_recording_is_quoted_back_not_acted_on(clock) -> None:
    client = _client(clock)
    runtime = client.app.state.runtime
    runtime.transcriber = _ScriptedTranscriber("cancel my flight", 0.2)
    before = len(runtime.events())
    body = _voice(client).json()
    assert body["status"] == "refused"
    assert "not sure" in body["message"]
    assert len(runtime.events()) == before
    assert client.get("/api/activity").json()["activity"] == []


def test_demo_restarts_cleanly_and_settings_survive(clock, tmp_path) -> None:
    """Regression: demo mail used to stay 'seen' on disk while the demo calendar
    reset, so a restarted demo showed no bookings and no questions."""
    store = AppState(tmp_path / "app.db")
    first = Runtime(Settings(), store, clock=clock)
    assert first.scan()["handled"] == 8
    store.set_value("settings", {"poll_minutes": 15})

    second = Runtime(Settings(), store, clock=clock)

    assert second.scan()["handled"] == 8
    assert len(second.confirmations()) == 2
    assert second.settings.poll_minutes == 15
    assert store.activity() == []  # demo noise never reaches the persistent history
