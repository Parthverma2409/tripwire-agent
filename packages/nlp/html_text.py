"""HTML -> plain text, good enough for the regex stages to read.

Many vendors (MakeMyTrip, IndiGo, Swiggy) send HTML-only mail, or a text/plain
alternative that just says "view this email in your browser". The rule-based
extractor is written against plain text, so without this it would see markup
and produce confident nonsense -- the exact failure ``gmail._body_text`` warns
about.

This is not a renderer. It keeps the reading order, turns block elements and
table cells into line breaks (so "Departure</td><td>09:15" does not become
"Departure09:15"), and drops scripts, styles and hidden preheader text.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_BLOCK = {
    "address", "article", "br", "div", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "p", "section", "table", "td", "th", "tr",
}
_SKIP = {"script", "style", "head", "title", "noscript"}


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
            return
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        if self._hidden_depth or "display:none" in style:
            # Preheader text ("Save 20% on your next trip!") is usually hidden
            # and would otherwise trip the promotional filter on a real ticket.
            if tag not in ("br", "hr", "img", "meta", "input"):
                self._hidden_depth += 1
            return
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._hidden_depth:
            self._hidden_depth -= 1
            return
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._hidden_depth:
            return
        self.parts.append(data)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    collector = _Collector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:  # noqa: BLE001 -- malformed vendor HTML must not crash ingestion
        return re.sub(r"<[^>]+>", " ", unescape(html))
    text = "".join(collector.parts)
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)
