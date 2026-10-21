"""iCalendar (RFC 5545) attachments -> reservations.

Airlines, IRCTC, hotel sites and clinic booking tools attach an ``invite.ics`` to
their mail, and calendar clients act on it. The fields that matter:

    METHOD:CANCEL or STATUS:CANCELLED -> the booking is cancelled
    DTSTART / DTEND                   -> when (UTC "Z", TZID=..., or a DATE)
    UID                               -> stable id across updates; used as the
                                         booking reference when the email has
                                         no better one
    SEQUENCE                          -> revision number; a later revision at a
                                         new time is a reschedule, which the
                                         reconciler infers from the time change

A small hand-written reader rather than the ``icalendar`` package: the core of
this project depends on nothing but PyYAML, and the subset needed -- unfold
lines, read one VEVENT's properties and their parameters -- is short enough to
read in one sitting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from packages.core.models import BookingStatus

from .zones import localise, zone_info


@dataclass
class IcsEvent:
    uid: str | None
    summary: str
    start: datetime | None
    end: datetime | None
    location: str | None
    status: BookingStatus
    sequence: int


def parse_ics(text: str, zone: str | None = None) -> list[IcsEvent]:
    lines = _unfold(text)
    method = ""
    events: list[IcsEvent] = []
    current: dict[str, tuple[dict, str]] | None = None
    for line in lines:
        name, params, value = _split(line)
        if name == "METHOD":
            method = value.upper()
        elif name == "BEGIN" and value.upper() == "VEVENT":
            current = {}
        elif name == "END" and value.upper() == "VEVENT" and current is not None:
            events.append(_event(current, method, zone))
            current = None
        elif current is not None and name not in current:
            # First occurrence wins: a VALARM nested inside the VEVENT repeats
            # DESCRIPTION and TRIGGER, and must not overwrite the event's own.
            current[name] = (params, value)
    return events


def _unfold(text: str) -> list[str]:
    """RFC 5545 3.1: a line starting with a space or tab continues the last one."""
    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _split(line: str) -> tuple[str, dict, str]:
    head, _, value = line.partition(":")
    name, *raw_params = head.split(";")
    params = {}
    for raw in raw_params:
        key, _, val = raw.partition("=")
        params[key.upper()] = val.strip('"')
    return name.upper(), params, value


def _unescape(value: str) -> str:
    return re.sub(r"\\([,;\\nN])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value).strip()


def _when(entry: tuple[dict, str] | None, zone: str | None) -> datetime | None:
    if entry is None:
        return None
    params, value = entry
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
            return datetime.combine(date(int(value[:4]), int(value[4:6]), int(value[6:8])), datetime.min.time())
        parsed = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    if value.endswith("Z"):
        return localise(parsed.replace(tzinfo=timezone.utc), zone)
    tzid = params.get("TZID")
    source = zone_info(tzid) if tzid else None
    if source is not None:
        return localise(parsed.replace(tzinfo=source), zone)
    # Floating time, or a TZID we cannot resolve: the wall clock as written.
    return parsed


def _event(props: dict[str, tuple[dict, str]], method: str, zone: str | None) -> IcsEvent:
    status_raw = props.get("STATUS", ({}, ""))[1].upper()
    cancelled = method == "CANCEL" or status_raw == "CANCELLED"
    try:
        sequence = int(props.get("SEQUENCE", ({}, "0"))[1] or 0)
    except ValueError:
        sequence = 0
    uid = props.get("UID", ({}, ""))[1].strip() or None
    return IcsEvent(
        uid=uid,
        summary=_unescape(props.get("SUMMARY", ({}, ""))[1]),
        start=_when(props.get("DTSTART"), zone),
        end=_when(props.get("DTEND"), zone),
        location=_unescape(props.get("LOCATION", ({}, ""))[1]) or None,
        status=BookingStatus.CANCELLED if cancelled else BookingStatus.CONFIRMED,
        sequence=sequence,
    )
