"""HTTP: the JSON API, server-sent events, the OAuth redirect, and the UI.

The API has no login, so it is only ever bound to loopback (config.py refuses
anything else). Loopback is not enough on its own, because a web page on any
site can still make the user's browser send requests to 127.0.0.1. Two checks
close that:

  * **Host allow-list.** Every request must name 127.0.0.1 / localhost on our
    port in its Host header. That defeats DNS rebinding, where evil.example is
    re-pointed at 127.0.0.1 and then reads our responses as same-origin.
  * **Write guard.** Every state-changing request (POST/PUT/PATCH/DELETE) must
    carry ``X-BSync: 1``, and if it carries an Origin it must be ours. A
    cross-site form or ``fetch`` cannot add a custom header without a CORS
    preflight, and there is no CORS policy here to grant one.

Without these, any page the user visits could approve a pending deletion.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, Settings, load_settings
from .google_auth import AuthFlowError, GoogleAuth
from .runtime import NotReady, Runtime
from .scheduler import Scheduler
from .state import AppState

WEB_OUT = ROOT / "web" / "out"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
#: Next.js dev server, allowed only when BSYNC_DEV_ORIGIN names it explicitly.
DEV_ORIGIN_ENV = "BSYNC_DEV_ORIGIN"


class CommandIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class DecisionIn(BaseModel):
    approve: bool


class SettingsIn(BaseModel):
    zone: str | None = Field(default=None, max_length=64)
    active_window: str | None = Field(default=None, max_length=11)
    poll_minutes: int | None = Field(default=None, ge=1, le=240)
    alarm_minutes: int | None = Field(default=None, ge=0, le=1440)
    mode: str | None = Field(default=None, pattern="^(demo|live)$")


def create_app(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    start_background: bool = True,
    dev_origin: str | None = None,
) -> FastAPI:
    import os

    settings = settings or load_settings()
    if runtime is None:
        state = AppState(settings.db_path)
        runtime = Runtime(settings, state, google=GoogleAuth(settings))
    scheduler = Scheduler(runtime)
    allowed_hosts = {"127.0.0.1:%d" % settings.port, "localhost:%d" % settings.port, "testserver"}
    allowed_origins = {"http://127.0.0.1:%d" % settings.port, "http://localhost:%d" % settings.port}
    dev_origin = dev_origin or os.environ.get(DEV_ORIGIN_ENV)
    if dev_origin:
        allowed_origins.add(dev_origin.rstrip("/"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_background:
            scheduler.start()
        yield
        if start_background:
            await scheduler.stop()

    app = FastAPI(title="Tripwire", version="1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.runtime = runtime
    app.state.scheduler = scheduler

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.headers.get("host") or "").lower()
        if host not in allowed_hosts:
            return JSONResponse({"detail": "Host not allowed"}, status_code=421)
        if request.method in WRITE_METHODS:
            origin = request.headers.get("origin")
            if request.headers.get("x-bsync") != "1" or (origin and origin.rstrip("/") not in allowed_origins):
                return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def run(fn, *args):
        """Map domain errors to HTTP. Agent refusals are 200s with a message."""
        try:
            return fn(*args)
        except NotReady as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    # -- reads -------------------------------------------------------------

    @app.get("/api/status")
    def status():
        return runtime.status_dict()

    @app.get("/api/events")
    def events():
        return {"events": run(runtime.events)}

    @app.get("/api/confirmations")
    def confirmations():
        return {"confirmations": runtime.confirmations()}

    @app.get("/api/inbox")
    def inbox(quarantine: bool = False, limit: int = 200):
        return {"mail": runtime.state.mail(limit=min(limit, 500), quarantine_only=quarantine)}

    @app.get("/api/activity")
    def activity(limit: int = 100):
        return {"activity": runtime.state.activity(limit=min(limit, 500))}

    @app.get("/api/trajectories")
    def trajectories(limit: int = 50):
        return {"trajectories": runtime.state.recent_trajectories(limit=min(limit, 200))}

    @app.get("/api/trajectories/{trajectory_id}")
    def trajectory(trajectory_id: str):
        found = runtime.state.get(trajectory_id)
        if found is None:
            raise HTTPException(404, "No such trajectory")
        return found

    # -- writes ------------------------------------------------------------

    @app.post("/api/command")
    def command(body: CommandIn):
        return run(runtime.command, body.text, "app")

    @app.post("/api/confirmations/{request_id}")
    def decide(request_id: str, body: DecisionIn):
        return run(runtime.resolve, request_id, body.approve, "app")

    @app.post("/api/emails/upload")
    async def upload(file: UploadFile = File(...)):
        raw = await file.read(5 * 1024 * 1024 + 1)
        return {"results": await asyncio.to_thread(run, runtime.upload_eml, raw)}

    @app.post("/api/scan")
    def scan():
        try:
            return run(runtime.scan)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 -- provider failure, shown to the user
            raise HTTPException(502, "Inbox scan failed: %s" % exc) from None

    @app.post("/api/inbox/{row_id}/dismiss")
    def dismiss(row_id: int):
        if not runtime.state.dismiss_mail(row_id):
            raise HTTPException(404, "No such inbox row")
        runtime.bus.publish("inbox", {})
        return {"ok": True}

    @app.post("/api/settings")
    def update_settings(body: SettingsIn):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if "active_window" in changes and changes["active_window"]:
            from packages.sync_agent.activation import ActivationWindow

            run(ActivationWindow.parse, changes["active_window"])
        if "zone" in changes:
            from packages.nlp.zones import zone_info

            if zone_info(changes["zone"]) is None:
                raise HTTPException(422, "Unknown time zone %r" % changes["zone"])
        saved = runtime.state.get_value("settings", {}) or {}
        saved.update(changes)
        runtime.state.set_value("settings", saved)
        runtime.build()
        scheduler.poke_inbox()
        return runtime.status_dict()

    @app.post("/api/demo/reset")
    def reset_demo():
        if runtime.settings.mode != "demo":
            raise HTTPException(409, "Reset is only for demo mode.")
        runtime.state.reset()
        runtime.build()
        scheduler.poke_inbox()
        return runtime.status_dict()

    @app.post("/api/connections/test/{name}")
    def test_connection(name: str):
        return run(_test_connection, runtime, name)

    # -- Google OAuth ------------------------------------------------------

    @app.get("/auth/google/start")
    def google_start():
        if runtime.google is None:
            raise HTTPException(404, "Google sign-in is not available")
        try:
            return RedirectResponse(runtime.google.start_url(), status_code=303)
        except AuthFlowError as exc:
            return RedirectResponse("/connections/?error=%s" % _q(str(exc)), status_code=303)

    @app.get("/auth/google/callback")
    def google_callback(code: str | None = None, state: str | None = None, error: str | None = None):
        if runtime.google is None:
            raise HTTPException(404, "Google sign-in is not available")
        try:
            runtime.google.complete(code, state, error)
        except AuthFlowError as exc:
            return RedirectResponse("/connections/?error=%s" % _q(str(exc)), status_code=303)
        saved = runtime.state.get_value("settings", {}) or {}
        saved["mode"] = "live"
        runtime.state.set_value("settings", saved)
        runtime.build()
        scheduler.poke_inbox()
        return RedirectResponse("/connections/?connected=google", status_code=303)

    @app.post("/auth/google/disconnect")
    def google_disconnect():
        if runtime.google is not None:
            runtime.google.disconnect()
        runtime.build()
        return runtime.status_dict()

    # -- live updates ------------------------------------------------------

    @app.get("/api/stream")
    async def stream(request: Request):
        queue = runtime.bus.subscribe()

        async def events():
            try:
                yield "event: hello\ndata: {}\n\n"
                while not await request.is_disconnected():
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=20)
                        yield "event: %s\ndata: %s\n\n" % (message["type"], json.dumps(message["data"], default=str))
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                runtime.bus.unsubscribe(queue)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    # -- the UI ------------------------------------------------------------

    dashboard = ROOT / "dashboard"
    if dashboard.exists():
        app.mount("/research", StaticFiles(directory=dashboard, html=True), name="research")
    if WEB_OUT.exists():
        app.mount("/", StaticFiles(directory=WEB_OUT, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def no_ui():
            return _NO_UI

    return app


def _test_connection(runtime: Runtime, name: str) -> dict:
    if name == "calendar":
        events = runtime.events()
        return {"ok": runtime.calendar is not None, "detail": "Read %d events" % len(events)}
    if name == "mail":
        if runtime.mail is None:
            raise NotReady("No inbox connected.")
        if hasattr(runtime.mail, "test_connection"):
            count = runtime.mail.test_connection()
            return {"ok": True, "detail": "Signed in; %d messages in the last 30 days" % count}
        return runtime.scan() | {"ok": True, "detail": "Scan completed"}
    if name == "telegram":
        if runtime.bot is None:
            raise NotReady(runtime.status.telegram.get("detail", "Telegram is not configured."))
        me = runtime.bot.get_me()
        runtime.bot.send_text("Tripwire is connected. Approvals will arrive here.")
        return {"ok": True, "detail": "Sent a test message as @%s" % me.get("username", "bot")}
    if name == "ollama":
        runtime.build()
        return {"ok": bool(runtime.status.ollama.get("ok")), "detail": runtime.status.ollama.get("detail")}
    raise ValueError("Unknown connection %r" % name)


def _q(text: str) -> str:
    import urllib.parse

    return urllib.parse.quote(text[:300])


_NO_UI = """<!doctype html><meta charset="utf-8"><title>Tripwire</title>
<body style="font:16px system-ui;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1>Tripwire is running</h1>
<p>The web UI has not been built yet. From the repository root:</p>
<pre>cd web
npm install
npm run build</pre>
<p>Then reload this page. The API is live at <code>/api/status</code>.</p>
</body>"""


def main_app() -> FastAPI:
    """Factory for ``uvicorn --factory packages.app.server:main_app``."""
    return create_app()

