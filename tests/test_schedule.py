"""Unit tests for the schedule planner (no Google API access needed)."""

from datetime import date

import pytest

from labremind.schedule import (
    data_streak,
    is_holiday_thursday,
    next_presenter_index,
    next_thursday_after,
    plan_schedule,
    start_date_from_history,
)


def test_next_thursday_after():
    # Thursday -> the following Thursday (strictly after)
    assert next_thursday_after(date(2026, 1, 1)) == date(2026, 1, 8)
    # Wednesday -> next day
    assert next_thursday_after(date(2026, 1, 7)) == date(2026, 1, 8)
    # Friday -> six days later
    assert next_thursday_after(date(2026, 1, 2)) == date(2026, 1, 8)


def test_federal_holidays():
    assert is_holiday_thursday(date(2026, 1, 1), {})[0]  # first Thursday of Jan
    assert is_holiday_thursday(date(2026, 1, 8), {}) == (False, "")  # second Thursday: not a holiday
    assert is_holiday_thursday(date(2026, 11, 26), {})[0]  # Thanksgiving
    assert is_holiday_thursday(date(2026, 12, 31), {})[0]  # last Thursday of Dec
    assert is_holiday_thursday(date(2026, 12, 24), {}) == (False, "")
    # July 4 2026 is a Saturday, so no Thursday holiday that year
    assert is_holiday_thursday(date(2026, 7, 2), {}) == (False, "")


def test_july_fourth_on_thursday():
    # July 4 2024 was a Thursday
    assert is_holiday_thursday(date(2024, 7, 4), {})[0]


def test_custom_holidays():
    custom = {"2026-02-12": "Lab retreat"}
    assert is_holiday_thursday(date(2026, 2, 12), custom) == (True, "Lab retreat")
    assert is_holiday_thursday(date(2026, 2, 19), custom) == (False, "")


def test_next_presenter_index_empty_history():
    assert next_presenter_index([], "Data", ["A", "B"]) == 0


def test_next_presenter_index_from_history():
    history = [
        {"Type": "Data", "Presenter(s)": "Alice"},
        {"Type": "Data", "Presenter(s)": "Bob"},
    ]
    assert next_presenter_index(history, "Data", ["Alice", "Bob", "Carol"]) == 2


def test_next_presenter_index_wraps():
    history = [{"Type": "Data", "Presenter(s)": "Carol"}]
    assert next_presenter_index(history, "Data", ["Alice", "Bob", "Carol"]) == 0


def test_next_presenter_index_jc_pair_uses_last_name():
    history = [{"Type": "Journal Club", "Presenter(s)": "Dave, Erin"}]
    assert next_presenter_index(history, "Journal Club", ["Dave", "Erin", "Frank"]) == 2


def test_next_presenter_index_unknown_name_starts_over():
    history = [{"Type": "Data", "Presenter(s)": "Zed"}]
    assert next_presenter_index(history, "Data", ["Alice", "Bob"]) == 0


def test_data_streak():
    assert data_streak([]) == 0
    history = [
        {"Type": "Data", "Presenter(s)": "A"},
        {"Type": "Holiday", "Presenter(s)": "X"},
        {"Type": "Data", "Presenter(s)": "B"},
        {"Type": "Data", "Presenter(s)": "C"},
    ]
    assert data_streak(history) == 3  # holidays don't break the cadence
    history.append({"Type": "Journal Club", "Presenter(s)": "D, E"})
    assert data_streak(history) == 0


def test_plan_schedule_cadence():
    rows = plan_schedule(
        start_date=date(2026, 1, 1),
        rotation_data=["Alice", "Bob", "Carol"],
        rotation_jc=["Dave", "Erin", "Frank", "Grace"],
        custom_holidays={},
        history=[],
        limit=5,
    )
    assert [r[1] for r in rows] == ["Data", "Data", "Data", "Journal Club", "Data"]
    assert [r[2] for r in rows] == ["Alice", "Bob", "Carol", "Dave, Erin", "Alice"]
    # Thursdays, one week apart
    assert rows[0][0] == "2026-01-08"
    assert rows[1][0] == "2026-01-15"


def test_plan_schedule_skips_holidays_without_breaking_rotation():
    rows = plan_schedule(
        start_date=date(2026, 1, 1),
        rotation_data=["Alice", "Bob"],
        rotation_jc=["Dave", "Erin"],
        custom_holidays={"2026-01-15": "Lab retreat"},
        history=[],
        limit=4,
    )
    assert rows[0] == ["2026-01-08", "Data", "Alice"]
    assert rows[1] == ["2026-01-15", "Holiday", "Lab retreat"]
    assert rows[2] == ["2026-01-22", "Data", "Bob"]  # rotation continues past the holiday
    assert rows[3] == ["2026-01-29", "Data", "Alice"]


def test_plan_schedule_resumes_from_history():
    history = [
        {"Date": date(2026, 1, 8), "Type": "Data", "Presenter(s)": "Alice"},
        {"Date": date(2026, 1, 15), "Type": "Data", "Presenter(s)": "Bob"},
    ]
    rows = plan_schedule(
        start_date=start_date_from_history(history),
        rotation_data=["Alice", "Bob", "Carol"],
        rotation_jc=["Dave", "Erin"],
        custom_holidays={},
        history=history,
        limit=2,
    )
    # Continues after the last scheduled date, next in rotation
    assert rows[0] == ["2026-01-22", "Data", "Carol"]
    assert rows[1][1] == "Journal Club"


def test_plan_schedule_requires_rotations():
    with pytest.raises(ValueError):
        plan_schedule(date(2026, 1, 1), [], ["Dave"], {}, [], 4)


def test_start_date_from_history_empty():
    assert start_date_from_history([], today=date(2026, 3, 1)) == date(2026, 3, 1)
