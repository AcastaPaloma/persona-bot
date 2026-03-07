"""Capture event ingestion — fast, no LLM, immediate push.

Each Discord message becomes one capture event:
1. Append raw text to daily capture file
2. Record event in SQLite
3. Git commit + push
"""

import logging
from datetime import datetime
from pathlib import Path

import pytz

from . import config
from .git_ops import commit, push
from .schemas import CaptureEvent
from .state import insert_capture

logger = logging.getLogger(__name__)


def _now() -> datetime:
    tz = pytz.timezone(config.TIMEZONE)
    return datetime.now(tz)


def _capture_path(dt: datetime) -> str:
    return f"01-Daily/Capture-{dt.strftime('%Y-%m-%d')}.md"


def _create_capture_frontmatter(dt: datetime) -> str:
    weekday = dt.strftime("%A")
    date_str = dt.strftime("%Y-%m-%d")
    return (
        "---\n"
        f"date: {date_str}\n"
        "type: daily-capture\n"
        "---\n\n"
        f"# Captures — {weekday}, {dt.strftime('%B %d, %Y')}\n\n"
    )


def _format_capture_entry(raw_text: str, dt: datetime) -> str:
    timestamp = dt.strftime("%H:%M")
    return f"## {timestamp}\n{raw_text}\n\n---\n\n"


def ingest_capture(
    message_id: str,
    raw_text: str,
    author: str,
) -> CaptureEvent:
    """Ingest a single capture event. No LLM. Appends to daily file and records in SQLite.

    Caller is responsible for acquiring the vault lock and calling git sync after.
    """
    dt = _now()
    vault_root = Path(config.VAULT_PATH)

    # Ensure daily directory
    daily_dir = vault_root / "01-Daily"
    daily_dir.mkdir(parents=True, exist_ok=True)

    # Build capture file path
    rel_path = _capture_path(dt)
    full_path = vault_root / rel_path

    # Create file with frontmatter if it doesn't exist
    if not full_path.exists():
        logger.info("Creating new daily capture: %s", rel_path)
        full_path.write_text(_create_capture_frontmatter(dt), encoding="utf-8")

    # Append the entry
    entry = _format_capture_entry(raw_text, dt)
    with open(full_path, "a", encoding="utf-8") as f:
        f.write(entry)

    # Record in SQLite
    event = CaptureEvent(
        id=message_id,
        timestamp=dt,
        author=author,
        raw_text=raw_text,
        status="pending",
    )
    insert_capture(event)

    logger.info("Captured event %s from %s at %s", message_id, author, dt.strftime("%H:%M"))
    return event


def sync_after_capture(raw_text: str = "") -> bool:
    """Git commit + push after a capture. Caller should pull beforehand.

    Returns True on full success.
    """
    preview = raw_text[:50].replace("\n", " ").strip() if raw_text else "message"
    committed = commit(config.VAULT_PATH, f"capture: {preview}")
    if committed:
        return push(config.VAULT_PATH)
    return True
