"""The research dashboard, fed from what the app actually did.

``dashboard/`` was built to review the benchmark: ``build_index.py`` turns the
66 scenario trajectories into ``window.TRAJECTORY_DATA``. Served by the app, the
same page should show the app's own trajectories and the real calendar instead,
so ``/research/data/index.js`` is answered here, live, with the same index shape
-- the same summary and aggregation functions, so a number on the dashboard
means the same thing whichever corpus it describes. The benchmark view is still
there when ``dashboard/index.html`` is opened from disk.

Trajectories are grouped by what started them (a command, an email, an
approval, a connected account) where the benchmark groups by scenario family.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from functools import lru_cache

from .config import ROOT
from .runtime import NotReady, Runtime

FAMILIES = {
    "command": "typed or spoken command",
    "email": "booking email",
    "decision": "your approval",
    "booking": "connected account",
    "timeout": "unanswered question",
}


@lru_cache(maxsize=1)
def _builder():
    spec = importlib.util.spec_from_file_location("dashboard_build_index", ROOT / "dashboard" / "build_index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live_index(runtime: Runtime) -> dict:
    builder = _builder()
    records = runtime.state.all()
    started_by = {
        row["trajectory_id"]: row["kind"]
        for row in runtime.state.activity(limit=5000)
        if row.get("trajectory_id")
    }
    summaries, by_key, last_touch = [], {}, {}
    for number, record in enumerate(records, start=1):
        key = "T-%04d" % number
        kind = started_by.get(record.get("trajectory_id"), "")
        summaries.append(builder.summarise(record, key, FAMILIES.get(kind, kind or "other")))
        by_key[key] = record
        for action in record.get("actions") or []:
            if action.get("target"):
                last_touch[str(action["target"])] = (key, action, (record.get("outcome") or {}).get("status"))
    summaries.sort(key=lambda row: str(row.get("started") or ""), reverse=True)

    try:
        events = runtime.events()
    except NotReady:
        events = []
    calendar = []
    for event in events:
        key, action, outcome = last_touch.get(event["id"], (None, None, None))
        calendar.append({
            # The page groups by scenario; live, the useful split is whether
            # Tripwire has acted on the event or it was already there.
            "scenario_id": "Tripwire" if key else "Calendar",
            "family": event.get("type") or "other",
            "trajectory_key": key,
            "outcome": outcome,
            "origin": "calendar",
            "event_id": event["id"],
            "title": event["title"],
            "type": event.get("type") or "other",
            "start": event["start"],
            "end": event.get("end"),
            "booking_ref": event.get("ref"),
            "location": event.get("location"),
            "source": None,
            "status": None,
            "t": None,
            "action": (
                {"type": str(action.get("type")), "destructive": bool(action.get("destructive")),
                 "confirmed": action.get("confirmed_by_user") is True}
                if action else None
            ),
            "kept": None,
        })

    return {
        "mode": "live",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "schema_version": "1.0",
        "source_dir": "app.db",
        "files_seen": len(records),
        "summary": builder._aggregate(summaries, []),
        "trajectories": summaries,
        "records": by_key,
        "quarantined": [],
        "calendar": {
            "now": runtime.now().isoformat(timespec="seconds"),
            "events": calendar,
            "types": sorted({e["type"] for e in calendar}),
        },
    }


def live_index_js(runtime: Runtime) -> str:
    return "%s = %s;\n" % (_builder().DATA_GLOBAL, json.dumps(live_index(runtime), default=str))
