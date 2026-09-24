"""Email + calendar-invite delivery over SMTP.

Sends RFC-compliant iCalendar invites as email attachments, so no Google
OAuth user token is needed: a service account reads the Sheets, and an
SMTP app password sends the mail. This is the port of the previously
active ``cal_invite_no_oauth2.py`` script.
"""

from __future__ import annotations

import logging
import os
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Iterable, Iterator, List
from uuid import uuid4
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, vCalAddress, vText

from .config import MeetingConfig
from .sheets import LabEvent

log = logging.getLogger(__name__)


def chunk_recipients(recipients: List[str], batch_size: int) -> Iterator[List[str]]:
    """Yield successive batches; batch_size <= 0 means a single batch."""
    if not batch_size or batch_size <= 0:
        yield list(recipients)
        return
    for i in range(0, len(recipients), batch_size):
        yield recipients[i : i + batch_size]


def _smtp_credentials() -> tuple[str | None, str | None]:
    return os.getenv("EMAIL_USER"), os.getenv("EMAIL_PASSWORD")


def _base_headers(msg, sender_email: str) -> None:
    msg["Message-ID"] = make_msgid()
    msg["Date"] = formatdate(localtime=True)


def send_plain_email(
    recipients: List[str],
    subject: str,
    body: str,
    smtp_server: str = "smtp.gmail.com",
    smtp_port: int = 587,
    dry_run: bool = False,
    bcc: bool = False,
) -> bool:
    """Send a plain-text email to ``recipients``.

    With ``bcc=True`` the message is addressed to the sender itself so
    batched recipients can't see each other.
    """
    sender_email, password = _smtp_credentials()
    if not sender_email or not password:
        log.error("Missing EMAIL_USER / EMAIL_PASSWORD environment variables.")
        return False

    if dry_run:
        log.info("[dry-run] Would send email %r to %d recipient(s).", subject, len(recipients))
        return True

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = sender_email if bcc else ", ".join(recipients)
        msg["User-Agent"] = "Mozilla Thunderbird"
        _base_headers(msg, sender_email)

        time.sleep(2.5)  # avoid sending immediately after SMTP connect
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            time.sleep(0.5)  # small delay helps with some strict servers
            server.login(sender_email, password)
            time.sleep(0.5)
            server.sendmail(sender_email, recipients, msg.as_string())

        log.info("Email sent successfully via SMTP.")
        return True
    except Exception as exc:
        log.error("SMTP email error: %s", exc)
        return False


def send_calendar_invite(
    recipients: List[str],
    subject: str,
    event: LabEvent,
    config: MeetingConfig,
    description: str,
    dry_run: bool = False,
    bcc: bool = False,
) -> bool:
    """Send an iCalendar REQUEST invite as an email attachment.

    With ``bcc=True`` the message is addressed to the sender itself so
    batched recipients can't see each other.
    """
    sender_email, password = _smtp_credentials()
    if not sender_email or not password:
        log.error("Missing EMAIL_USER / EMAIL_PASSWORD environment variables.")
        return False

    if dry_run:
        log.info(
            "[dry-run] Would send calendar invite %r (%s) to %d recipient(s).",
            subject,
            event.event_date.isoformat(),
            len(recipients),
        )
        return True

    try:
        tz = ZoneInfo(config.timezone)
        start = datetime.combine(
            event.event_date, datetime.strptime(config.start_time, "%H:%M:%S").time()
        ).replace(tzinfo=tz)
        end = datetime.combine(
            event.event_date, datetime.strptime(config.end_time, "%H:%M:%S").time()
        ).replace(tzinfo=tz)
    except (ValueError, KeyError) as exc:
        log.error("Date/time parsing error: %s", exc)
        return False

    cal = Calendar()
    cal.add("prodid", "-//XZLab//Lab Meeting Scheduler//EN")
    cal.add("version", "2.0")
    cal.add("method", "REQUEST")

    cal_event = Event()
    cal_event.add("summary", subject)
    cal_event.add("dtstart", start)
    cal_event.add("dtend", end)
    cal_event.add("dtstamp", datetime.now(timezone.utc))
    cal_event.add("uid", str(uuid4()))
    cal_event.add("location", vText(f"{config.room}, {config.meeting_link}"))
    cal_event.add("description", vText(description))
    cal_event["dtstart"].params["tzid"] = vText(config.timezone)
    cal_event["dtend"].params["tzid"] = vText(config.timezone)

    organizer = vCalAddress(f"mailto:{sender_email}")
    organizer.params["cn"] = vText("XZLab Bot")
    cal_event.add("organizer", organizer)

    for email in recipients:
        attendee = vCalAddress(f"mailto:{email}")
        attendee.params["cn"] = vText(email)
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        attendee.params["RSVP"] = vText("TRUE")
        cal_event.add("attendee", attendee, encode=0)

    cal.add_component(cal_event)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"XZLab Bot <{sender_email}>"
    msg["To"] = sender_email if bcc else ", ".join(recipients)
    msg["User-Agent"] = "Microsoft Outlook 16.0"
    # Helps Outlook recognize the message as a calendar invite.
    msg.add_header("Content-Class", "urn:content-classes:calendarmessage")
    _base_headers(msg, sender_email)

    msg.attach(MIMEText(description, "plain"))
    cal_part = MIMEText(cal.to_ical().decode("utf-8"), _subtype="calendar", _charset="utf-8")
    cal_part.replace_header("Content-Type", 'text/calendar; charset="utf-8"; method=REQUEST')
    cal_part.add_header("Content-Disposition", 'inline; filename="invite.ics"')
    msg.attach(cal_part)

    time.sleep(2)
    try:
        with smtplib.SMTP(config.smtp_server, config.smtp_port) as server:
            server.starttls()
            time.sleep(0.5)
            server.login(sender_email, password)
            time.sleep(0.5)
            server.sendmail(sender_email, recipients, msg.as_string())
        log.info("Calendar invite sent to %d attendees.", len(recipients))
        return True
    except smtplib.SMTPAuthenticationError:
        log.error("SMTP auth failed. Check EMAIL_USER and EMAIL_PASSWORD.")
        return False
    except Exception as exc:
        log.error("SMTP error sending calendar invite: %s", exc)
        return False


