"""Command-line interface: one entry point for all labremind commands."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .config import load_config
from .notify import handle_holiday_event, handle_regular_meeting
from .schedule import generate_schedule
from .sheets import (
    get_attendees,
    get_next_event,
    get_service_account_credentials,
    open_spreadsheet,
)
from .state import DEFAULT_STATE_FILE, load_state, mark_sent, save_state, was_sent
from .teams import send_teams

log = logging.getLogger(__name__)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="cal_config.cfg", help="Path to config file.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without sending anything or writing to Sheets.",
    )


def cmd_invite(args) -> int:
    meeting, _ = load_config(args.config)
    spreadsheet = open_spreadsheet(get_service_account_credentials(meeting.autocreds), meeting.googlesheet)
    if spreadsheet is None:
        return 1

    target_date = None
    if args.auto:
        target_date = datetime.now().date() + timedelta(days=meeting.days_ahead)
        log.info("Auto mode: looking for an event on %s.", target_date)

    event = get_next_event(spreadsheet, exact_date=target_date)
    if event is None:
        log.info("No event found%s.", f" for {target_date}" if target_date else "")
        return 0
    log.info("Found event: %r (%s) on %s.", event.presenter, event.event_type, event.event_date)

    state = load_state(args.state_file)
    if not args.force and was_sent(state, event.key):
        log.info("Already notified for %s; skipping (use --force to resend).", event.key)
        return 0

    attendees = get_attendees(spreadsheet)
    if not attendees:
        log.warning("No attendees found in the 'Emails' sheet.")

    if event.event_type in meeting.holiday_vocab:
        ok = handle_holiday_event(event, attendees, meeting, dry_run=args.dry_run)
    else:
        ok = handle_regular_meeting(event, attendees, meeting, dry_run=args.dry_run)

    if ok and not args.dry_run:
        mark_sent(state, event.key)
        save_state(state, args.state_file)
    return 0 if ok else 1


def cmd_teams(args) -> int:
    meeting, teams = load_config(args.config)
    spreadsheet = open_spreadsheet(get_service_account_credentials(meeting.autocreds), meeting.googlesheet)
    if spreadsheet is None:
        return 1
    ok = send_teams(spreadsheet, meeting, teams, dry_run=args.dry_run)
    return 0 if ok else 1


def cmd_generate_schedule(args) -> int:
    meeting, _ = load_config(args.config)
    spreadsheet = open_spreadsheet(get_service_account_credentials(meeting.autocreds), meeting.googlesheet)
    if spreadsheet is None:
        return 1
    generate_schedule(
        spreadsheet,
        limit=args.limit or meeting.schedule_events_count,
        dry_run=args.dry_run,
        data_per_jc=meeting.data_per_jc,
        num_jc_presenters=meeting.num_jc_presenters,
        meeting_weekday=meeting.meeting_weekday,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="labremind",
        description="Lab meeting reminders: calendar invites and Teams notifications from Google Sheets.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--log-file", default=None, help="Also write logs to this file.")

    sub = parser.add_subparsers(dest="command", required=True)

    p_invite = sub.add_parser("invite", help="Send a calendar invite (or holiday notice) for an event.")
    _add_common(p_invite)
    p_invite.add_argument("--auto", action="store_true",
                          help="Only act on the event `days_ahead` days out (for cron).")
    p_invite.add_argument("--force", action="store_true",
                          help="Resend even if this event was already notified.")
    p_invite.add_argument("--state-file", default=DEFAULT_STATE_FILE,
                          help="File tracking already-sent invites.")
    p_invite.set_defaults(func=cmd_invite)

    p_teams = sub.add_parser("teams", help="Post upcoming meetings to Microsoft Teams.")
    _add_common(p_teams)
    p_teams.set_defaults(func=cmd_teams)

    p_gen = sub.add_parser("generate-schedule", help="Generate schedule rows from the rotation.")
    _add_common(p_gen)
    p_gen.add_argument("--limit", type=int, default=None,
                       help="Number of events to generate (default: schedule_events_count).")
    p_gen.set_defaults(func=cmd_generate_schedule)

    return parser


def main(argv=None) -> int:
    # Secrets come from .env next to the repo root (or the environment).
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    args = build_parser().parse_args(argv)
    handlers = [logging.StreamHandler(sys.stderr)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level: log and exit non-zero for cron
        log.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
