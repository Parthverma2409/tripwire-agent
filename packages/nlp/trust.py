"""Did the mail system authenticate the sender the email claims?

A forged "Your IndiGo flight has been cancelled" is the most obvious attack on
an agent that reads booking mail: it costs nothing to send, and it aims straight
at a destructive action. FR5 already means nothing is deleted without the user
saying yes. This module makes sure that when they are asked, they are also told
whether the sender is real.

The receiving server records its verdict in ``Authentication-Results`` (RFC
8601). Gmail writes, for example:

    mx.google.com; dkim=pass header.i=@goindigo.in header.s=s1;
        spf=pass smtp.mailfrom=bounce@goindigo.in;
        dmarc=pass (p=REJECT) header.from=goindigo.in

Verdicts:
  * verified   -- DMARC passed for the From domain, or a DKIM signature passed
                  for a domain aligned with it (the same organisation).
  * failed     -- the server checked and authentication failed.
  * unknown    -- no verdict: an uploaded file, a provider that does not add the
                  header, or results that do not mention the From domain.

Trust is *advisory*. It changes the wording of the confirmation prompt and is
recorded in the trajectory. It never approves anything. Only the user does that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .vendors import Vendor, domain_matches, identify, sender_domain

VERIFIED = "verified"
FAILED = "failed"
UNKNOWN = "unknown"

_RESULT = re.compile(r"\b(dkim|spf|dmarc)\s*=\s*(\w+)([^;]*)", re.I)
_DOMAIN_PROP = re.compile(r"\b(?:header\.d|header\.i|header\.from|smtp\.mailfrom)\s*=\s*([^\s;()]+)", re.I)


@dataclass(frozen=True)
class SenderTrust:
    level: str
    domain: str
    vendor_id: str | None
    vendor_name: str | None
    reason: str

    @property
    def is_verified(self) -> bool:
        return self.level == VERIFIED

    def warning(self) -> str | None:
        """The sentence added to a destructive confirmation prompt, if any."""
        who = self.vendor_name or self.domain or "the sender"
        if self.level == FAILED:
            return "Warning: this email failed sender authentication -- it may not really be from %s." % who
        if self.level == UNKNOWN and self.vendor_id:
            return "Note: I could not verify that this email really came from %s." % who
        return None

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "domain": self.domain,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_name,
            "reason": self.reason,
        }


def assess(sender: str, headers: dict[str, str], vendor: Vendor | None = None) -> SenderTrust:
    domain = sender_domain(sender)
    vendor = vendor if vendor is not None else identify(sender)
    vendor_id = vendor.id if vendor else None
    vendor_name = vendor.name if vendor else None

    raw = _auth_results(headers)
    if not raw or not domain:
        return SenderTrust(UNKNOWN, domain, vendor_id, vendor_name, "no authentication results on this message")

    passed, failed = [], []
    for method, verdict, props in _RESULT.findall(raw):
        method, verdict = method.lower(), verdict.lower()
        prop_domain = _prop_domain(props)
        aligned = prop_domain is not None and _aligned(prop_domain, domain)
        if verdict == "pass" and aligned and method in ("dkim", "dmarc"):
            passed.append(method)
        elif verdict in ("fail", "permerror") and (aligned or method == "dmarc"):
            failed.append(method)

    if passed:
        return SenderTrust(VERIFIED, domain, vendor_id, vendor_name, "%s passed for %s" % ("+".join(passed), domain))
    if failed:
        return SenderTrust(FAILED, domain, vendor_id, vendor_name, "%s failed for %s" % ("+".join(failed), domain))
    return SenderTrust(UNKNOWN, domain, vendor_id, vendor_name, "no passing signature aligned with %s" % domain)


def _auth_results(headers: dict[str, str]) -> str:
    found = []
    for name, value in headers.items():
        if name.lower() in ("authentication-results", "arc-authentication-results"):
            found.append(str(value))
    return " ; ".join(found)


def _prop_domain(props: str) -> str | None:
    match = _DOMAIN_PROP.search(props)
    if not match:
        return None
    # header.i=@goindigo.in and smtp.mailfrom=bounce@goindigo.in both carry the
    # domain after the last "@"; header.d=goindigo.in has none.
    return match.group(1).rpartition("@")[2].lower().strip(".") or None


def _aligned(a: str, b: str) -> bool:
    """Relaxed DMARC alignment: same domain, or one is a subdomain of the other."""
    return domain_matches(a, b) or domain_matches(b, a)
