"""A local LLM (Ollama) as the reader of last resort for booking mail.

Consulted only for mail the markup, .ics and rule stages flagged as booking
related but could not parse -- the UC2 E1 quarantine path. So the model can
rescue a booking the rules missed; it can never override a rule that did parse.

Model output is untrusted input and is validated like it:
  * status and type must be from the closed vocabularies, or the answer is void;
  * the start time must parse;
  * a booking reference is kept only if it appears **verbatim** in the email.
    A plausible-looking invented reference is the most dangerous thing a model
    can return here: exact-reference matching is the strongest tier in FR3, so a
    hallucinated ref that happens to equal a real one targets the wrong event.

Whatever survives still goes through reconciliation and, if destructive, through
the FR5 confirmation gate. Not exercised in CI -- no model on the build machine.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime

from packages.core.models import BookingStatus, BookingType

from .email_extract import EmailExtraction

PROMPT = """You read booking emails and extract the booking as JSON.
Return ONLY a JSON object with these keys:
  "status": one of "confirmed", "rescheduled", "cancelled", or null if this is not a booking
  "type": one of {types}
  "title": short calendar title, e.g. "Flight 6E211 Chennai to Delhi"
  "start": local start time as "YYYY-MM-DDTHH:MM"
  "end": local end time as "YYYY-MM-DDTHH:MM" or null
  "ref": the booking reference / PNR exactly as written, or null
  "location": where it starts, or null
Today is {today}. Use null rather than guessing.

Subject: {subject}

{text}

JSON:"""

#: Enough of an email to find the booking in, without paying to read the footer.
MAX_CHARS = 6000


class OllamaEmailExtractor:
    def __init__(self, model: str = "qwen2.5:7b-instruct", host: str = "http://localhost:11434", timeout: float = 90):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.name = "ollama:%s" % model

    def extract(self, subject: str, text: str, now: datetime) -> EmailExtraction | None:
        prompt = PROMPT.format(
            types=", ".join('"%s"' % t.value for t in BookingType),
            today=now.strftime("%Y-%m-%d"),
            subject=subject,
            text=text[:MAX_CHARS],
        )
        return parse_answer(self._call(prompt), "%s\n%s" % (subject, text), self.name)

    def _call(self, prompt: str) -> str:
        body = json.dumps(
            {"model": self.model, "prompt": prompt, "stream": False, "format": "json", "options": {"temperature": 0}}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.host + "/api/generate", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8")).get("response", "")


def parse_answer(raw: str, source_text: str, model_name: str) -> EmailExtraction | None:
    """Validate a model's JSON against the email it claims to describe."""
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    try:
        status = BookingStatus(str(data.get("status") or "").lower())
        booking_type = BookingType(str(data.get("type") or "").lower())
    except ValueError:
        return None
    start = _parse(data.get("start"))
    title = str(data.get("title") or "").strip()
    if start is None or not title:
        return None

    notes = ["read by %s" % model_name]
    ref = str(data.get("ref") or "").strip()
    if ref and ref.lower() not in source_text.lower():
        notes.append("dropped reference %r: not present in the email" % ref)
        ref = ""

    return EmailExtraction(
        is_booking_related=True,
        status=status,
        booking_type=booking_type,
        booking_ref=ref.upper() or None,
        date_time=start,
        title=title[:120],
        notes=notes,
        stage="llm:%s" % model_name,
        end_time=_parse(data.get("end")),
        location=(str(data.get("location")).strip() or None) if data.get("location") else None,
    )


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)
