"""Tiny persistent record of which events were already notified.

A cron job that runs twice (or is re-run by hand) must not send duplicate
invites. We record each sent event's key in a small JSON file next to the
working directory.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_STATE_FILE = ".labremind_state.json"


def load_state(path: str | Path = DEFAULT_STATE_FILE) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read state file %s: %s", path, exc)
        return {}


def save_state(state: dict, path: str | Path = DEFAULT_STATE_FILE) -> None:
    Path(path).write_text(json.dumps(state, indent=2))


def was_sent(state: dict, key: str) -> bool:
    return key in state


def mark_sent(state: dict, key: str) -> None:
    state[key] = datetime.now(timezone.utc).isoformat()
