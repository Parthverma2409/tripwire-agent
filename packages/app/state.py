"""Durable app state in one SQLite file (``~/.booking-sync/app.db``).

What is kept, and why each survives a restart:
  * **seen**         mail already processed. Without it a restart re-reads the
                     last 30 days of mail; idempotency makes that safe, but it
                     is slow and re-asks open questions.
  * **mail**         one row per booking found in an email, with the stage that
                     read it and the sender verdict -- the Inbox page.
  * **activity**     everything the agent said to the user, in order.
  * **trajectories** every trajectory, scored by the FR8 evaluators. This class
                     *is* a ``TrajectoryStore``, so the ingest pipeline writes
                     straight into it.
  * **kv**           settings changed in the UI, and small facts like the time
                     of the last inbox scan.

What is deliberately NOT kept: pending confirmations. They expire after 15
minutes (UC3 E1), so after a restart the safe reading of "was the user asked?"
is "no" -- nothing is applied, and the next email or command asks again. Persisting
them would mean persisting a capability to delete calendar events, for a gain
of saving one repeated question.

One connection, one lock. SQLite serialises writers anyway; the lock keeps the
FastAPI thread pool and the background loops from tripping over each other.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    message_id TEXT PRIMARY KEY,
    seen_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mail (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL,
    idx          INTEGER NOT NULL DEFAULT 0,
    channel      TEXT NOT NULL,
    received_at  TEXT,
    processed_at TEXT NOT NULL,
    sender       TEXT,
    subject      TEXT,
    stage        TEXT,
    vendor_id    TEXT,
    trust        TEXT,
    status       TEXT NOT NULL,
    message      TEXT,
    booking      TEXT,
    request_id   TEXT,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (message_id, idx)
);
CREATE TABLE IF NOT EXISTS activity (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    channel       TEXT NOT NULL,
    kind          TEXT NOT NULL,
    input         TEXT,
    status        TEXT NOT NULL,
    message       TEXT,
    meta          TEXT,
    trajectory_id TEXT,
    request_id    TEXT
);
CREATE TABLE IF NOT EXISTS trajectories (
    trajectory_id TEXT PRIMARY KEY,
    ts            TEXT NOT NULL,
    body          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class AppState:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, params)

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    # -- seen --------------------------------------------------------------

    def seen_set(self) -> "SeenSet":
        return SeenSet(self)

    # -- mail --------------------------------------------------------------

    def record_mail(self, *, message_id: str, idx: int, channel: str, received_at: datetime | None,
                    sender: str, subject: str, stage: str | None, vendor_id: str | None, trust: dict | None,
                    status: str, message: str, booking: dict | None, request_id: str | None) -> None:
        self._exec(
            """INSERT INTO mail (message_id, idx, channel, received_at, processed_at, sender, subject, stage,
                                 vendor_id, trust, status, message, booking, request_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(message_id, idx) DO UPDATE SET
                 processed_at=excluded.processed_at, stage=excluded.stage, trust=excluded.trust,
                 status=excluded.status, message=excluded.message, booking=excluded.booking,
                 request_id=excluded.request_id""",
            (
                message_id, idx, channel, received_at.isoformat() if received_at else None, _now(), sender,
                subject, stage, vendor_id, _dump(trust), status, message, _dump(booking), request_id,
            ),
        )

    def mail(self, limit: int = 200, quarantine_only: bool = False) -> list[dict]:
        where = "WHERE status = 'failure' AND dismissed = 0" if quarantine_only else ""
        rows = self._rows("SELECT * FROM mail %s ORDER BY processed_at DESC, id DESC LIMIT ?" % where, (limit,))
        for row in rows:
            row["trust"] = _load(row["trust"])
            row["booking"] = _load(row["booking"])
            row["dismissed"] = bool(row["dismissed"])
        return rows

    def dismiss_mail(self, row_id: int) -> bool:
        return self._exec("UPDATE mail SET dismissed = 1 WHERE id = ?", (row_id,)).rowcount > 0

    # -- activity ----------------------------------------------------------

    def log(self, *, channel: str, kind: str, input: str | None, status: str, message: str,
            meta: dict | None = None, trajectory_id: str | None = None, request_id: str | None = None) -> int:
        cursor = self._exec(
            "INSERT INTO activity (ts, channel, kind, input, status, message, meta, trajectory_id, request_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (_now(), channel, kind, input, status, message, _dump(meta), trajectory_id, request_id),
        )
        return int(cursor.lastrowid)

    def activity(self, limit: int = 100) -> list[dict]:
        rows = self._rows("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["meta"] = _load(row["meta"])
        return rows

    # -- trajectories (TrajectoryStore protocol) ---------------------------

    def put(self, trajectory: dict) -> None:
        self._exec(
            "INSERT OR REPLACE INTO trajectories (trajectory_id, ts, body) VALUES (?,?,?)",
            (str(trajectory["trajectory_id"]), _now(), json.dumps(trajectory)),
        )

    def get(self, trajectory_id: str) -> dict | None:
        rows = self._rows("SELECT body FROM trajectories WHERE trajectory_id = ?", (trajectory_id,))
        return json.loads(rows[0]["body"]) if rows else None

    def all(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self._rows("SELECT body FROM trajectories ORDER BY ts")]

    def recent_trajectories(self, limit: int = 100) -> list[dict]:
        rows = self._rows("SELECT body FROM trajectories ORDER BY ts DESC LIMIT ?", (limit,))
        return [json.loads(r["body"]) for r in rows]

    def __len__(self) -> int:
        return int(self._rows("SELECT COUNT(*) AS n FROM trajectories")[0]["n"])

    # -- kv ----------------------------------------------------------------

    def get_value(self, key: str, default: Any = None) -> Any:
        rows = self._rows("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_value(self, key: str, value: Any) -> None:
        self._exec("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def delete_value(self, key: str) -> None:
        self._exec("DELETE FROM kv WHERE key = ?", (key,))

    def reset(self) -> None:
        """Forget processed mail, activity and trajectories. Settings survive."""
        with self._lock:
            self._db.executescript("DELETE FROM seen; DELETE FROM mail; DELETE FROM activity; DELETE FROM trajectories;")


class SeenSet:
    """``in`` and ``add`` over the seen table -- what the mail adapters expect."""

    def __init__(self, state: AppState):
        self._state = state

    def __contains__(self, message_id: object) -> bool:
        return bool(self._state._rows("SELECT 1 FROM seen WHERE message_id = ?", (str(message_id),)))

    def add(self, message_id: str) -> None:
        self._state._exec("INSERT OR IGNORE INTO seen (message_id, seen_at) VALUES (?, ?)", (message_id, _now()))

    def __iter__(self) -> Iterator[str]:
        return iter([r["message_id"] for r in self._state._rows("SELECT message_id FROM seen")])

    def __len__(self) -> int:
        return int(self._state._rows("SELECT COUNT(*) AS n FROM seen")[0]["n"])


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def _load(value: str | None) -> Any:
    return None if value is None else json.loads(value)