def invite_description(event: LabEvent, config: MeetingConfig) -> str:
    kind = "data" if event.event_type == "Data" else "journal club articles"
    return f"""
Hi Lab,

{event.presenter} will be presenting {kind} at our next lab meeting.

Meeting will be held in {config.room} and virtually at {config.meeting_link}.

If you have any questions regarding scheduling, let {config.contact_email} know.

Best,
XZLab Bot
"""


def holiday_description(event: LabEvent, config: MeetingConfig) -> str:
    date_str = event.event_date.strftime("%A, %B %d")
    return f"""Hi Lab,

Just a reminder: we will not have lab meeting on {date_str} due to {event.presenter}.

If you have any questions regarding scheduling, let {config.contact_email} know.

Best,
XZLab Bot
"""


def handle_holiday_event(
    event: LabEvent, attendees: List[str], config: MeetingConfig, dry_run: bool = False
) -> bool:
    """Send a plain-text 'no meeting' reminder, honoring batch_size."""
    log.info("Holiday/break event (%s): sending reminder email.", event.presenter)
    date_str = event.event_date.strftime("%A, %B %d")
    subject = f"[XZ Lab Meeting]: No lab meeting on {date_str}"
    body = holiday_description(event, config)

    ok = True
    batches = list(chunk_recipients(attendees, config.batch_size))
    bcc = config.bcc
    for i, batch in enumerate(batches, 1):
        log.info("Sending holiday batch %d/%d (%d recipients).", i, len(batches), len(batch))
        ok &= send_plain_email(
            batch, subject, body, config.smtp_server, config.smtp_port, dry_run=dry_run, bcc=bcc
        )
        time.sleep(2)
    return ok


def handle_regular_meeting(
    event: LabEvent, attendees: List[str], config: MeetingConfig, dry_run: bool = False
) -> bool:
    """Send a batched iCalendar invite for a regular meeting."""
    log.info("Regular meeting with %r: sending calendar invite.", event.presenter)
    subject = f"[XZ Lab Meeting]: {event.presenter} | {event.event_type}"
    description = invite_description(event, config)

    ok = True
    batches = list(chunk_recipients(attendees, config.batch_size))
    bcc = config.bcc
    for i, batch in enumerate(batches, 1):
        log.info("Sending invite batch %d/%d (%d recipients).", i, len(batches), len(batch))
        ok &= send_calendar_invite(
            batch, subject, event, config, description, dry_run=dry_run, bcc=bcc
        )
        time.sleep(2)
    return ok
