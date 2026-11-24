"""Microsoft Teams notifications.

Two backends, selected by ``[teams] mode`` in the config:

- ``workflow`` (default): POSTs a JSON payload to a Power Automate webhook.
  Dependency-free and future-proof — Microsoft retired the classic
  Office 365 connector webhooks this replaced.
- ``card``: builds a connector card with ``pymsteams`` and POSTs it to a
  classic incoming webhook. Kept for existing setups.
"""

from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from html import escape
from typing import List

import pandas as pd
import requests

from .config import MeetingConfig, TeamsConfig
from .sheets import LabEvent, get_schedule_events
from .timing import resolve_event_details

log = logging.getLogger(__name__)

def get_upcoming_events(
    spreadsheet,
    num_events: int,
    meeting: MeetingConfig,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Return the next num_events meetings that haven't started."""
    now = now or datetime.now(timezone.utc)
    upcoming = []

    for event in get_schedule_events(spreadsheet):
        details = resolve_event_details(event, meeting)
        local_now = now.astimezone(details.start.tzinfo)

        if event.event_type in meeting.holiday_vocab:
            if event.event_date < local_now.date():
                continue
        elif details.start <= now:
            continue

        upcoming.append((details.start, event))

    upcoming.sort(key=lambda item: item[0])

    return pd.DataFrame(
        [
            {
                "Date": event.event_date,
                "Type": event.event_type,
                "Presenter(s)": event.presenter,
                "Start": event.start_time,
                "End": event.end_time,
                "Timezone": event.time_zone,
                "Location": event.location,
            }
            for _, event in upcoming[:int(num_events)]
        ]
    )

def format_event_line(
    event_date,
    topic: str,
    member: str,
    holiday_vocab: set,
    location: str,
    meeting_link: str,
    first: bool,
    mode: str = "workflow",
    time_label: str = "",
) -> str:
    """One HTML line for the Teams message; every event gets its own details.

    Card mode uses the raw meeting URL (pymsteams cards render anchor tags
    unpredictably); workflow mode uses an HTML link on the first meeting.
    """
    formatted = pd.to_datetime(event_date).strftime("%Y-%m-%d")
    if pd.isna(member) or topic in holiday_vocab:
        return f"<strong>{formatted}</strong> <font color='red'>{escape(str(topic))} - {escape(str(member))}</font>"
    line = f"<strong>{formatted}</strong> {escape(str(member))} | {escape(str(topic))}"
    if time_label:
        line += f" ({escape(time_label)})"
    parts = []
    if location:
        parts.append(f"location <strong>{escape(location)}</strong>")
    if first and meeting_link:
        if mode == "card":
            parts.append(escape(meeting_link))
        else:
            parts.append(f"<a href='{escape(meeting_link, quote=True)}'>Meeting Link</a>")
    if parts:
        line += f" ({' and '.join(parts)})"
    return line


def build_lines(events: pd.DataFrame, meeting: MeetingConfig, mode: str = "workflow") -> List[str]:
    holiday_vocab = set(meeting.holiday_vocab)
    lines, first = [], True
    for _, row in events.iterrows():
        def cell(name: str) -> str:
            value = row.get(name, "")
            return "" if pd.isna(value) else str(value).strip()

        is_holiday = row["Type"] in holiday_vocab
        time_label, location = "", ""
        if not is_holiday:
            event = LabEvent(
                event_date=pd.to_datetime(row["Date"]).date(),
                event_type=row["Type"],
                presenter=row["Presenter(s)"],
                start_time=cell("Start"),
                end_time=cell("End"),
                time_zone=cell("Timezone"),
                location=cell("Location"),
            )
            details = resolve_event_details(event, meeting)
            time_label = (
                f"{details.start:%H:%M}–{details.end:%H:%M} {details.time_zone}"
            )
            location = details.location
        lines.append(
            format_event_line(
                row["Date"],
                row["Type"],
                row["Presenter(s)"],
                holiday_vocab,
                location,
                meeting.meeting_link,
                first,
                mode,
                time_label=time_label,
            )
        )
        if not is_holiday:
            first = False
    return lines


def send_workflow(
    teams: TeamsConfig, lines: List[str], dry_run: bool = False
) -> bool:
    """POST a simple JSON payload to a Power Automate webhook."""
    payload = {
        "title": "Upcoming Lab Meeting Schedule",
        "message_list": "<br>".join(lines),
        "sender": teams.webhook_name,
        "date_sent": datetime.today().strftime("%Y-%m-%d"),
    }
    if dry_run:
        log.info("[dry-run] Would POST workflow payload to Teams: %s", payload["title"])
        for line in lines:
            log.info("[dry-run]   %s", line)
        return True
    try:
        response = requests.post(teams.webhook_url, json=payload, timeout=10)
    except requests.exceptions.RequestException as exc:
        log.error("Connection error posting to Teams workflow: %s", exc)
        return False
    if response.status_code not in (200, 202):
        log.error("Teams workflow failed: %s - %s", response.status_code, response.text)
        return False
    log.info("Teams workflow message sent successfully.")
    return True


def send_card(
    teams: TeamsConfig, meeting: MeetingConfig, lines: List[str], dry_run: bool = False
) -> bool:
    """POST a pymsteams connector card to a classic incoming webhook."""
    import pymsteams

    if dry_run:
        log.info("[dry-run] Would send Teams connector card with %d sections.", len(lines))
        return True

    card = pymsteams.connectorcard(hookurl=teams.webhook_url)
    card.title("Upcoming Lab Meeting Schedule")
    card.text(" ")
    for line in lines:
        section = pymsteams.cardsection()
        section.text(line)
        card.addSection(section)
    try:
        payload = card.payload
        response = requests.post(
            teams.webhook_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=10,
        )
    except requests.exceptions.RequestException as exc:
        log.error("Connection error posting Teams card: %s", exc)
        return False
    if response.status_code != 200:
        log.error("Teams webhook failed: %s - %s", response.status_code, response.text)
        return False
    log.info("Teams card sent successfully.")
    return True


def send_teams(
    spreadsheet,
    meeting: MeetingConfig,
    teams: TeamsConfig,
    dry_run: bool = False,
) -> bool:
    """Fetch upcoming events and notify Teams via the configured backend."""
    if not teams.webhook_url:
        log.error("No Teams webhook URL configured ([teams] webhookUrl).")
        return False
    events = get_upcoming_events(spreadsheet, teams.max_events, meeting)
    if events.empty:
        log.info("No upcoming events found in the schedule.")
        return True
    lines = build_lines(events, meeting, teams.mode)
    if teams.mode == "card":
        return send_card(teams, meeting, lines, dry_run=dry_run)
    if teams.mode == "workflow":
        return send_workflow(teams, lines, dry_run=dry_run)
    log.error("Unknown [teams] mode %r (expected 'workflow' or 'card').", teams.mode)
    return False
