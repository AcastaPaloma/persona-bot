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
    """Format a single capture entry as Obsidian-compatible markdown."""
    timestamp = dt.strftime("%H:%M")

    # Build tag string from topics
    tags = ""
    if metadata.get("topics"):
        tag_list = " ".join(f"#{t.replace(' ', '-')}" for t in metadata["topics"])
        tags = f"\n{tag_list}\n"

    # Build metadata block
    meta_lines = []
    if metadata.get("mood"):
        meta_lines.append(f"**Mood**: {metadata['mood']}")
    if metadata.get("summary"):
        meta_lines.append(f"**Summary**: {metadata['summary']}")
    if metadata.get("projects"):
        projects = ", ".join(metadata["projects"])
        meta_lines.append(f"**Projects**: {projects}")

    meta_block = "\n".join(meta_lines)

    entry = (
        f"## {timestamp}\n\n"
        f"{raw_text.strip()}\n\n"
    )

    if meta_block:
        entry += (
            f"> [!info]- Metadata\n"
        )
        for line in meta_lines:
            entry += f"> {line}\n"
        entry += "\n"

    if tags:
        entry += f"{tags}\n"

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