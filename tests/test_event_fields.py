"""Per-row meeting details and daily idempotent invitation scans."""

from datetime import date, datetime, timezone
from email import message_from_string
from types import SimpleNamespace

import pandas as pd
import pytest
from icalendar import Calendar

from labremind import cli
from labremind.config import MeetingConfig
from labremind.notify import send_calendar_invite
from labremind.schedule import generate_schedule
from labremind.sheets import LabEvent, append_schedule_rows, get_schedule_events
from labremind.state import load_state, was_sent
from labremind.teams import build_lines
from labremind.timing import resolve_event_details


def _config(**kwargs):
    values = dict(
        googlesheet="Lab Schedule",
        autocreds="credentials.json",
        start_time="09:00:00",
        end_time="10:30:00",
        timezone="America/Chicago",
        room="Default Room",
        meeting_link="https://meet.example/test",
        holiday_vocab=["Holiday"],
    )
    values.update(kwargs)
    return MeetingConfig(**values)


class FakeScheduleWorksheet:
    def __init__(self, headers, records=None):
        self.headers = list(headers)
        self.records = records or []
        self.appended = []
        self.updated = []

    def get_all_records(self):
        return self.records

    def row_values(self, row):
        assert row == 1
        return self.headers.copy()

    def update(self, *, range_name, values):
        self.updated.append((range_name, values))
        self.headers = list(values[0])

    def append_rows(self, rows):
        self.appended.extend(rows)


class FakeSpreadsheet:
    def __init__(self, worksheet):
        self.sheet = worksheet

    def worksheet(self, name):
        assert name == "Schedule"
        return self.sheet


def test_read_schedule_with_new_fields_and_legacy_rows():
    records = [
        {"Date": "2026-12-10", "Type": "Data", "Presenter(s)": "Bob", "Start": "1:00 PM",
         "End": "2:30 PM", "Timezone": "America/New_York", "Location": "Room B"},
        {"Date": "2026-12-03", "Type": "Data", "Presenter(s)": "Alice", "Start": "",
         "End": "", "Timezone": "", "Location": ""},
    ]
    worksheet = FakeScheduleWorksheet(list(records[0]), records)
    events = get_schedule_events(FakeSpreadsheet(worksheet))
    assert [e.presenter for e in events] == ["Alice", "Bob"]
    assert events[0].start_time == ""
    assert resolve_event_details(events[0], _config()).location == "Default Room"
    details = resolve_event_details(events[1], _config())
    assert (details.start.hour, details.end.hour) == (13, 14)
    assert details.time_zone == "America/New_York"
    assert details.location == "Room B"


def test_missing_columns_fall_back_to_config():
    worksheet = FakeScheduleWorksheet(
        ["Date", "Type", "Presenter(s)"],
        [{"Date": "2026-12-03", "Type": "Data", "Presenter(s)": "Alice"}],
    )
    event = get_schedule_events(FakeSpreadsheet(worksheet))[0]
    details = resolve_event_details(event, _config())
    assert details.start.strftime("%H:%M") == "09:00"
    assert details.end.strftime("%H:%M") == "10:30"
    assert details.time_zone == "America/Chicago"


def test_invalid_time_zone_and_end_before_start_rejected():
    event = LabEvent(date(2026, 12, 3), "Data", "Alice", time_zone="CST")
    with pytest.raises(ValueError, match="IANA"):
        resolve_event_details(event, _config())

    event = LabEvent(date(2026, 12, 3), "Data", "Alice", start_time="11:00", end_time="10:00")
    with pytest.raises(ValueError, match="End must be after Start"):
        resolve_event_details(event, _config())

    event = LabEvent(date(2026, 12, 3), "Data", "Alice", start_time="not a time")
    with pytest.raises(ValueError, match="Invalid Start"):
        resolve_event_details(event, _config())


def test_timezone_dst_date_is_resolved_correctly():
    winter = LabEvent(date(2026, 1, 8), "Data", "Alice")
    summer = LabEvent(date(2026, 7, 9), "Data", "Alice")
    assert resolve_event_details(winter, _config()).start.utcoffset().total_seconds() == -21600
    assert resolve_event_details(summer, _config()).start.utcoffset().total_seconds() == -18000


def test_append_generated_schedule_adds_headers_and_aligns_existing_columns():
    worksheet = FakeScheduleWorksheet(["Presenter(s)", "Date", "Notes", "Type"])
    append_schedule_rows(
        FakeSpreadsheet(worksheet),
        [["2026-12-03", "Data", "Alice"], ["2026-12-10", "Holiday", "Break"]],
        defaults={
            "Start": "09:00:00",
            "End": "10:30:00",
            "Timezone": "America/Chicago",
            "Location": "Room 123",
        },
    )
    assert worksheet.headers == [
        "Presenter(s)", "Date", "Notes", "Type",
        "Start", "End", "Timezone", "Location"
    ]
    assert worksheet.updated[0][0] == "A1"
    assert worksheet.appended[0] == [
        "Alice", "2026-12-03", "", "Data",
        "09:00:00", "10:30:00", "America/Chicago", "Room 123"
    ]
    assert worksheet.appended[1] == [
        "Break", "2026-12-10", "", "Holiday", "", "", "", ""
    ]


