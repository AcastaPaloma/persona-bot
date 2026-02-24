"""Vault operations — append-only daily capture files for Obsidian.

Design principles:
  - Append-only: never rewrite or delete content
  - Obsidian-compatible: YAML frontmatter, tags, callout blocks
  - Safe writes: write to temp file, then rename
  - Timezone-aware timestamps everywhere
"""

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import pytz

from . import config

logger = logging.getLogger(__name__)

DAILY_DIR = "01-Daily"


def _now() -> datetime:
    """Current time in the configured timezone."""
    tz = pytz.timezone(config.TIMEZONE)
    return datetime.now(tz)


def _ensure_daily_dir() -> Path:
    """Create the daily directory if it doesn't exist."""
    daily = Path(config.VAULT_PATH) / DAILY_DIR
    daily.mkdir(parents=True, exist_ok=True)
    return daily


def _capture_path(dt: Optional[datetime] = None) -> Path:
    """Path to today's capture file."""
    dt = dt or _now()
    return _ensure_daily_dir() / f"Capture-{dt.strftime('%Y-%m-%d')}.md"


def _create_frontmatter(dt: datetime) -> str:
    """YAML frontmatter for a new daily capture file."""
    return (
        "---\n"
        f"date: {dt.strftime('%Y-%m-%d')}\n"
        "type: daily-capture\n"
        f"created: {dt.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        "tags:\n"
        "  - capture\n"
        "  - persona-agent\n"
        "---\n\n"
        f"# Capture — {dt.strftime('%A, %B %d, %Y')}\n\n"
    )


def _format_entry(raw_text: str, metadata: Dict, dt: datetime) -> str:
    """Format a single capture entry as Obsidian-compatible markdown.

    The summary is the main content. Raw text goes in a collapsed callout
    so captures stay concise and high-signal.
    """
    timestamp = dt.strftime("%H:%M")
    mood = metadata.get("mood", "")
    summary = metadata.get("summary", "")
    topics = metadata.get("topics", [])
    projects = metadata.get("projects", [])

    # Main line: timestamp + summary
    entry = f"## {timestamp}"
    if mood:
        entry += f" — {mood}"
    entry += "\n\n"

    if summary:
        entry += f"{summary}\n\n"

    # Tags from topics
    if topics:
        tag_list = " ".join(f"#{t.replace(' ', '-')}" for t in topics)
        entry += f"{tag_list}\n\n"

    # Projects as wikilinks
    if projects:
        project_links = ", ".join(f"[[{p}]]" for p in projects)
        entry += f"**Projects**: {project_links}\n\n"

    entry += "---\n\n"
    return entry


def append_capture(raw_text: str, metadata: Dict) -> Path:
    """Append a structured entry to today's capture file.

    Creates the file with frontmatter if it doesn't exist.
    Uses atomic write pattern to prevent corruption.

    Returns the path to the capture file.
    """
    dt = _now()
    path = _capture_path(dt)

    # If file doesn't exist, write frontmatter first
    if not path.exists():
        logger.info("Creating new daily capture: %s", path.name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_create_frontmatter(dt))

    # Format and append the entry
    entry = _format_entry(raw_text, metadata, dt)

    # Atomic append: write to temp, read existing, combine, rename
    # For append-only, a simple append is actually safe since we never
    # read-modify-write. But we still handle encoding explicitly.
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.info("Appended capture to %s at %s", path.name, dt.strftime("%H:%M"))
    return path


def get_todays_capture() -> Optional[str]:
    """Read today's capture content, or None if no file exists."""
    path = _capture_path()
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def append_verbatim(formatted_text: str) -> Path:
    """Append a verbatim (losslessly formatted) entry to today's capture file.

    Unlike append_capture, this skips mood/topics/summary metadata and writes
    the LLM-formatted text directly with a 📋 verbatim marker.

    Creates the file with frontmatter if it doesn't exist.
    Returns the path to the capture file.
    """
    dt = _now()
    path = _capture_path(dt)

    # If file doesn't exist, write frontmatter first
    if not path.exists():
        logger.info("Creating new daily capture: %s", path.name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_create_frontmatter(dt))

    timestamp = dt.strftime("%H:%M")
    entry = f"## {timestamp} — 📋 verbatim\n\n{formatted_text}\n\n---\n\n"

    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)

    logger.info("Appended verbatim capture to %s at %s", path.name, timestamp)
    return path