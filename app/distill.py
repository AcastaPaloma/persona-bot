"""Nightly distillation — autonomous knowledge organizer.

Reads today's captures, scans the full vault structure, and asks the LLM
to decide what notes/folders to create, what connections to make, and
whether to update the personality profile.

This replicates the OpenClaw nightly cron job:
  1. Read today's Capture file
  2. Scan vault tree (file/folder names only — no content)
  3. LLM decides: daily note, new topics/projects, profile updates, wikilinks
  4. Execute file actions (create, append)
  5. Git sync

Design principles:
  - Does NOT read file contents (except today's capture) — names only
  - Creates meaningful [[wikilinks]] — medium+ strength, not noise
  - Follows the vault structure defined in distill_clarification.md
  - Never deletes or rewrites existing files
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import pytz

from . import config
from .git_ops import sync_vault
from .vault import get_todays_capture, _now, _ensure_daily_dir

logger = logging.getLogger(__name__)

# Folders to skip when scanning the vault tree
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules"}


def _scan_vault_tree(vault_path: str) -> str:
    """Walk the vault and return a tree string of folder/file names.

    Does NOT read file contents. Only returns the structure.
    Example output:
        01-Daily/
          Capture-2026-02-21.md
          Daily/
            2026-02-18.md
        03-People/
          Kuan.md
        04-Projects/
          Brain-Tumor-MRI-Platform/
            README.md
        05-Topics/
          BJJ/
            Jiu Jitsu.md
    """
    lines = []
    root = Path(vault_path)

    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden/system directories
        dirnames[:] = [d for d in sorted(dirnames) if d not in SKIP_DIRS and not d.startswith(".")]

        depth = Path(dirpath).relative_to(root)
        indent = "  " * len(depth.parts)

        # Don't print root itself
        if dirpath != str(root):
            lines.append(f"{indent}{Path(dirpath).name}/")

        for f in sorted(filenames):
            if not f.startswith("."):
                lines.append(f"{indent}  {f}")

    return "\n".join(lines)


DISTILL_SYSTEM_PROMPT = """\
You are a personal knowledge assistant for Kuan. Your job is to distill \
today's raw capture into organized knowledge in an Obsidian vault.

You will receive:
1. Today's raw capture (stream of consciousness notes)
2. The current vault structure (file/folder names only)

Your tasks:
1. Create a clean daily summary note for 01-Daily/Daily/{date}.md
2. Decide if any new topic notes should be created in 05-Topics/
3. Decide if any project notes should be created/updated in 04-Projects/
4. Identify meaningful [[wikilinks]] — connections between concepts
5. STRONGLY EMPHASIZE ON CREATING WIKILINKS BETWEEN NOTES.
6. Note any personality observations for 03-People/Kuan.md

Rules:
- ONLY return valid JSON, no markdown, no explanation
- Use [[wikilinks]] for medium+ strength relations (including abstract/emotional)
- DO NOT HALLUCINATE WIKILINKS. ONLY CREATE WIKILINKS IF THE TOPIC/PROJECT ALREADY EXISTS IN THE VAULT TREE. CREATE LINKS TO NOTES/FOLDERS THAT ALREADY EXIST.
- Don't create noise — only create files/links that add real value
- Don't duplicate existing files — check the vault tree before creating
- Daily note should be concise, high-signal summary
- If nothing meaningful was captured today, return minimal output

Return this exact JSON structure:
{{
  "daily_note": {{
    "content": "<full markdown content for 01-Daily/Daily/{date}.md>"
  }},
  "new_files": [
    {{"path": "<relative path from vault root>", "content": "<full markdown>"}}
  ],
  "append_files": [
    {{"path": "<relative path from vault root>", "content": "<content to append>"}}
  ],
  "profile_update": "<text to append to Kuan.md signals section, or null if none>",
  "reasoning": "<1-2 sentences explaining what you did and why>"
}}
"""


async def _call_llm_for_distillation(capture_text: str, vault_tree: str, date_str: str) -> dict:
    """Send capture + vault tree to LLM and get back structured actions."""
    headers = {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Content-Type": "application/json",
    }

    user_prompt = f"""## Today's Raw Capture ({date_str})

{capture_text}

## Current Vault Structure

```
{vault_tree}
```

