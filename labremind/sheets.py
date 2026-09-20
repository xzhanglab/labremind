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
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    return df


def get_next_event(
    spreadsheet,
    exact_date: Optional[date] = None,
    today: Optional[date] = None,
) -> Optional[LabEvent]:
    """Return the next upcoming event, or the event on ``exact_date``."""
    try:
        df = _schedule_df(spreadsheet)
    except Exception as exc:
        log.error("Error fetching schedule: %s", exc)
        return None
    if df.empty:
        return None

    if exact_date is not None:
        matches = df[df["Date"] == exact_date]
    else:
        today = today or datetime.now().date()
        matches = df[df["Date"] > today]
    matches = matches.dropna(subset=["Date"]).sort_values("Date")
    if matches.empty:
        return None

    row = matches.iloc[0]
    return LabEvent(
        event_date=row["Date"],
        event_type=row["Type"],
        presenter=row["Presenter(s)"],
    )


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


def append_schedule_rows(spreadsheet, rows: list[list[str]]) -> None:
    """Append generated rows [date, type, presenter] to the Schedule sheet."""
    try:
        sheet = spreadsheet.worksheet("Schedule")
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title="Schedule", rows="100", cols="10")
        sheet.append_row(["Date", "Type", "Presenter(s)"])
    sheet.append_rows(rows)
    log.info("Appended %d rows to the Schedule sheet.", len(rows))
