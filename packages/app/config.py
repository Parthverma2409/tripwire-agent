"""Settings for the app, from ``.env`` and the process environment.

Precedence: real environment variables beat ``.env``, which beats defaults. That
is the usual convention and the one that lets a launcher or a test override a
single value without editing a file.

Every secret field is ``repr=False`` -- the same rule as ``OAuthCredentials`` --
and ``public()`` is the only view of the settings that ever leaves the process.
It reports *whether* a secret is set, never what it is.

``.env`` is parsed by hand (KEY=VALUE, ``#`` comments, optional quotes) rather
than with python-dotenv: the format needed is tiny and one dependency fewer is
one fewer thing to install on a fresh Windows machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORT = 8765
DEFAULT_ZONE = "Asia/Kolkata"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


@dataclass
class Settings:
    #: "demo" runs on an in-memory calendar and a fixture inbox; "live" uses the
    #: connected accounts. Demo is the default so a fresh clone works offline.
    mode: str = "demo"
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    home: Path = field(default_factory=lambda: Path.home() / ".booking-sync")
    zone: str = DEFAULT_ZONE
    active_window: str = ""  # e.g. "07:00-23:00"; empty = always active
    poll_minutes: int = 5
    alarm_minutes: int = 60

    google_client_id: str = ""
    google_client_secret: str = field(default="", repr=False)
    gmail_query: str = ""
    calendar_id: str = "primary"

    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = field(default="", repr=False)

    telegram_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""

    ollama_host: str = ""
    ollama_model: str = "qwen2.5:7b-instruct"

    #: faster-whisper model size (e.g. "base.en"). Empty = the browser's own
    #: speech recognition, which sends audio to Google / Microsoft.
    whisper_model: str = ""

    #: Tell the user (Telegram, desktop notification) every time the calendar
    #: actually changes -- not only when a question needs an answer.
    notify_changes: bool = True

    #: Where Swiggy Dineout searches and books: an area and its PIN code,
    #: turned into coordinates once through Swiggy's own geocoder.
    dining_area: str = ""
    dining_pin: str = ""

    #: Optional; normally the passphrase lives in the OS keyring (secrets.py).
    token_passphrase: str = field(default="", repr=False)

    @property
    def base_url(self) -> str:
        return "http://%s:%d" % (self.host, self.port)

    @property
    def redirect_uri(self) -> str:
        return "%s/auth/google/callback" % self.base_url

    @property
    def db_path(self) -> Path:
        return self.home / "app.db"

    @property
    def token_path(self) -> Path:
        return self.home / "google-token.json"

    @property
    def swiggy_token_path(self) -> Path:
        return self.home / "swiggy-token.json"

    @property
    def swiggy_redirect_uri(self) -> str:
        return "%s/auth/swiggy/callback" % self.base_url

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def imap_configured(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_password)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    def public(self) -> dict:
        """What the UI may see: values for plain settings, booleans for secrets."""
        return {
            "mode": self.mode,
            "base_url": self.base_url,
            "redirect_uri": self.redirect_uri,
            "zone": self.zone,
            "active_window": self.active_window,
            "poll_minutes": self.poll_minutes,
            "alarm_minutes": self.alarm_minutes,
            "calendar_id": self.calendar_id,
            "gmail_query": self.gmail_query,
            "google_client_id_set": bool(self.google_client_id),
            "google_client_secret_set": bool(self.google_client_secret),
            "imap_host": self.imap_host,
            "imap_user": self.imap_user,
            "imap_password_set": bool(self.imap_password),
            "telegram_token_set": bool(self.telegram_token),
            "telegram_chat_id": self.telegram_chat_id,
            "ollama_host": self.ollama_host,
            "ollama_model": self.ollama_model,
            "whisper_model": self.whisper_model,
            "notify_changes": self.notify_changes,
            "dining_area": self.dining_area,
            "dining_pin": self.dining_pin,
            "home": str(self.home),
        }


_ENV_MAP = {
    "BSYNC_MODE": ("mode", str),
    "BSYNC_HOST": ("host", str),
    "BSYNC_PORT": ("port", int),
    "BSYNC_HOME": ("home", Path),
    "BSYNC_TIMEZONE": ("zone", str),
    "BSYNC_ACTIVE_WINDOW": ("active_window", str),
    "BSYNC_POLL_MINUTES": ("poll_minutes", int),
    "BSYNC_ALARM_MINUTES": ("alarm_minutes", int),
    "GOOGLE_CLIENT_ID": ("google_client_id", str),
    "GOOGLE_CLIENT_SECRET": ("google_client_secret", str),
    "BSYNC_GMAIL_QUERY": ("gmail_query", str),
    "BSYNC_CALENDAR_ID": ("calendar_id", str),
    "IMAP_HOST": ("imap_host", str),
    "IMAP_PORT": ("imap_port", int),
    "IMAP_USER": ("imap_user", str),
    "IMAP_PASSWORD": ("imap_password", str),
    "TELEGRAM_BOT_TOKEN": ("telegram_token", str),
    "TELEGRAM_CHAT_ID": ("telegram_chat_id", str),
    "OLLAMA_HOST": ("ollama_host", str),
    "OLLAMA_MODEL": ("ollama_model", str),
    "WHISPER_MODEL": ("whisper_model", str),
    "BSYNC_TOKEN_PASSPHRASE": ("token_passphrase", str),
    "BSYNC_NOTIFY_CHANGES": ("notify_changes", lambda raw: _flag(raw)),
    "SWIGGY_AREA": ("dining_area", str),
    "SWIGGY_PIN": ("dining_pin", str),
}


def _flag(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ValueError(raw)


def load_settings(env_file: Path | None = None, environ: dict[str, str] | None = None) -> Settings:
    values = load_env_file(env_file if env_file is not None else ROOT / ".env")
    values.update({k: v for k, v in (environ if environ is not None else os.environ).items() if k in _ENV_MAP})
    settings = Settings()
    for key, (attr, cast) in _ENV_MAP.items():
        raw = values.get(key)
        if raw is None or raw == "":
            continue
        try:
            setattr(settings, attr, cast(raw))
        except ValueError as exc:
            kind = "on/off value" if attr == "notify_changes" else cast.__name__
            raise ValueError("%s=%r is not a valid %s" % (key, raw, kind)) from exc
    if settings.mode not in ("demo", "live"):
        raise ValueError("BSYNC_MODE must be 'demo' or 'live', not %r" % settings.mode)
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        # The API has no login. It is safe on loopback and nowhere else.
        raise ValueError("BSYNC_HOST must be a loopback address; the app has no authentication")
    return settings
