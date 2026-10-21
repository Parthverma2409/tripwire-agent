"""A real calendar holds history; the agent has to tell last year from next week.

The first live run matched "cancel my Chennai flight" against two flights Gmail
had added -- one from July 2025, one from October 2025 -- and showed both without
a year, so a trip that was over a year old read as next week's.
"""

from __future__ import annotations

from datetime import datetime

from packages.core.models import BookingType, CalendarEvent
from packages.providers.fake_calendar import FakeCalendar
from packages.sync_agent.agent import SyncAgent

NOW = datetime(2026, 10, 21, 22, 0)


def _flight(event_id: str, when: datetime, code: str) -> CalendarEvent:
    return CalendarEvent(event_id=event_id, title="Flight to Chennai (%s)" % code, start_time=when,
                         booking_type=BookingType.FLIGHT)


def _agent(*events: CalendarEvent) -> SyncAgent:
    calendar = FakeCalendar()
    for event in events:
        calendar.seed(event)
    return SyncAgent(calendar)


def test_past_trips_are_not_candidates_when_an_upcoming_one_matches() -> None:
    agent = _agent(
        _flight("old", datetime(2025, 7, 7, 7, 20), "6E 971"),
        _flight("new", datetime(2026, 10, 27, 14, 20), "6E 6724"),
    )
    result = agent.handle_command("cancel my Chennai flight", NOW)
    assert result.status == "awaiting_confirmation"
    assert result.decision.target_event_id == "new"


def test_only_past_matches_change_nothing_and_say_when_they_were() -> None:
    agent = _agent(
        _flight("a", datetime(2025, 7, 7, 7, 20), "6E 971"),
        _flight("b", datetime(2025, 10, 27, 14, 20), "6E 6724"),
    )
    result = agent.handle_command("cancel my Chennai flight", NOW)
    assert result.status == "refused"
    assert result.confirmation is None
    assert "07 Jul 2025" in result.message and "27 Oct 2025" in result.message


def test_two_upcoming_matches_still_ask_and_show_the_year_only_when_it_differs() -> None:
    agent = _agent(
        _flight("a", datetime(2026, 10, 27, 14, 20), "6E 6724"),
        _flight("b", datetime(2027, 1, 5, 9, 0), "6E 100"),
    )
    result = agent.handle_command("cancel my Chennai flight", NOW)
    assert result.needs_clarification
    assert "27 Oct at 14:20" in result.message
    assert "05 Jan 2027 at 09:00" in result.message


def test_a_question_lists_whats_ahead_not_the_whole_history() -> None:
    agent = _agent(
        _flight("old", datetime(2025, 7, 7, 7, 20), "6E 971"),
        _flight("new", datetime(2026, 10, 27, 14, 20), "6E 6724"),
    )
    result = agent.handle_command("when is my Chennai flight", NOW)
    assert "6E 6724" in result.message
    assert "6E 971" not in result.message
