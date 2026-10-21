"""Turning a vendor's timestamp into the domain's naive local time.

The domain model never carries tzinfo (see ``google_calendar.py`` for why). Vendors
always do: "2026-08-20T09:15:00+05:30", "20260820T034500Z", or a TZID. So every
timestamp that enters from outside is converted here, once:

  * aware + zone configured -> converted into that zone, then made naive;
  * aware + no zone         -> the vendor's own wall-clock time, made naive. That
                               is the departure time printed on the ticket, which
                               is what a user in the same zone expects to see;
  * naive                   -> taken as already local.

``zoneinfo`` needs a tz database, which a bare Windows Python does not have. The
``tzdata`` package supplies it. If it is missing anyway, the fallback is the
vendor's wall clock, never a guess at an offset.
"""

from __future__ import annotations

from datetime import date, datetime, tzinfo
from functools import lru_cache


@lru_cache(maxsize=16)
def zone_info(name: str | None) -> tzinfo | None:
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 -- ZoneInfoNotFoundError, or no tzdata at all
        return None


def to_local(value, zone: str | None) -> datetime | None:
    """ISO-8601 string, date or datetime -> naive local datetime (or None)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                return datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())
            except ValueError:
                return None
    return localise(parsed, zone)


def localise(when: datetime, zone: str | None) -> datetime:
    if when.tzinfo is None:
        return when
    target = zone_info(zone)
    if target is not None:
        when = when.astimezone(target)
    return when.replace(tzinfo=None)
