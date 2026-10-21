"""The local web app: the agent as something a person can actually use.

Everything below ``packages/app`` is wiring. It owns no booking logic -- the
agent, the FR5 gate, reconciliation and extraction live where they always did
and are called exactly as the tests and the benchmark call them. If a behaviour
is reachable from the UI and not from ``SyncAgent``, that is a bug here.

Layout:
    config.py     settings from .env / the environment, never logged
    keystore.py   token passphrase and bot-signing key, via the OS keyring
    state.py      SQLite: seen mail, inbox log, activity, scored trajectories
    runtime.py    builds the agent (demo or live) and serialises calls into it
    google_auth.py  the OAuth redirect flow with CSRF state
    scheduler.py  background loops: inbox poll, expiry, Telegram
    server.py     FastAPI routes, SSE, the static UI
"""
