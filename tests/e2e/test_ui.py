"""Browser end-to-end tests of the app UI, in demo mode.

They drive the real built UI (web/out) against the real FastAPI app in a
background thread, so everything between a click and the calendar is in the
path: the static export, the X-BSync write guard, SSE refresh, the agent and
its FR5 gate.

Skipped unless pytest-playwright is installed (``pip install -e ".[e2e]"``,
then ``python -m playwright install chromium``) and the UI has been built
(``cd web && npm run build``). Run them with:  pytest tests/e2e
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("pytest_playwright")
pytest.importorskip("uvicorn")

import uvicorn  # noqa: E402

from packages.app.config import Settings  # noqa: E402
from packages.app.runtime import Runtime  # noqa: E402
from packages.app.server import WEB_OUT, create_app  # noqa: E402
from packages.app.state import AppState  # noqa: E402

pytestmark = pytest.mark.skipif(not WEB_OUT.exists(), reason="web UI not built (cd web && npm run build)")

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "emails" / "fixtures"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def app_url(tmp_path):
    port = _free_port()
    settings = Settings(port=port, home=tmp_path)
    runtime = Runtime(settings, AppState(settings.db_path))
    app = create_app(settings, runtime=runtime, start_background=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    runtime.scan()  # the demo inbox: bookings plus two questions
    yield "http://127.0.0.1:%d" % port, runtime
    server.should_exit = True
    thread.join(timeout=5)


def test_today_shows_the_demo_questions_and_bookings(page, app_url) -> None:
    url, _ = app_url
    page.goto(url + "/")
    page.get_by_role("heading", name="Needs your OK (2)").wait_for()
    assert page.get_by_role("heading", name="Delete this event?").is_visible()
    assert page.get_by_text("This email failed the sender check.").is_visible()
    assert page.get_by_text("Uber Reserve pickup to Kempegowda Airport").first.is_visible()


def test_typed_command_adds_a_booking(page, app_url) -> None:
    url, runtime = app_url
    page.goto(url + "/")
    page.get_by_label("Tell Tripwire what to do").fill("reserve a table in Bangalore tomorrow at 8:00 pm")
    page.get_by_role("button", name="Send").click()
    page.get_by_text("to your calendar").wait_for()
    assert any(e["type"] == "restaurant" for e in runtime.events())


def test_approving_a_deletion_removes_the_event(page, app_url) -> None:
    url, runtime = app_url
    page.goto(url + "/")
    page.get_by_role("button", name="Delete event").click()
    page.get_by_role("heading", name="Needs your OK (1)").wait_for()
    assert not any(e["title"] == "Flight AI440 BLR to MAA" for e in runtime.events())


def test_declining_keeps_the_event(page, app_url) -> None:
    url, runtime = app_url
    page.goto(url + "/")
    page.get_by_role("button", name="Keep current time").click()
    page.get_by_role("heading", name="Needs your OK (1)").wait_for()
    flight = next(e for e in runtime.events() if e["title"] == "Flight 6E211 MAA to DEL")
    assert flight["start"].endswith("06:00:00")


def test_uploading_a_promotion_changes_nothing(page, app_url) -> None:
    url, runtime = app_url
    before = runtime.events()
    page.goto(url + "/inbox/")
    page.get_by_label("Choose .eml files to test").set_input_files(str(FIXTURES / "promo_fare_sale.eml"))
    page.get_by_text("it’s a promotion").first.wait_for()
    assert runtime.events() == before


def test_a_booking_opens_its_detail_page(page, app_url) -> None:
    url, _ = app_url
    page.goto(url + "/bookings/")
    page.get_by_role("link", name="Uber Reserve pickup to Kempegowda Airport").first.click()
    page.get_by_role("heading", level=1, name="Uber Reserve pickup to Kempegowda Airport").wait_for()
    assert page.get_by_role("link", name="Book this ride in Uber").is_visible()
    assert page.get_by_role("heading", name="History").is_visible()
