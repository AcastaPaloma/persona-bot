"""Nightly distillation pipeline — the core intelligence of the system.

Phases:
  0. Setup (lock, pull, load pending)
  1. Atomize captures into knowledge items
  2. Retrieve candidate notes
  3. Plan actions (create / append / link / daily_only)
  4. Resolve same-batch references
  5. Validate plans
  6. Write notes atomically
  7. Cross-link old notes
  8. Generate daily summary
  9. Commit, push, mark distilled
"""

import asyncio
import logging
from datetime import datetime

import pytz

from . import config
from .cache import rebuild_folder_archetypes, rebuild_note_cards
from .git_ops import commit, pull, push, vault_lock
from .planner import (
    atomize_captures,
    plan_actions,
    resolve_batch_references,
    validate_plans,
)
from .schemas import DistillationResult
from .state import (
    get_pending_captures,
    mark_events_distilled,
    record_created_note,
)
from .templates import daily_summary
from .vault import write_note_atomic
from .writer import execute_append, execute_create, execute_cross_links

logger = logging.getLogger(__name__)


def _now() -> datetime:
    tz = pytz.timezone(config.TIMEZONE)
    return datetime.now(tz)


async def run_distillation() -> DistillationResult:
    """Run the full distillation pipeline. Returns a result summary."""
    result = DistillationResult()
    dt = _now()
    date_str = dt.strftime("%Y-%m-%d")

    logger.info("Starting distillation for %s...", date_str)

    async with vault_lock():
        try:
            # Phase 0: Setup
            pull(config.VAULT_PATH)

            pending = get_pending_captures()
            if not pending:
                logger.info("No pending captures — skipping distillation")
                return result

            # Extra safety check
            if config.ENABLE_SMART_DISTILL_CRON and len(pending) == 0:
                logger.debug("Smart CRON: Skipping because of 0 pending.")
                return result

            logger.info("Found %d pending capture events", len(pending))

            # Refresh caches (deterministic, no LLM)
            rebuild_folder_archetypes()

            # Only rebuild note cards without LLM for speed; full LLM rebuild
            # can be triggered separately or happens on first run
            rebuild_note_cards(use_llm=False)

            # Phase 1: Atomize
            capture_texts = [e.raw_text for e in pending]
            atoms = atomize_captures(capture_texts)

            if not atoms:
                logger.warning("No atoms extracted — generating daily summary only")
                _write_daily_summary(
                    date_str, dt, ["No structured content extracted."], capture_texts, []
                )
                _finalize(pending, result, date_str)
                return result

            # Assign source event IDs to atoms for traceability
            event_ids = [e.id for e in pending]
            for atom in atoms:
                atom.source_event_ids = event_ids

            # Phase 2+3: Retrieve candidates + Plan actions
            plans, cross_links, daily_highlights, daily_related = plan_actions(atoms)

            # Phase 4: Resolve same-batch references
            plans = resolve_batch_references(plans)

            # Phase 5: Validate
            valid_plans, validation_errors = validate_plans(plans)
            result.errors.extend(validation_errors)

            # Phase 6: Write notes
            for plan in valid_plans:
                if plan.action == "create":
                    success = execute_create(plan)
                    if success:
                        result.notes_created += 1
                        record_created_note(
                            note_id=plan.planned_id,
                            path=plan.target_path,
                            title=plan.title,
                            distill_run=date_str,
                        )
                    else:
                        result.errors.append(f"Failed to create: {plan.title}")

                elif plan.action == "append":
                    success = execute_append(plan, date_str)
                    if success:
                        result.notes_appended += 1
                    else:
                        result.errors.append(f"Failed to append to: {plan.target_path}")

                elif plan.action == "link":
                    from .vault import add_related_link

                    for link_title in plan.related_notes:
                        add_related_link(plan.target_path, link_title)
                    result.notes_linked += 1

            # Phase 7: Cross-link old notes
            if cross_links:
                added = execute_cross_links(cross_links)
                result.cross_links_added = added

            # Phase 8: Daily summary
            all_touched_titles = daily_related.copy()
            for plan in valid_plans:
                if plan.title not in all_touched_titles:
                    all_touched_titles.append(plan.title)

            summary_text = "\n".join(
                f"- {e.raw_text[:100]}..." if len(e.raw_text) > 100 else f"- {e.raw_text}"
                for e in pending
            )
            summary_path = _write_daily_summary(
                date_str,
                dt,
                daily_highlights or ["Captures were processed and organized."],
                [summary_text],
                all_touched_titles,
            )
            result.daily_summary_path = summary_path

            # Phase 9: Commit, push, mark distilled
            _finalize(pending, result, date_str)

        except Exception as e:
            logger.error("Distillation failed: %s", e, exc_info=True)
            result.errors.append(f"Pipeline error: {e}")

            # Dead-Letter Queue Handling
            from .state import increment_capture_attempts
            if pending:
                failed_ids = increment_capture_attempts([p.id for p in pending])
                if failed_ids and hasattr(config, "BOT_REF") and config.BOT_REF:
                    # Async task to DM the user
                    asyncio.create_task(config.BOT_REF._dm_user(
                        f"🚨 **Distillation Failed permanently** for {len(failed_ids)} capture events. "
                        f"They have been moved to the Dead-Letter Queue to prevent infinite loops."
                    ))

    return result


