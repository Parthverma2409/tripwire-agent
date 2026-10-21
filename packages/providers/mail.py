"""MailProvider port."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Attachment:
    """A MIME part the extractor may read. Only small, typed parts are kept --
    the extractor wants ``text/calendar``, not someone's PDF boarding pass."""

    filename: str
    mime_type: str
    data: bytes = field(repr=False)

    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class RawEmail:
    message_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime
    headers: dict[str, str] = field(default_factory=dict)
    #: The HTML alternative, when the message has one. Vendors put schema.org
    #: reservation markup here, which is the most reliable thing in the email.
    html: str = ""
    attachments: tuple[Attachment, ...] = ()


@runtime_checkable
class MailProvider(Protocol):
    def fetch_new(self) -> list[RawEmail]: ...
    def mark_seen(self, message_id: str) -> None: ...
