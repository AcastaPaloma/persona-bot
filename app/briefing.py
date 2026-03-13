import logging
import pytz
from datetime import datetime, timedelta
import asyncio
from anthropic import AsyncAnthropic

from . import config
from .cache import get_note_cards

logger = logging.getLogger(__name__)

async def generate_morning_briefing() -> str:
    """Generate the Morning Briefing (Learnings, Improvements, Tips) using Anthropic."""
    if not config.ENABLE_MORNING_BRIEFING:
        return ""

    cards = get_note_cards()
    if not cards:
        return "Your vault is empty! Start logging thoughts to get morning briefings."

    # Get notes from the last 7 days
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.now(tz)
    week_ago = now - timedelta(days=7)

    recent_notes = []
    for c in cards:
        # Assuming we can parse `created` or `updated` from fingerprint or we just take a random sample
        # For a robust implementation, assume the bot cached them. We'll pick a slice
        recent_notes.append(f"- {c.title}: {c.summary}")
        if len(recent_notes) >= 30: # Limit to 30 to save tokens
            break

    if not recent_notes:
        return "Good morning! You haven't captured any new notes recently. Log some thoughts today!"

    context = "\n".join(recent_notes)

    prompt = f"""You are my personal AI assistant, pulling insights from my recent notes.
Here is a summary of some notes I've touched or created recently:
{context}

Please write a short, highly motivating Morning Briefing for me.
Structure it cleanly with 3 sections:
1. **Recent Learnings**: What did I learn recently? (1 sentence)
2. **Opportunities for Improvement**: How can I improve? (1 bullet)
3. **Tips for the Week**: 1 actionable tip based on my notes.

Keep it extremely concise, warm, and zero fluff. No intro/outro pleasantries, just jump into the markdown."""

    try:
        client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=300,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Failed to generate morning briefing: {e}")
        return "Good morning! (Failed to generate personalized briefing today due to an API error)."

async def morning_briefing_scheduler(bot):
    """Background task to send the morning briefing at 6AM daily."""
    await bot.wait_until_ready()
    logger.info("Morning briefing scheduler started — will run daily at 06:00")

    while not bot.is_closed():
        tz = pytz.timezone(config.TIMEZONE)
        now = datetime.now(tz)

        target = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= target:
            target = target + timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)

        if config.ENABLE_MORNING_BRIEFING:
            try:
                # Find the user to DM based on whoever sent the last capture, or just notify the owner
                # Since this is a personal bot, we DM the owner. We'll implement a `_dm_user` helper in bot.py
                briefing = await generate_morning_briefing()
                if briefing:
                    await bot._dm_user("🌅 **Smart Daily Briefing**\n\n" + briefing)
            except Exception as e:
                logger.error(f"Morning briefing error: {e}", exc_info=True)

        await asyncio.sleep(60) # Prevent double firing
