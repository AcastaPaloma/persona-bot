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


def _scan_link_graph(vault_path: str) -> str:
    """Scan every .md file for [[wikilinks]] and build a link graph.

    Returns a readable map showing which files link to what, plus backlinks.
    Does NOT read file contents beyond extracting link patterns.

    Example output:
        03-People/Kuan.md
          → [[Guidelines]], [[Brain-Tumor-MRI-Platform]]
        05-Topics/BJJ/Jiu Jitsu.md
          → [[Takedowns]], [[Positional Escapes]]
          ← 05-Topics/Takedowns.md, 01-Daily/Daily/2026-02-18.md
    """
    root = Path(vault_path)
    wikilink_re = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")

    # Forward links: file → [targets]
    forward: Dict[str, List[str]] = {}
    # Backlinks: target → [source files]
    backlinks: Dict[str, List[str]] = {}

    for md_file in root.rglob("*.md"):
        rel = str(md_file.relative_to(root)).replace("\\", "/")

        # Skip hidden dirs
        if any(part.startswith(".") for part in md_file.parts):
            continue

        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        links = wikilink_re.findall(text)
        if not links:
            continue

        # Deduplicate while preserving order
        seen = set()
        unique_links = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)

        forward[rel] = unique_links

        for link in unique_links:
            backlinks.setdefault(link, []).append(rel)

    # Build readable output
    lines = []
    all_files = sorted(set(list(forward.keys()) + [k for k in backlinks.keys() if "/" in k]))

    for f in forward:
        lines.append(f"{f}")
        lines.append(f"  → {', '.join('[[' + l + ']]' for l in forward[f])}")
        # Check if this file has backlinks (by its stem name)
        stem = Path(f).stem
        if stem in backlinks:
            sources = [s for s in backlinks[stem] if s != f]
            if sources:
                lines.append(f"  ← {', '.join(sources)}")

    return "\n".join(lines)


DISTILL_SYSTEM_PROMPT = """\
You are Kuan's knowledge librarian. You organize his second brain — an \
Obsidian vault that serves as a RAG-style knowledge base about his life, \
thoughts, projects, and interests.

Every night, you receive the day's raw capture (stream of consciousness) \
and the full vault structure (file/folder names). Your job is to \
repartition today's information into the vault: sorting it into the right \
places, connecting it to what already exists, and growing the brain \
organically.

## What you receive
1. Today's raw capture (unstructured notes, ideas, rants, updates)
2. The current vault tree (all folder and file names — NO file contents)
3. The link graph (which files link to which via [[wikilinks]], including backlinks)

## What you produce
A JSON object with actions to execute.

## Your decision framework

### 1. ALWAYS: Create the daily note
Create `01-Daily/Daily/{date}.md` — a clean, high-signal summary of the \
day. This note is the HUB: it should contain [[wikilinks]] to every \
relevant existing or newly-created note. Think of it as today's index card.

### 2. First instinct: Update existing notes
Before creating anything, check the vault tree. If today's capture mentions \
a project, person, topic, or concept that already has a note — APPEND new \
information to that existing note. This is your default action. Examples:
- Mentioned BJJ training → append to existing 05-Topics/BJJ/ notes
- Progress on Brain Tumor MRI → append to 04-Projects/Brain-Tumor-MRI-Platform/
- Something about Grandmother → append to 03-People/Grandmother.md

### 3. When warranted: Create new notes in the RIGHT folder
If something genuinely new comes up that has no home in the vault, create \
a note for it — but place it in the CORRECT folder:
- New person → `03-People/PersonName.md`
- New project → `04-Projects/ProjectName/README.md` (create folder + file)
- New topic → `05-Topics/TopicName.md` or `05-Topics/Category/TopicName.md`
- School-related → `06-School/`
DO NOT create notes at the vault root. Every note belongs in a subfolder.

### 4. Only when needed: Create new folders
Only create a new folder when the new concept is big enough to warrant \
multiple notes underneath it (e.g. a new major project). A single note \
does not justify a new folder.

## Vault structure (FIXED root — do not add root folders)
```
01-Daily/        ← daily captures + Daily/ summaries
03-People/       ← personality profiles
04-Projects/     ← project folders with notes inside
05-Topics/       ← topic notes, optionally grouped in subfolders
06-School/       ← academic notes
99-Archive/      ← retired material
```

## Wikilink philosophy
- [[wikilinks]] are the BACKBONE of the second brain
- Link from the daily note to every relevant existing note
- Link from newly created/updated notes back to related concepts
- Include abstract/emotional connections, not just concrete ones
- ONLY link to notes that exist in the vault tree or that you are creating
- Do NOT hallucinate links to nonexistent files

## Rules
- Return ONLY valid JSON, no markdown fences, no explanation
- Keep daily note concise but rich with [[wikilinks]]
- When appending, add a dated section header (## {date}) so updates are traceable
- Never delete or rewrite existing content
- If today's capture is trivial (just greetings, tests), return minimal output

Return this exact JSON:
{{
  "daily_note": {{
    "content": "<full markdown for 01-Daily/Daily/{date}.md>"
  }},
  "new_files": [
    {{"path": "<relative path from vault root>", "content": "<full markdown>"}}
  ],
  "append_files": [
    {{"path": "<relative path to existing file>", "content": "<content to append>"}}
  ],
  "profile_update": "<text to append to Kuan.md, or null if nothing notable>",
  "reasoning": "<1-2 sentences: what you did, why, what you linked>"
}}
"""


async def _call_llm_for_distillation(capture_text: str, vault_tree: str, link_graph: str, date_str: str) -> dict:
    """Send capture + vault tree + link graph to LLM and get back structured actions."""
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

## Link Graph (which files connect to what)

```
{link_graph}
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

    # 2. Scan vault tree and link graph
    vault_tree = _scan_vault_tree(config.VAULT_PATH)
    link_graph = _scan_link_graph(config.VAULT_PATH)
    logger.info("Vault tree scanned: %d lines, link graph: %d lines",
                vault_tree.count("\n") + 1, link_graph.count("\n") + 1)

    # 3. Call LLM for distillation actions
    try:
        actions = await _call_llm_for_distillation(captures, vault_tree, link_graph, date_str)
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
