"""Configuration loading.

Settings live in an INI file (default ``cal_config.cfg``); secrets
(EMAIL_USER / EMAIL_PASSWORD) live in a ``.env`` file or the environment
and are never stored in the config.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .schedule import WEEKDAY_NAMES


@dataclass
class MeetingConfig:
    """Settings for the [labmeeting] section."""

    googlesheet: str
    autocreds: str
    start_time: str = "09:00:00"
    end_time: str = "10:30:00"
    timezone: str = "America/Chicago"
    room: str = ""
    meeting_link: str = ""
    contact_email: str = ""
    holiday_vocab: List[str] = field(default_factory=list)
    schedule_events_count: int = 16
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    batch_size: int = 0
    days_ahead: int = 7  # how far ahead --auto looks for an event
    meeting_weekday: int = 3  # Monday=0..Sunday=6; from 'meeting_day'
    data_per_jc: int = 3  # Data meetings per Journal Club
    num_jc_presenters: int = 2  # presenters per Journal Club
    # BCC mode: address each email to the bot itself so recipients can't
    # see each other. Off by default (visible To: header, as before).
    bcc: bool = False


@dataclass
class TeamsConfig:
    """Settings for the [teams] section."""

    webhook_name: str = "lab_events"
    webhook_url: str = ""
    max_events: int = 7
    # 'workflow' posts JSON to a Power Automate webhook (recommended:
    # Microsoft retired Office 365 connector webhooks); 'card' posts a
    # pymsteams connector card to a classic incoming webhook.
    mode: str = "workflow"


def _split_vocab(raw: str) -> List[str]:
    return [token.strip() for token in raw.split(",") if token.strip()]


def load_config(path: str | Path = "cal_config.cfg") -> tuple[MeetingConfig, TeamsConfig]:
    """Load meeting + Teams settings from an INI file."""
    parser = configparser.ConfigParser(interpolation=None)
    read = parser.read(str(path))
    if not read:
        raise FileNotFoundError(f"Config file not found: {path}")
    if "labmeeting" not in parser:
        raise ValueError(f"Config file {path} must have a [labmeeting] section.")

    s = parser["labmeeting"]
    meeting = MeetingConfig(
        googlesheet=s.get("googlesheet", ""),
        autocreds=s.get("autocreds", ""),
        start_time=s.get("start_time", "09:00:00"),
        end_time=s.get("end_time", "10:30:00"),
        timezone=s.get("timezone", "America/Chicago"),
        room=s.get("room", ""),
        # 'zoom' is accepted as a legacy alias for 'meeting_link'.
        meeting_link=s.get("meeting_link", s.get("zoom", "")),
        contact_email=s.get("email", ""),
        holiday_vocab=_split_vocab(s.get("holiday_vocab", "")),
        schedule_events_count=s.getint("schedule_events_count", 16),
        smtp_server=s.get("smtp_server", "smtp.gmail.com"),
        smtp_port=s.getint("smtp_port", 587),
        batch_size=s.getint("batch_size", 0),
        days_ahead=s.getint("days_ahead", 7),
        data_per_jc=max(1, s.getint("data_per_jc", 3)),
        num_jc_presenters=max(1, s.getint("num_jc_presenters", 2)),
        meeting_weekday=_parse_meeting_day(s.get("meeting_day", "Thursday")),
        bcc=s.getboolean("bcc", False),
    )

    t = parser["teams"] if "teams" in parser else {}
    teams = TeamsConfig(
        webhook_name=t.get("webhookname", "lab_events"),
        webhook_url=t.get("webhookUrl", ""),
        max_events=t.getint("maxevents", 7) if hasattr(t, "getint") else 7,
        mode=t.get("mode", "workflow"),
    )
    return meeting, teams


def _parse_meeting_day(raw: str) -> int:
    """Map a 'meeting_day' config value (e.g. 'Friday') to a weekday number."""
    key = raw.strip().lower()
    if key not in WEEKDAY_NAMES:
        valid = ", ".join(sorted(WEEKDAY_NAMES, key=WEEKDAY_NAMES.get))
        raise ValueError(f"Invalid meeting_day {raw!r}; expected one of: {valid}.")
    return WEEKDAY_NAMES[key]
