"""RFC 822 / MIME -> RawEmail, in one place.

Four callers need this and must agree: the fixture inbox, the IMAP adapter, the
app's ``.eml`` upload and the ``check-email`` CLI. If each walked MIME trees its
own way, the same message would extract differently depending on how it arrived,
and "it works when I upload it but not from IMAP" is a miserable bug to chase.

What is kept from the tree:
  * the first ``text/plain`` part as ``body`` -- what the regex stages read;
  * the first ``text/html`` part as ``html`` -- where schema.org markup lives;
  * every ``text/calendar`` / ``.ics`` part as an attachment, size-capped.

Everything else (PDF tickets, images, logos) is dropped. Not reading it is the
cheapest way to not mishandle it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from .mail import Attachment, RawEmail

#: A real itinerary .ics is a few KB. Anything near this is not one.
MAX_ATTACHMENT_BYTES = 256 * 1024

_CALENDAR_TYPES = {"text/calendar", "application/ics"}


def parse_eml(raw: bytes | str, message_id: str | None = None) -> RawEmail:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    msg = message_from_bytes(data, policy=policy.default)
    assert isinstance(msg, EmailMessage)

    plain = _first_text(msg, "text/plain")
    html = _first_text(msg, "text/html")
    return RawEmail(
        message_id=message_id or str(msg.get("Message-ID", "")).strip() or _fallback_id(data),
        sender=str(msg.get("From", "")),
        subject=str(msg.get("Subject", "")),
        body=plain if plain is not None else "",
        received_at=_received_at(msg),
        headers={k: str(v) for k, v in msg.items()},
        html=html or "",
        attachments=tuple(_calendar_parts(msg)),
    )


def _first_text(msg: EmailMessage, mime_type: str) -> str | None:
    for part in msg.walk():
        if part.is_multipart() or part.get_content_type() != mime_type:
            continue
        if part.get_content_disposition() == "attachment":
            continue
        try:
            return str(part.get_content())
        except (LookupError, ValueError):
            # Unknown charset or broken transfer encoding: fall back to bytes so
            # one bad part does not hide the rest of the message.
            payload = part.get_payload(decode=True) or b""
            return payload.decode("utf-8", errors="replace")
    return None


def _calendar_parts(msg: EmailMessage) -> list[Attachment]:
    found = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename() or ""
        if part.get_content_type() not in _CALENDAR_TYPES and not filename.lower().endswith(".ics"):
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload or len(payload) > MAX_ATTACHMENT_BYTES:
            continue
        found.append(Attachment(filename=filename or "invite.ics", mime_type="text/calendar", data=payload))
    return found


def _received_at(msg: EmailMessage) -> datetime:
    """The Date header, as naive UTC like the rest of the domain model.

    Only a fallback ordering key -- providers that know the true arrival time
    (Gmail's internalDate, IMAP's INTERNALDATE) pass their own.
    """
    raw = msg.get("Date")
    if raw:
        try:
            parsed = parsedate_to_datetime(str(raw))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fallback_id(data: bytes) -> str:
    import hashlib

    # Content-addressed, so the same file uploaded twice is the same message
    # (NFR2) rather than two.
    return "sha256:" + hashlib.sha256(data).hexdigest()[:24]
