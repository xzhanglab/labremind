"""Lab meeting schedule generation.

The cadence is 3 Data meetings followed by 1 Journal Club (2 presenters),
skipping holidays. The weekly meeting day is configurable (default Thursday).
``plan_schedule`` is pure — it takes plain
Python data and returns rows — so it can be unit-tested without any
Google API access. The thin ``generate_schedule`` wrapper handles sheet IO.

This ports ``add_events_from_rotation.py``, which superseded the older
``generate_schedule.py``: instead of trusting rotation-sheet date columns,
the start state is derived from the actual Schedule history.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
import re
import holidays

log = logging.getLogger(__name__)

NUM_JC_PRESENTERS = 2
DATA_PER_JC = 3

# Config value -> date.weekday() number (Monday=0 .. Sunday=6).
WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# US federal holidays (with observed dates, e.g. Friday when July 4 is a
# Saturday). Lab-specific closures go on the Holidays sheet instead.
_US_HOLIDAYS = holidays.US(observed=True)


def next_weekday_after(d: date, weekday: int) -> date:
    """Return the first ``weekday`` (Monday=0..Sunday=6) strictly after ``d``."""
    days_ahead = (weekday - d.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return d + timedelta(days=days_ahead)


def is_holiday(d: date, custom_holidays: dict[str, str]) -> tuple[bool, str]:
    """Check whether a meeting date is a holiday.

    ``custom_holidays`` maps 'YYYY-MM-DD' -> holiday name (from the
    Holidays sheet) and takes precedence over federal holidays.
    """
    name = custom_holidays.get(d.strftime("%Y-%m-%d"))
    if name:
        return True, name
    federal = _US_HOLIDAYS.get(d)
    if federal:
        return True, federal
    return False, ""


def next_presenter_index(history: list[dict], event_type: str, rotation: list[str]) -> int:
    """Index of the person due next, from the last presenter of ``event_type``.

    ``history`` is oldest-first; each entry has 'Type' and 'Presenter(s)'.
    """
    for row in reversed(history):
        if row["Type"] != event_type:
            continue
        last_presenters = [x.strip() for x in re.split(r"[,&]", str(row["Presenter(s)"])) if x.strip()]
        last_person = last_presenters[-1]
        try:
            return (rotation.index(last_person) + 1) % len(rotation)
        except ValueError:
            log.warning(
                "Last presenter %r not in rotation list; starting from the top.", last_person
            )
            return 0
    return 0


def data_streak(history: list[dict]) -> int:
    """Number of consecutive Data meetings since the last Journal Club."""
    streak = 0
    for row in reversed(history):
        if row["Type"] == "Journal Club":
            break
        if row["Type"] == "Data":
            streak += 1
        # Holidays don't affect the cadence.
    return streak


def plan_schedule(
    start_date: date,
    rotation_data: list[str],
    rotation_jc: list[str],
    custom_holidays: dict[str, str],
    history: list[dict],
    limit: int,
    data_per_jc: int = DATA_PER_JC,
    num_jc_presenters: int = NUM_JC_PRESENTERS,
    meeting_weekday: int = 3,  # Thursday
) -> list[list[str]]:
    """Generate ``limit`` schedule rows [date, type, presenter] from pure inputs.

    ``history`` (oldest-first schedule rows) determines the rotation
    position and where we are in the data/JC cycle.
    """
    if not rotation_data or not rotation_jc:
        raise ValueError("Both data and journal-club rotations must be non-empty.")

    data_index = next_presenter_index(history, "Data", rotation_data)
    jc_index = next_presenter_index(history, "Journal Club", rotation_jc)
    streak = data_streak(history)

    rows: list[list[str]] = []
    current = start_date
    while len(rows) < limit:
        current = next_weekday_after(current, meeting_weekday)

        on_holiday, holiday_name = is_holiday(current, custom_holidays)
        if on_holiday:
            rows.append([current.strftime("%Y-%m-%d"), "Holiday", holiday_name])
            continue

        if streak < data_per_jc:
            presenter = rotation_data[data_index % len(rotation_data)]
            rows.append([current.strftime("%Y-%m-%d"), "Data", presenter])
            data_index = (data_index + 1) % len(rotation_data)
            streak += 1
        else:
            presenters = [
                rotation_jc[(jc_index + i) % len(rotation_jc)]
                for i in range(num_jc_presenters)
            ]
            rows.append([current.strftime("%Y-%m-%d"), "Journal Club", ", ".join(presenters)])
            jc_index = (jc_index + num_jc_presenters) % len(rotation_jc)
            streak = 0

    return rows


def start_date_from_history(history: list[dict], today: date | None = None) -> date:
    """Start planning after the latest scheduled date (or today if empty)."""
    today = today or datetime.now().date()
    dates = [row["Date"] for row in history if isinstance(row.get("Date"), date)]
    return max(dates) if dates else today


def generate_schedule(
    spreadsheet,
    limit: int = 16,
    dry_run: bool = False,
    today: date | None = None,
    data_per_jc: int = DATA_PER_JC,
    num_jc_presenters: int = NUM_JC_PRESENTERS,
    meeting_weekday: int = 3,  # Thursday
) -> list[list[str]]:
    """Plan new schedule rows from the workbook and append them (unless dry-run)."""
    from .sheets import append_schedule_rows, get_holidays, get_rotation, get_schedule_history

    rotation_data, rotation_jc = get_rotation(spreadsheet)
    custom_holidays = get_holidays(spreadsheet)
    history = get_schedule_history(spreadsheet)

    rows = plan_schedule(
        start_date_from_history(history, today),
        rotation_data,
        rotation_jc,
        custom_holidays,
        history,
        limit,
        data_per_jc=data_per_jc,
        num_jc_presenters=num_jc_presenters,
        meeting_weekday=meeting_weekday,
    )

    if dry_run:
        log.info("[dry-run] Would append %d schedule rows:", len(rows))
        for row in rows:
            log.info("[dry-run]   %s | %s | %s", *row)
        return rows

    append_schedule_rows(spreadsheet, rows)
    return rows
