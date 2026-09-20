"""Regression tests for second-opinion review findings.

- Config defaults apply when optional keys are missing (SectionProxy
  getint accepts a positional fallback; ConfigParser.getint does not —
  this test locks in the SectionProxy path).
- BCC is an explicit config flag (bcc = true/false).
- Teams card mode uses the raw meeting URL; workflow mode uses an anchor tag.
"""

from datetime import date

import pytest

from labremind.config import load_config
from labremind.notify import handle_holiday_event, handle_regular_meeting
from labremind.sheets import LabEvent
from labremind.teams import format_event_line


def _config(**overrides):
    from labremind.config import MeetingConfig

    base = dict(
        googlesheet="Sheet",
        autocreds="creds.json",
        room="Room 123",
        meeting_link="https://meet.example/1",
        contact_email="lab@example.com",
    )
    base.update(overrides)
    return MeetingConfig(**base)


def test_config_defaults_for_missing_keys(tmp_path):
    cfg = tmp_path / "minimal.cfg"
    cfg.write_text("[labmeeting]\ngooglesheet = S\nautocreds = c.json\n")
    meeting, teams = load_config(cfg)
    assert meeting.schedule_events_count == 16
    assert meeting.smtp_server == "smtp.gmail.com"
    assert meeting.smtp_port == 587
    assert meeting.batch_size == 1
    assert meeting.days_ahead == 7
    assert meeting.bcc is False
    assert meeting.holiday_vocab == []
    assert teams.mode == "workflow"
    assert teams.max_events == 7


class FakeSMTP:
    """Captures sent messages instead of delivering them."""

    sent = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def sendmail(self, from_addr, to_addrs, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("EMAIL_USER", "bot@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    return FakeSMTP


def _to_header(raw_msg):
    for line in raw_msg.splitlines():
        if line.startswith("To:"):
            return line
    raise AssertionError("no To header in message")


def test_single_send_keeps_visible_recipients(fake_smtp):
    event = LabEvent(event_date=date(2026, 2, 5), event_type="Data", presenter="Alice")
    assert handle_regular_meeting(event, ["a@x.edu", "b@x.edu"], _config(batch_size=1))
    assert len(FakeSMTP.sent) == 1
    assert _to_header(FakeSMTP.sent[0]) == "To: a@x.edu, b@x.edu"


def test_bcc_flag_hides_recipients(fake_smtp):
    event = LabEvent(event_date=date(2026, 2, 5), event_type="Data", presenter="Alice")
    attendees = ["a@x.edu", "b@x.edu", "c@x.edu"]
    assert handle_regular_meeting(event, attendees, _config(batch_size=2, bcc=True))
    assert len(FakeSMTP.sent) == 2  # 2 + 1
    for raw in FakeSMTP.sent:
        assert _to_header(raw) == "To: bot@example.com"


def test_batching_without_bcc_keeps_visible_recipients(fake_smtp):
    event = LabEvent(event_date=date(2026, 2, 5), event_type="Data", presenter="Alice")
    attendees = ["a@x.edu", "b@x.edu", "c@x.edu"]
    assert handle_regular_meeting(event, attendees, _config(batch_size=2, bcc=False))
    assert len(FakeSMTP.sent) == 2
    assert _to_header(FakeSMTP.sent[0]) == "To: a@x.edu, b@x.edu"


def test_batched_holiday_email_hides_recipients(fake_smtp):
    event = LabEvent(event_date=date(2026, 2, 5), event_type="Holiday", presenter="Break")
    assert handle_holiday_event(event, ["a@x.edu", "b@x.edu"], _config(batch_size=1))
    assert _to_header(FakeSMTP.sent[0]) == "To: a@x.edu, b@x.edu"
    FakeSMTP.sent = []
    assert handle_holiday_event(
        event, ["a@x.edu", "b@x.edu", "c@x.edu"], _config(batch_size=2, bcc=True)
    )
    assert len(FakeSMTP.sent) == 2
    assert _to_header(FakeSMTP.sent[0]) == "To: bot@example.com"


def test_teams_card_mode_uses_raw_meeting_url():
    line = format_event_line(
        date(2026, 2, 5), "Data", "Alice", set(), "Room 123",
        "https://meet.example/1", True, mode="card",
    )
    assert "https://meet.example/1" in line
    assert "<a href" not in line


def test_teams_workflow_mode_uses_anchor_tag():
    line = format_event_line(
        date(2026, 2, 5), "Data", "Alice", set(), "Room 123",
        "https://meet.example/1", True, mode="workflow",
    )
    assert "<a href='https://meet.example/1'>Meeting Link</a>" in line


def test_legacy_zoom_config_alias(tmp_path):
    cfg = tmp_path / "legacy.cfg"
    cfg.write_text(
        "[labmeeting]\ngooglesheet = S\nautocreds = c.json\nzoom = https://zoom.example/old\n"
    )
    meeting, _ = load_config(cfg)
    assert meeting.meeting_link == "https://zoom.example/old"


def test_meeting_link_config_key_preferred_over_alias(tmp_path):
    cfg = tmp_path / "both.cfg"
    cfg.write_text(
        "[labmeeting]\ngooglesheet = S\nautocreds = c.json\n"
        "meeting_link = https://meet.example/new\nzoom = https://zoom.example/old\n"
    )
    meeting, _ = load_config(cfg)
    assert meeting.meeting_link == "https://meet.example/new"