Now distill this into organized knowledge. Return JSON only."""

    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": DISTILL_SYSTEM_PROMPT.format(date=date_str)},
            {"role": "user", "content": user_prompt},
        ],
        "max_completion_tokens": 2000,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            config.LLM_ENDPOINT,
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"]
    logger.debug("Distillation LLM raw response: %s", content[:1000] if content else "(empty)")

    if not content or not content.strip():
        raise ValueError("LLM returned empty content for distillation")

    content = content.strip()

    # Strip <think>...</think> blocks from reasoning models
    if "<think>" in content:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    # Strip markdown code fences
    if content.startswith("```"):
        content = content.split("\n", 1)[1]
        content = content.rsplit("```", 1)[0]
        content = content.strip()

    return json.loads(content)


def _safe_write_file(vault_path: str, relative_path: str, content: str, mode: str = "w") -> bool:
    """Safely write/append to a file in the vault. Creates directories as needed.

    Returns True on success, False on error.
    """
    try:
        full_path = Path(vault_path) / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(full_path, mode, encoding="utf-8") as f:
            f.write(content)

        logger.info("Wrote %s: %s", "to" if mode == "w" else "appended to", relative_path)
        return True
    except Exception as e:
        logger.error("Failed to write %s: %s", relative_path, e)
        return False


def _execute_actions(vault_path: str, actions: dict, date_str: str) -> int:
    """Execute the LLM's file actions. Returns count of files written."""
    files_written = 0

    # 1. Write daily note
    daily_note = actions.get("daily_note", {})
    if daily_note and daily_note.get("content"):
        daily_dir = Path(vault_path) / "01-Daily" / "Daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
        path = f"01-Daily/Daily/{date_str}.md"
        if _safe_write_file(vault_path, path, daily_note["content"]):
            files_written += 1

    # 2. Create new files (topics, projects, etc.)
    for new_file in actions.get("new_files", []):
        fpath = new_file.get("path", "")
        fcontent = new_file.get("content", "")
        if fpath and fcontent:
            # Safety: don't overwrite existing files
            full = Path(vault_path) / fpath
            if full.exists():
                logger.warning("Skipping %s — file already exists", fpath)
                continue
            if _safe_write_file(vault_path, fpath, fcontent):
                files_written += 1

    # 3. Append to existing files
    for append_file in actions.get("append_files", []):
        fpath = append_file.get("path", "")
        fcontent = append_file.get("content", "")
        if fpath and fcontent:
            full = Path(vault_path) / fpath
            if not full.exists():
                logger.warning("Skipping append to %s — file doesn't exist", fpath)
                continue
            if _safe_write_file(vault_path, fpath, "\n" + fcontent, mode="a"):
                files_written += 1

    # 4. Update personality profile
    profile_update = actions.get("profile_update")
    if profile_update:
        kuan_path = Path(vault_path) / "03-People" / "Kuan.md"
        if kuan_path.exists():
            if _safe_write_file(vault_path, "03-People/Kuan.md",
                                f"\n\n## Signal — {date_str}\n{profile_update}\n", mode="a"):
                files_written += 1

    # Log reasoning
    reasoning = actions.get("reasoning", "")
    if reasoning:
        logger.info("Distillation reasoning: %s", reasoning)

    return files_written


async def run_distillation() -> bool:
    """Run the nightly distillation. Returns True on success."""
    dt = _now()
    date_str = dt.strftime("%Y-%m-%d")
    logger.info("Starting nightly distillation for %s...", date_str)

    # 1. Read today's capture
    captures = get_todays_capture()
    if not captures or captures.strip() == "":
        logger.info("No captures today — skipping distillation")
        return True

    # 2. Scan vault tree
    vault_tree = _scan_vault_tree(config.VAULT_PATH)
    logger.info("Vault tree scanned: %d lines", vault_tree.count("\n") + 1)

    # 3. Call LLM for distillation actions
    try:
        actions = await _call_llm_for_distillation(captures, vault_tree, date_str)
    except Exception as e:
        logger.error("Distillation LLM call failed: %s", e)
        return False

    # 4. Execute file actions
    files_written = _execute_actions(config.VAULT_PATH, actions, date_str)
    logger.info("Distillation wrote %d files", files_written)

    # 5. Git sync
    sync_vault(config.VAULT_PATH)
    logger.info("Nightly distillation complete for %s", date_str)
    return True


async def distillation_scheduler(bot) -> None:
    """Background task that runs distillation at the configured time daily.

    Call this as a task in the bot's setup_hook.
    """
    await bot.wait_until_ready()
    logger.info(
        "Distillation scheduler started — will run daily at %02d:%02d (%s)",
        config.DISTILL_HOUR, config.DISTILL_MINUTE, config.TIMEZONE,
    )

    while not bot.is_closed():
        now = _now()
        # Calculate seconds until next run
        target = now.replace(
            hour=config.DISTILL_HOUR,
            minute=config.DISTILL_MINUTE,
            second=0,
            microsecond=0,
        )
        if now >= target:
            # Already past today's target — schedule for tomorrow
            from datetime import timedelta
            target = target + timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.debug("Next distillation in %.0f seconds (%s)", wait_seconds, target)

        await asyncio.sleep(wait_seconds)

        try:
            await run_distillation()
        except Exception as e:
            logger.error("Distillation scheduler error: %s", e)

        # Sleep a short time to avoid running twice on the same second
        await asyncio.sleep(60)
