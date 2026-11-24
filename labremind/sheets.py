"""Google Sheets access: authentication, schedule/event reads, schedule writes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

log = logging.getLogger(__name__)

SCHEDULE_COLUMNS = ("Date", "Type", "Presenter(s)", "Start", "End", "Timezone", "Location")

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


@dataclass
class LabEvent:
    """A single row of the Schedule sheet."""

    event_date: date
    event_type: str
    presenter: str
    start_time: str = ""
    end_time: str = ""
    time_zone: str = ""
    location: str = ""

    @property
    def key(self) -> str:
        """Stable identity used for send-dedup: one invite per event."""
        return f"{self.event_date.isoformat()}|{self.event_type}|{self.presenter}"


def get_service_account_credentials(path: str) -> Credentials:
    return Credentials.from_service_account_file(path, scopes=SHEETS_SCOPES)


def open_spreadsheet(creds: Credentials, name: str):
    """Open a spreadsheet by name; returns None and logs on failure."""
    if not name:
        log.error("No spreadsheet name provided in config.")
        return None
    try:
        spreadsheet = gspread.authorize(creds).open(name)
        log.info("Connected to spreadsheet %r.", name)
        return spreadsheet
    except gspread.SpreadsheetNotFound:
        log.error("Spreadsheet %r not found.", name)
        return None
    except Exception as exc:  # e.g. auth / network errors
        log.error("Error opening spreadsheet %r: %s", name, exc)
        return None


def _schedule_df(spreadsheet) -> pd.DataFrame:
    df = pd.DataFrame(spreadsheet.worksheet("Schedule").get_all_records())
    if df.empty:
        return df
    missing = set(SCHEDULE_COLUMNS[:3]) - set(df.columns)
    if missing:
        raise ValueError(f"Schedule sheet missing required columns: {', '.join(sorted(missing))}")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    return df


def get_schedule_events(spreadsheet) -> list[LabEvent]:
    """Read all dated Schedule rows, including optional per-event meeting details."""
    df = _schedule_df(spreadsheet)
    if df.empty:
        return []

    def cell(row, name: str) -> str:
        value = row.get(name, "")
        return "" if pd.isna(value) else str(value).strip()

    return [
        LabEvent(
            event_date=row["Date"],
            event_type=cell(row, "Type"),
            presenter=cell(row, "Presenter(s)"),
            start_time=cell(row, "Start"),
            end_time=cell(row, "End"),
            time_zone=cell(row, "Timezone"),
            location=cell(row, "Location"),
        )
        for _, row in df.dropna(subset=["Date"]).sort_values("Date").iterrows()
    ]


def get_next_event(
    spreadsheet,
    exact_date: Optional[date] = None,
    today: Optional[date] = None,
) -> Optional[LabEvent]:
    """Return the next upcoming event, or the event on ``exact_date``."""
    try:
        events = get_schedule_events(spreadsheet)
    except Exception as exc:
        log.error("Error fetching schedule: %s", exc)
        return None
    if exact_date is not None:
        return next((event for event in events if event.event_date == exact_date), None)
    today = today or datetime.now().date()
    return next((event for event in events if event.event_date > today), None)


def get_attendees(spreadsheet) -> List[str]:
    """Email addresses from the Emails sheet."""
    try:
        records = spreadsheet.worksheet("Emails").get_all_records()
    except Exception as exc:
        log.error("Error fetching Emails sheet: %s", exc)
        return []
    return [row["Email"] for row in records if row.get("Email")]


def get_rotation(spreadsheet) -> tuple[List[str], List[str]]:
    """(data rotation, journal-club rotation) name lists, blanks removed."""
    df = pd.DataFrame(spreadsheet.worksheet("Rotation").get_all_records())
    data = [x for x in df["Data rotation"].tolist() if x and str(x).strip()]
    jc = [x for x in df["JC rotation"].tolist() if x and str(x).strip()]
    return data, jc


def get_holidays(spreadsheet) -> dict:
    """Map 'YYYY-MM-DD' -> holiday name from the Holidays sheet."""
    try:
        df = pd.DataFrame(spreadsheet.worksheet("Holidays").get_all_records())
    except Exception as exc:
        log.error("Error fetching Holidays sheet: %s", exc)
        return {}
    if df.empty or "Date" not in df.columns:
        return {}
    dates = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    names = df["Holiday"] if "Holiday" in df.columns else ""
    return {d: n for d, n in zip(dates, names) if d and d != "NaT"}


def get_schedule_history(spreadsheet) -> list[dict]:
    """Existing Schedule rows as plain dicts for the schedule planner."""
    df = _schedule_df(spreadsheet)
    if df.empty:
        return []
    return [
        {"Date": row["Date"], "Type": row["Type"], "Presenter(s)": row["Presenter(s)"]}
        for _, row in df.dropna(subset=["Date"]).sort_values("Date").iterrows()
    ]


def append_schedule_rows(
    spreadsheet, rows: list[list[str]], defaults: dict[str, str] | None = None
) -> None:
    """Append planned rows, aligning fields by header instead of column position.

    If defaults are supplied, add any missing meeting-detail headers and fill
    those fields for generated meetings. Existing columns are left in place.
    """
    try:
        sheet = spreadsheet.worksheet("Schedule")
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title="Schedule", rows="100", cols="10")
        sheet.append_row(list(SCHEDULE_COLUMNS))
    headers = sheet.row_values(1)
    if defaults is not None:
        missing = [column for column in SCHEDULE_COLUMNS if column not in headers]
        if missing:
            headers.extend(missing)
            sheet.update(range_name="A1", values=[headers])
    if any(column not in headers for column in SCHEDULE_COLUMNS[:3]):
        raise ValueError("Schedule sheet must have Date, Type, and Presenter(s) headers.")

    payload = []
    for event_date, event_type, presenter in rows:
        values = {"Date": event_date, "Type": event_type, "Presenter(s)": presenter}
        if defaults is not None and event_type not in ("Holiday", "Cancel"):
            values.update(defaults)
        payload.append([values.get(header, "") for header in headers])
    if payload:
        sheet.append_rows(payload)
    log.info("Appended %d rows to the Schedule sheet.", len(rows))
