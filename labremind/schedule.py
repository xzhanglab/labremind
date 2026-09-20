"""Lab meeting schedule generation.

The cadence is 3 Data meetings followed by 1 Journal Club (2 presenters),
skipping holiday Thursdays. ``plan_schedule`` is pure — it takes plain
Python data and returns rows — so it can be unit-tested without any
Google API access. The thin ``generate_schedule`` wrapper handles sheet IO.

This ports ``add_events_from_rotation.py``, which superseded the older
``generate_schedule.py``: instead of trusting rotation-sheet date columns,
the start state is derived from the actual Schedule history.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

NUM_JC_PRESENTERS = 2
DATA_PER_JC = 3


def next_thursday_after(d: date) -> date:
    """Return the first Thursday strictly after ``d``."""
    days_ahead = (3 - d.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return d + timedelta(days=days_ahead)


def is_holiday_thursday(d: date, custom_holidays: dict[str, str]) -> tuple[bool, str]:
    """Check whether a Thursday is a holiday.

    ``custom_holidays`` maps 'YYYY-MM-DD' -> holiday name (from the
    Holidays sheet). Federal/BCM-observed Thursdays are hardcoded.
    """
    if (
        # New Year's: first Thursday in January
        (d.month == 1 and d.weekday() == 3 and d.day <= 7)
        # Independence Day, when July 4 falls on a Thursday
        or (d.month == 7 and d.day == 4 and d.weekday() == 3)
        # Thanksgiving: fourth Thursday in November
        or (d.month == 11 and d.weekday() == 3 and 22 <= d.day <= 28)
        # Christmas/New Year: last Thursday in December
        or (
            d.month == 12
            and d.weekday() == 3
            and d.day >= calendar.monthrange(d.year, d.month)[1] - 6
        )
    ):
        return True, "Federal Holiday/BCM observed"

    name = custom_holidays.get(d.strftime("%Y-%m-%d"))
    if name:
        return True, name
    return False, ""


def next_presenter_index(history: list[dict], event_type: str, rotation: list[str]) -> int:
    """Index of the person due next, from the last presenter of ``event_type``.

    ``history`` is oldest-first; each entry has 'Type' and 'Presenter(s)'.
    """
    for row in reversed(history):
        if row["Type"] != event_type:
            continue
        last_presenters = [x.strip() for x in str(row["Presenter(s)"]).split(",")]
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
) -> list[list[str]]:
    """Generate ``limit`` schedule rows [date, type, presenter] from pure inputs.

    ``history`` (oldest-first schedule rows) determines the rotation
    position and where we are in the 3-Data/1-JC cycle.
    """
    if not rotation_data or not rotation_jc:
        raise ValueError("Both data and journal-club rotations must be non-empty.")

    data_index = next_presenter_index(history, "Data", rotation_data)
    jc_index = next_presenter_index(history, "Journal Club", rotation_jc)
    streak = data_streak(history)

    rows: list[list[str]] = []
    current = start_date
    while len(rows) < limit:
        current = next_thursday_after(current)

        is_holiday, holiday_name = is_holiday_thursday(current, custom_holidays)
        if is_holiday:
            rows.append([current.strftime("%Y-%m-%d"), "Holiday", holiday_name])
            continue

        if streak < DATA_PER_JC:
            presenter = rotation_data[data_index % len(rotation_data)]
            rows.append([current.strftime("%Y-%m-%d"), "Data", presenter])
            data_index = (data_index + 1) % len(rotation_data)
            streak += 1
        else:
            presenters = [
                rotation_jc[(jc_index + i) % len(rotation_jc)]
                for i in range(NUM_JC_PRESENTERS)
            ]
            rows.append([current.strftime("%Y-%m-%d"), "Journal Club", ", ".join(presenters)])
            jc_index = (jc_index + NUM_JC_PRESENTERS) % len(rotation_jc)
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
    )

    if dry_run:
        log.info("[dry-run] Would append %d schedule rows:", len(rows))
        for row in rows:
            log.info("[dry-run]   %s | %s | %s", *row)
        return rows

    append_schedule_rows(spreadsheet, rows)
    return rows