def test_generate_schedule_forwards_meeting_defaults(monkeypatch):
    import labremind.sheets as sheets

    written = []
    monkeypatch.setattr(sheets, "get_rotation", lambda _book: (["Alice"], ["Bob", "Carol"]))
    monkeypatch.setattr(sheets, "get_holidays", lambda _book: {})
    monkeypatch.setattr(sheets, "get_schedule_history", lambda _book: [])
    monkeypatch.setattr(
        sheets, "append_schedule_rows",
        lambda _book, rows, defaults=None: written.append((rows, defaults)),
    )
    defaults = {
        "Start": "09:00",
        "End": "10:30",
        "Timezone": "America/Chicago",
        "Location": "Room 1",
    }
    rows = generate_schedule(
        object(),
        limit=1,
        today=date(2026, 1, 1),
        meeting_defaults=defaults,
    )
    assert written == [(rows, defaults)]


def test_calendar_invite_uses_row_specific_details(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self):
            pass

        def login(self, *args):
            pass

        def sendmail(self, _sender, _recipients, message):
            sent.append(message)

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setenv("EMAIL_USER", "bot@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")

    event = LabEvent(
        date(2026, 12, 3),
        "Data",
        "Alice",
        start_time="13:00",
        end_time="14:30",
        time_zone="America/New_York",
        location="Conference Room B",
    )

    assert send_calendar_invite(
        ["a@example.com"], "Meeting", event, _config(), "Details"
    )

    message = message_from_string(sent[0])
    part = next(
        part for part in message.walk()
        if part.get_content_type() == "text/calendar"
    )
    calendar = Calendar.from_ical(part.get_payload(decode=True))
    ical_event = next(
        component for component in calendar.walk()
        if component.name == "VEVENT"
    )

    assert ical_event.decoded("DTSTART").hour == 13
    assert ical_event.decoded("DTEND").hour == 14
    assert str(ical_event["DTSTART"].params["TZID"]) == "America/New_York"
    assert "Conference Room B" in str(ical_event["LOCATION"])
    assert "Default Room" not in str(ical_event["LOCATION"])


def test_teams_digest_uses_each_event_details():
    events = pd.DataFrame([
        {
            "Date": "2026-12-03", "Type": "Data",
            "Presenter(s)": "Alice", "Start": "09:00",
            "End": "10:30", "Timezone": "America/Chicago",
            "Location": "Room A",
        },
        {
            "Date": "2026-12-10", "Type": "Data",
            "Presenter(s)": "Bob", "Start": "1:00 PM",
            "End": "2:00 PM", "Timezone": "America/New_York",
            "Location": "Room B",
        },
    ])

    lines = build_lines(events, _config())
    assert "09:00–10:30 America/Chicago" in lines[0]
    assert "Room A" in lines[0]
    assert "13:00–14:00 America/New_York" in lines[1]
    assert "Room B" in lines[1]
    assert "Default Room" not in " ".join(lines)
    assert "Meeting Link" in lines[0] and "Meeting Link" not in lines[1]


def test_auto_window_includes_seven_days_and_today_but_not_past_starts():
    now = datetime(2026, 11, 23, 13, tzinfo=timezone.utc)
    events = [
        LabEvent(date(2026, 11, 22), "Data", "Old"),
        LabEvent(date(2026, 11, 23), "Data", "Already begun", start_time="06:00"),
        LabEvent(date(2026, 11, 23), "Data", "Today"),
        LabEvent(date(2026, 11, 30), "Data", "Seven days"),
        LabEvent(date(2026, 12, 1), "Data", "Eight days"),
    ]

    assert [
        e.presenter for e in cli.select_upcoming_events(events, _config(), days_ahead=7, now=now)
    ] == ["Today", "Seven days"]


def test_daily_auto_dedup_sends_multiple_events_once(tmp_path, monkeypatch):
    meeting = _config()
    events = [
        LabEvent(date(2026, 11, 24), "Data", "Alice"),
        LabEvent(date(2026, 11, 27), "Data", "Bob"),
        LabEvent(date(2026, 11, 24), "Data", "Alice"),
    ]
    sent = []

    monkeypatch.setattr(cli, "load_config", lambda path: (meeting, None))
    monkeypatch.setattr(cli, "get_service_account_credentials", lambda path: object())
    monkeypatch.setattr(cli, "open_spreadsheet", lambda *args: object())
    monkeypatch.setattr(cli, "get_schedule_events", lambda _sheet: events)
    monkeypatch.setattr(cli, "get_attendees", lambda _sheet: ["lab@example.com"])
    monkeypatch.setattr(
        cli,
        "handle_regular_meeting",
        lambda event, *a, **kw: sent.append(event.key) or True,
    )

    actual_selector = cli.select_upcoming_events
    monkeypatch.setattr(
        cli,
        "select_upcoming_events",
        lambda ev, cfg, days_ahead=None: actual_selector(
            ev, cfg, days_ahead=days_ahead, now=datetime(2026, 11, 23, 13, tzinfo=timezone.utc)
        ),
    )

    args = SimpleNamespace(
        config="unused",
        auto=True,
        force=False,
        dry_run=False,
        state_file=tmp_path / "state.json",
    )

    assert cli.cmd_invite(args) == 0
    assert sent == [event.key for event in events[:2]]
    assert all(
        was_sent(load_state(args.state_file), event.key)
        for event in events
    )
    assert cli.cmd_invite(args) == 0
    assert len(sent) == 2
