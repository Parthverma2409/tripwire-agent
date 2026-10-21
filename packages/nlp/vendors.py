"""Vendor profiles: who an email is from, and what that tells the extractor.

Profiles live in ``data/vendors/vendors.yaml`` so a new sender is a data change,
not a code change. Identification is by the sender's *domain*, matched on a
label boundary -- ``mail.goindigo.in`` is IndiGo, ``goindigo.in.evil.com`` and
``notgoindigo.in`` are not.

The From header is trivially forged, so identifying a vendor here grants nothing
on its own. ``trust.py`` checks whether the mail system actually authenticated
that domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr
from functools import lru_cache
from pathlib import Path

import yaml

from packages.core.models import BookingType

VENDOR_FILE = Path(__file__).resolve().parents[2] / "data" / "vendors" / "vendors.yaml"


@dataclass(frozen=True)
class Vendor:
    id: str
    name: str
    domains: tuple[str, ...]
    booking_type: BookingType | None
    ref_patterns: tuple[re.Pattern, ...]

    def find_ref(self, text: str) -> str | None:
        for pattern in self.ref_patterns:
            match = pattern.search(text)
            if match:
                return match.group(1).upper()
        return None


@lru_cache(maxsize=4)
def load_vendors(path: Path = VENDOR_FILE) -> tuple[Vendor, ...]:
    if not path.exists():
        return ()
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    vendors = []
    for row in rows:
        raw_type = row.get("booking_type")
        vendors.append(
            Vendor(
                id=row["id"],
                name=row.get("name", row["id"]),
                domains=tuple(d.lower().strip(".") for d in row.get("domains") or ()),
                booking_type=BookingType(raw_type) if raw_type else None,
                ref_patterns=tuple(re.compile(p) for p in row.get("ref_patterns") or ()),
            )
        )
    return tuple(vendors)


def sender_domain(sender: str) -> str:
    """``"IndiGo <noreply@mail.goindigo.in>"`` -> ``"mail.goindigo.in"``."""
    _, address = parseaddr(sender or "")
    return address.rpartition("@")[2].lower().strip(".>")


def domain_matches(domain: str, owned: str) -> bool:
    return domain == owned or domain.endswith("." + owned)


def identify(sender: str, vendors: tuple[Vendor, ...] | None = None) -> Vendor | None:
    domain = sender_domain(sender)
    if not domain:
        return None
    for vendor in vendors if vendors is not None else load_vendors():
        if any(domain_matches(domain, owned) for owned in vendor.domains):
            return vendor
    return None
