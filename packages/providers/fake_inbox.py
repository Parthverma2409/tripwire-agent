"""In-memory MailProvider that replays fixtures from data/emails/."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from .eml import parse_eml
from .mail import RawEmail


@dataclass
class FakeInbox:
    _queue: list[RawEmail] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)

    def deliver(self, email: RawEmail) -> None:
        self._queue.append(email)

    def deliver_fixture(self, path: Path, message_id: str | None = None) -> RawEmail:
        parsed = parse_eml(path.read_bytes(), message_id=message_id)
        # Fixtures are replayed "now", whatever their Date header says.
        email = replace(parsed, received_at=datetime.now(timezone.utc))
        self.deliver(email)
        return email

    def fetch_new(self) -> list[RawEmail]:
        new = [e for e in self._queue if e.message_id not in self._seen]
        return new

    def mark_seen(self, message_id: str) -> None:
        self._seen.add(message_id)
