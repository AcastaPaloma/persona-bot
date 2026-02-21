"""Nightly distillation — summarize today's captures.

Runs as a background asyncio task inside the bot. At the configured hour,
it reads today's capture file, sends it to the LLM for summarization,
and writes a summary file. Then git syncs.

Design principles:
  - Does NOT scan the entire vault
  - Does NOT rewrite historical days
  - Does NOT require approval
  - Skips silently if no captures today
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import httpx
import pytz

from . import config
from .vault import get_todays_capture, _now, _ensure_daily_dir
from .git_ops import sync_vault

logger = logging.getLogger(__name__)

DISTILL_PROMPT = """\
You are a personal knowledge assistant. Given the raw daily captures below, \
produce a concise daily summary in markdown format.

Structure:
## Daily Summary — {date}

### Key Themes
- bullet points of main themes

### Mood Trajectory
One sentence about how mood changed through the day.

### Action Items
- any tasks or follow-ups mentioned (or "None identified")

### Notable Quotes
> any particularly insightful or important statements (or skip if none)

Rules:
- Be concise but comprehensive
- Preserve the person's voice
- Do NOT invent information not in the captures
- Keep under 300 words
"""


async def _generate_summary(captures_text: str) -> str:
    """Send today's captures to the LLM for summarization."""
    dt = _now()
    prompt = DISTILL_PROMPT.format(date=dt.strftime("%A, %B %d, %Y"))

    headers = {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": captures_text},
        ],
        "temperature": 0.4,
        "max_tokens": 800,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            config.LLM_ENDPOINT,
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    return data["choices"][0]["message"]["content"]


async def run_distillation() -> bool:
    """Run the nightly distillation. Returns True on success."""
    logger.info("Starting nightly distillation...")

    captures = get_todays_capture()
    if not captures or captures.strip() == "":
        logger.info("No captures today — skipping distillation")
        return True

    try:
        summary = await _generate_summary(captures)
    except Exception as e:
        logger.error("Distillation LLM call failed: %s", e)
        return False

    # Write summary file
    dt = _now()
    summary_path = _ensure_daily_dir() / f"Summary-{dt.strftime('%Y-%m-%d')}.md"

    frontmatter = (
        "---\n"
        f"date: {dt.strftime('%Y-%m-%d')}\n"
        "type: daily-summary\n"
        "tags:\n"
        "  - summary\n"
        "  - persona-agent\n"
        "---\n\n"
    )

    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(frontmatter)
        f.write(summary)
        f.write("\n")

    logger.info("Wrote summary to %s", summary_path.name)

    # Sync to git
    sync_vault(config.VAULT_PATH)
    logger.info("Nightly distillation complete")
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
            target = target.replace(day=target.day + 1)

        wait_seconds = (target - now).total_seconds()
        logger.debug("Next distillation in %.0f seconds (%s)", wait_seconds, target)

        await asyncio.sleep(wait_seconds)

        try:
            await run_distillation()
        except Exception as e:
            logger.error("Distillation scheduler error: %s", e)

        # Sleep a short time to avoid running twice on the same second
        await asyncio.sleep(60)