def _write_daily_summary(
    date_str: str,
    dt: datetime,
    highlights: list[str],
    captures_summaries: list[str],
    related_notes: list[str],
) -> str:
    path = f"01-Daily/Daily/{date_str}.md"
    from pathlib import Path as P

    (P(config.VAULT_PATH) / "01-Daily" / "Daily").mkdir(parents=True, exist_ok=True)

    # Check if daily summary already exists (e.g., /distill run twice in one day)
    from .vault import read_note, append_to_note

    existing = read_note(path)
    if existing is not None:
        # Don't overwrite — append new highlights and captures
        append_content = f"\n\n## Updated {dt.strftime('%H:%M')}\n"
        append_content += "\n".join(f"- {h}" for h in highlights) + "\n"
        if captures_summaries:
            append_content += "\n### Additional Captures\n"
            append_content += "\n".join(captures_summaries) + "\n"
        append_to_note(path, append_content)
        logger.info("Appended to existing daily summary: %s", path)
        return path

    weekday = dt.strftime("%A, %B %d, %Y")
    captures_text = "\n".join(captures_summaries) if captures_summaries else "(no captures)"

    content = daily_summary(
        date_str=date_str,
        weekday=weekday,
        highlights=highlights,
        captures_summary=captures_text,
        related_notes=related_notes,
        created=dt,
    )

    write_note_atomic(path, content)
    logger.info("Wrote daily summary: %s", path)
    return path


def _finalize(pending, result: DistillationResult, date_str: str) -> None:
    """Commit, push, and mark events as distilled."""
    msg = (
        f"distill: {date_str} "
        f"({result.notes_created} created, "
        f"{result.notes_appended} appended, "
        f"{result.notes_linked} linked)"
    )
    committed = commit(config.VAULT_PATH, msg)

    push_success = True
    if committed:
        push_success = push(config.VAULT_PATH)

    # CRITICAL: Only mark distilled after successful push
    if push_success:
        event_ids = [e.id for e in pending]
        mark_events_distilled(event_ids)
        logger.info("Distillation complete: %s", msg)
    else:
        logger.error(
            "Push failed — events NOT marked as distilled, will retry next run"
        )
        result.errors.append("Push failed — events will be retried")


# ── Scheduler ─────────────────────────────────────────────────────────────────

async def distillation_scheduler(bot) -> None:
    """Background task that runs distillation at the configured time daily."""
    await bot.wait_until_ready()
    logger.info(
        "Distillation scheduler started — will run daily at %02d:%02d (%s)",
        config.DISTILL_HOUR,
        config.DISTILL_MINUTE,
        config.TIMEZONE,
    )

    while not bot.is_closed():
        now = _now()
        target = now.replace(
            hour=config.DISTILL_HOUR,
            minute=config.DISTILL_MINUTE,
            second=0,
            microsecond=0,
        )
        if now >= target:
            from datetime import timedelta

            target = target + timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        logger.debug("Next distillation in %.0f seconds (%s)", wait_seconds, target)

        await asyncio.sleep(wait_seconds)

        try:
            from .state import get_pending_count

            # Smart Distill Cron check
            if config.ENABLE_SMART_DISTILL_CRON and get_pending_count() == 0:
                logger.info("Smart Cron: 0 pending captures, skipping tonight's distillation.")
            else:
                result = await run_distillation()
                if result.errors:
                    logger.warning(
                        "Distillation completed with %d errors", len(result.errors)
                    )
        except Exception as e:
            logger.error("Distillation scheduler error: %s", e, exc_info=True)

        await asyncio.sleep(60)
