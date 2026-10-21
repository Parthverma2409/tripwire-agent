"""ImapProvider -- the MailProvider for every inbox that is not Gmail (FR2).

Outlook.com, Yahoo, Zoho, a college mail server: all speak IMAP, and all accept
an *app password* when two-factor sign-in is on. That makes this the universal
fallback, at the cost of a password-shaped secret instead of a scoped OAuth
token -- which is why the guide recommends Gmail OAuth where possible.

Read-only by construction, mirroring ``gmail.readonly``:
  * the mailbox is opened with ``EXAMINE``, not ``SELECT``. The server then
    refuses every write, including the implicit "mark as read" that a FETCH of
    BODY[] would otherwise perform;
  * ``BODY.PEEK[]`` is fetched for the same reason, belt and braces;
  * ``mark_seen`` records state locally, exactly as the Gmail adapter does.

Idempotency key (NFR2) is ``imap:<UIDVALIDITY>:<UID>``. A UID alone is only
unique while UIDVALIDITY holds; when a server resets it, every old UID may name
a different message, and folding UIDVALIDITY into the key makes such a reset
look like new mail (safe -- reconciliation is idempotent) rather than like mail
already handled (unsafe -- a cancellation would be skipped).
"""

from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from .eml import parse_eml
from .mail import RawEmail

#: Only mail this recent is considered. Older bookings are either in the past or
#: already reconciled, and a narrow SEARCH keeps a large mailbox cheap.
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_MAX_MESSAGES = 50

_UIDVALIDITY = re.compile(rb"UIDVALIDITY (\d+)")
_INTERNALDATE = re.compile(rb'INTERNALDATE "([^"]+)"')


class ImapError(RuntimeError):
    """Login or protocol failure. The message never includes the password."""


@dataclass
class ImapProvider:
    host: str
    username: str
    password: str = field(repr=False)
    port: int = 993
    mailbox: str = "INBOX"
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    max_messages: int = DEFAULT_MAX_MESSAGES
    #: Injected for tests; production uses a real TLS connection.
    connect: Callable[[str, int], imaplib.IMAP4] | None = None
    _seen: set[str] = field(default_factory=set)
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def fetch_new(self) -> list[RawEmail]:
        conn = self._open()
        try:
            status, data = conn.select(self.mailbox, readonly=True)  # EXAMINE
            if status != "OK":
                raise ImapError("cannot open mailbox %r" % self.mailbox)
            validity = self._uidvalidity(conn, data)
            since = (self.clock() - timedelta(days=self.lookback_days)).strftime("%d-%b-%Y")
            status, data = conn.uid("SEARCH", None, "SINCE", since)
            if status != "OK":
                raise ImapError("SEARCH failed on %r" % self.mailbox)
            uids = (data[0] or b"").split()[-self.max_messages :]
            emails = []
            for uid in uids:
                key = "imap:%s:%s" % (validity, uid.decode())
                if key in self._seen:
                    continue
                email = self._fetch(conn, uid, key)
                if email is not None:
                    emails.append(email)
            return sorted(emails, key=lambda e: (e.received_at, e.message_id))
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    def mark_seen(self, message_id: str) -> None:
        self._seen.add(message_id)

    def test_connection(self) -> int:
        """Log in and count messages in the window. Used by the Connections page."""
        conn = self._open()
        try:
            conn.select(self.mailbox, readonly=True)
            since = (self.clock() - timedelta(days=self.lookback_days)).strftime("%d-%b-%Y")
            _, data = conn.uid("SEARCH", None, "SINCE", since)
            return len((data[0] or b"").split())
        finally:
            try:
                conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass

    # -- internals ---------------------------------------------------------

    def _open(self) -> imaplib.IMAP4:
        try:
            conn = (self.connect or imaplib.IMAP4_SSL)(self.host, self.port)
            conn.login(self.username, self.password)
        except (imaplib.IMAP4.error, OSError) as exc:
            # The exception text from a failed LOGIN can echo the command; build
            # a fresh message instead of passing it through.
            raise ImapError("could not sign in to %s as %s (%s)" % (self.host, self.username, type(exc).__name__)) from None
        return conn

    def _uidvalidity(self, conn: imaplib.IMAP4, select_data) -> str:
        for line in conn.response("UIDVALIDITY")[1] or []:
            if line:
                return line.decode() if isinstance(line, bytes) else str(line)
        for chunk in select_data or []:
            match = _UIDVALIDITY.search(chunk if isinstance(chunk, bytes) else b"")
            if match:
                return match.group(1).decode()
        return "0"

    def _fetch(self, conn: imaplib.IMAP4, uid: bytes, key: str) -> RawEmail | None:
        status, data = conn.uid("FETCH", uid, "(INTERNALDATE BODY.PEEK[])")
        if status != "OK" or not data:
            return None
        for part in data:
            if isinstance(part, tuple) and len(part) == 2:
                meta, raw = part
                email = parse_eml(raw, message_id=key)
                arrived = _internal_date(meta)
                return replace(email, received_at=arrived) if arrived else email
        return None


def _internal_date(meta: bytes) -> datetime | None:
    match = _INTERNALDATE.search(meta or b"")
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group(1).decode(), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        try:
            parsed = parsedate_to_datetime(match.group(1).decode())
        except (TypeError, ValueError):
            return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
