"""Reaching the user when they are not looking at the app.

FR5 means a destructive change waits for a "yes". If the only place the question
appears is a browser tab the user closed, the change waits out its timeout and
is dropped (UC3 E1, fail-safe) -- correct, but useless. A notifier takes the
question to wherever the user is.

A notifier only *delivers* questions and *relays* answers. It never decides:
an answer arriving through one still goes through ``SyncAgent.resolve_confirmation``,
and with it every check the confirmation module makes (pending, not expired,
not already answered).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    name: str

    def send_text(self, text: str) -> None: ...

    def send_confirmation(self, request_id: str, prompt: str, detail: str | None = None) -> None: ...
