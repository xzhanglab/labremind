"""Tests for config loading and notify/state helpers."""

from datetime import date

from labremind.config import MeetingConfig, load_config
from labremind.notify import chunk_recipients, holiday_description, invite_description
from labremind.sheets import LabEvent
from labremind.state import mark_sent, was_sent


def _event():
    return LabEvent(event_date=date(2026, 2, 5), event_type="Data", presenter="Alice")


def _config(**overrides):
    base = dict(
        googlesheet="Sheet",
        autocreds="creds.json",
        room="Room 123",
        meeting_link="https://meet.example/1",
        contact_email="lab@example.com",
    )
    base.update(overrides)
    return MeetingConfig(**base)


def test_load_config(tmp_path):
    cfg = tmp_path / "test.cfg"
    cfg.write_text(
        "[labmeeting]\n"
        "googlesheet = Labmeeting_schedule\n"
        "autocreds = serviceaccount.json\n"
        "start_time = 09:00:00\n"
        "end_time = 10:30:00\n"
        "timezone = America/Chicago\n"
        "room = 123\n"
        "meeting_link = https://meet.example/1\n"
        "email = myemail@example.com\n"
        "holiday_vocab = Holiday, Off, Cancel\n"
        "schedule_events_count = 16\n"
        "smtp_server = smtp.gmail.com\n"
        "smtp_port = 587\n"
        "batch_size = 5\n"
        "days_ahead = 7\n"
        "\n[teams]\n"
        "webhookname = lab_events\n"
        "webhookUrl = https://example.com/hook\n"
        "maxevents = 7\n"
        "mode = workflow\n"
    )
    meeting, teams = load_config(cfg)
    assert meeting.googlesheet == "Labmeeting_schedule"
    assert meeting.holiday_vocab == ["Holiday", "Off", "Cancel"]
    assert meeting.batch_size == 5
    assert meeting.days_ahead == 7
    assert teams.webhook_url == "https://example.com/hook"
    assert teams.mode == "workflow"


def test_load_config_missing_file(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.cfg")


def test_chunk_recipients():
    assert list(chunk_recipients(["a", "b", "c"], 0)) == [["a", "b", "c"]]
    assert list(chunk_recipients(["a", "b", "c"], 1)) == [["a", "b", "c"]]
    assert list(chunk_recipients(["a", "b", "c", "d"], 2)) == [["a", "b"], ["c", "d"]]
    assert list(chunk_recipients(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]


def test_invite_description_data_vs_jc():
    body = invite_description(_event(), _config())
    assert "Alice will be presenting data" in body
    assert "Room 123" in body

    jc = LabEvent(event_date=date(2026, 2, 12), event_type="Journal Club", presenter="Bob")
    assert "journal club articles" in invite_description(jc, _config())


def test_holiday_description():
    body = holiday_description(_event(), _config())
    assert "Thursday, February 05" in body
    assert "due to Alice" in body


def test_lab_event_key_unique_per_event():
    e1 = _event()
    e2 = LabEvent(event_date=date(2026, 2, 12), event_type="Data", presenter="Alice")
    assert e1.key != e2.key


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = {}
    assert not was_sent(state, "k")
    mark_sent(state, "k")
    assert was_sent(state, "k")

    from labremind.state import load_state, save_state

    save_state(state, path)
    assert was_sent(load_state(path), "k")
    assert not was_sent(load_state(path), "other")
