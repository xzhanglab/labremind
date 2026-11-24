"""Resolve optional per-row Schedule timing/location against legacy defaults."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import MeetingConfig
from .sheets import LabEvent


@dataclass(frozen=True)
class EventDetails:
    start: datetime
    end: datetime
    time_zone: str
    location: str


def _parse_time(value: str, label: str, event: LabEvent) -> time:
    # Sheets may display time cells as either 24-hour or 12-hour values.
    cleaned = str(value).strip().replace("\u202f", " ").replace("\xa0", " ")
    for pattern in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(cleaned, pattern).time()
        except ValueError:
            pass
    raise ValueError(
        f"Invalid {label} {value!r} for {event.event_date}; "
        "use HH:MM (e.g. 09:00 or 1:30 PM)."
    )


def resolve_event_details(event: LabEvent, config: MeetingConfig) -> EventDetails:
    """Validate and return local aware datetimes and a physical location.

    Blank or missing Schedule cells use the corresponding config setting so
    existing sheets continue to run during migration.
    """
    tz_name = event.time_zone or config.timezone
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"Invalid Time Zone {tz_name!r} for {event.event_date}; use an IANA zone "
            "such as America/Chicago."
        ) from exc

    start_time = _parse_time(event.start_time or config.start_time, "Start", event)
    end_time = _parse_time(event.end_time or config.end_time, "End", event)
    start = datetime.combine(event.event_date, start_time, tzinfo=tz)
    end = datetime.combine(event.event_date, end_time, tzinfo=tz)
    if end <= start:
        raise ValueError(f"End must be after Start for {event.event_date} ({event.presenter}).")
    return EventDetails(start, end, tz_name, event.location or config.room)
