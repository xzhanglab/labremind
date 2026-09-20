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
from datetime import datetime
from typing import List

import pandas as pd
import requests

from .config import MeetingConfig, TeamsConfig

log = logging.getLogger(__name__)


def get_upcoming_events(spreadsheet, num_events: int) -> pd.DataFrame:
    """Next ``num_events`` schedule rows after today, as a DataFrame."""
    df = pd.DataFrame(spreadsheet.worksheet("Schedule").get_all_records()).copy()
    if df.empty:
        return df
    df = df.dropna(subset=["Date"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    today = pd.to_datetime(datetime.today().strftime("%Y-%m-%d"))
    df = df[df["Date"] > today].sort_values("Date")
    if not df.empty:
        df = df.iloc[: int(num_events)].reset_index(drop=True)
    return df


def format_event_line(
    event_date,
    topic: str,
    member: str,
    holiday_vocab: set,
    location: str,
    meeting_link: str,
    first: bool,
    mode: str = "workflow",
) -> str:
    """One HTML line for the Teams message; the first event gets location info.

    Card mode uses the raw meeting URL (pymsteams cards render anchor tags
    unpredictably); workflow mode uses an HTML link.
    """
    formatted = pd.to_datetime(event_date).strftime("%Y-%m-%d")
    if pd.isna(member) or topic in holiday_vocab:
        return f"<strong>{formatted}</strong> <font color='red'>{topic} - {member}</font>"
    if first:
        if mode == "card":
            return (
                f"<strong>{formatted}</strong> {member} | {topic} "
                f"(location <strong>{location}</strong> and {meeting_link})"
            )
        return (
            f"<strong>{formatted}</strong> {member} | {topic} "
            f"(location <strong>{location}</strong> and "
            f"<a href='{meeting_link}'>Meeting Link</a>)"
        )
    return f"<strong>{formatted}</strong> {member} | {topic}"


def build_lines(events: pd.DataFrame, meeting: MeetingConfig, mode: str = "workflow") -> List[str]:
    holiday_vocab = set(meeting.holiday_vocab)
    lines, first = [], True
    for _, row in events.iterrows():
        lines.append(
            format_event_line(
                row["Date"],
                row["Type"],
                row["Presenter(s)"],
                holiday_vocab,
                meeting.room,
                meeting.meeting_link,
                first,
                mode,
            )
        )
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
    events = get_upcoming_events(spreadsheet, teams.max_events)
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
