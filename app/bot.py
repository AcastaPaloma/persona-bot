"""Discord bot — DM capture, slash commands, autocomplete.

Interface:
  - DM: raw capture (no LLM, fast)
  - /log: capture from guild channels
  - /distill: manually trigger distillation
  - /delete: delete a vault note with confirmation
  - /status: health check
"""

import asyncio
import logging
import traceback
from datetime import datetime

import discord
from discord import app_commands

from . import config
from .cache import get_note_cards
from .capture import ingest_capture, sync_after_capture
from .distill import distillation_scheduler, run_distillation
from .git_ops import pull, vault_lock
from .schemas import Tombstone
from .state import (
    get_last_distill_time,
    get_pending_count,
    insert_tombstone,
    remove_created_note,
)
from .vault import delete_note

logger = logging.getLogger(__name__)


class PersonaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Slash commands synced")
        self.loop.create_task(distillation_scheduler(self))

    async def on_ready(self):
        logger.info("Bot online as %s (ID: %s)", self.user.name, self.user.id)
        try:
            pull(config.VAULT_PATH)
            logger.info("Initial vault pull complete")
        except Exception as e:
            logger.warning("Initial vault pull failed: %s", e)

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return
        if not message.content or not message.content.strip():
            return

        logger.info("DM from %s: %s", message.author.name, message.content[:100])
        await self._handle_capture(message.channel, message.content, message.author.name, str(message.id))

    async def _handle_capture(
        self,
        channel: discord.abc.Messageable,
        text: str,
        author: str,
        message_id: str,
    ) -> None:
        try:
            async with vault_lock():
                pull(config.VAULT_PATH)
                ingest_capture(message_id, text, author)
                sync_after_capture(text)

            await channel.send("\u2705")

        except Exception as e:
            logger.error("Capture error: %s\n%s", e, traceback.format_exc())
            try:
                await channel.send(
                    f"\u274c Error capturing: {type(e).__name__}\n"
                    "Your message is safe — will retry on next sync."
                )
            except Exception:
                logger.error("Failed to send error message to user")


bot = PersonaBot()


# ── /log ──────────────────────────────────────────────────────────────────────

@bot.tree.command(name="log", description="Log a thought to your vault")
@app_commands.describe(text="Your thought, note, or brain dump")
async def log_command(interaction: discord.Interaction, text: str):
    await interaction.response.defer(thinking=True)
    try:
        async with vault_lock():
            pull(config.VAULT_PATH)
            ingest_capture(str(interaction.id), text, interaction.user.name)
            sync_after_capture(text)

        await interaction.followup.send("\u2705 Captured.")
    except Exception as e:
        logger.error("/log error: %s", e)
        await interaction.followup.send(f"\u274c Error: {type(e).__name__}")


# ── /distill ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="distill", description="Manually trigger distillation of pending captures")
async def distill_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    pending = get_pending_count()
    if pending == 0:
        await interaction.followup.send("No pending captures to distill.")
        return

    await interaction.followup.send(
        f"Starting distillation of {pending} pending captures... This may take a minute."
    )

    try:
        result = await run_distillation()

        summary = (
            f"**Distillation complete**\n"
            f"Notes created: {result.notes_created}\n"
            f"Notes appended: {result.notes_appended}\n"
            f"Notes linked: {result.notes_linked}\n"
            f"Cross-links added: {result.cross_links_added}"
        )
        if result.errors:
            summary += f"\nWarnings: {len(result.errors)}"

        await interaction.channel.send(summary)

    except Exception as e:
        logger.error("/distill error: %s", e, exc_info=True)
        await interaction.channel.send(f"\u274c Distillation failed: {type(e).__name__}")


# ── /delete ───────────────────────────────────────────────────────────────────

@bot.tree.command(name="delete", description="Delete a vault note")
@app_commands.describe(note="The note to delete (title or path)")
async def delete_command(interaction: discord.Interaction, note: str):
    await interaction.response.defer(thinking=True)

    cards = get_note_cards()
    match = None
    for card in cards:
        if (
            card.title.lower() == note.lower()
            or card.current_path.lower() == note.lower()
            or card.current_path.rsplit("/", 1)[-1].replace(".md", "").lower()
            == note.lower()
        ):
            match = card
            break

    if not match:
        await interaction.followup.send(f"Note not found: `{note}`")
        return

    confirm_msg = (
        f"**Delete this note?**\n"
        f"Title: **{match.title}**\n"
        f"Path: `{match.current_path}`\n"
        f"Summary: {match.summary[:200] if match.summary else '(no summary)'}\n"
        f"Outbound links: {len(match.outbound_links)}\n"
        f"Backlinks: {len(match.backlinks)}\n\n"
        f"React with \u2705 to confirm deletion."
    )

    msg = await interaction.followup.send(confirm_msg, wait=True)
    await msg.add_reaction("\u2705")
    await msg.add_reaction("\u274c")

    def check(reaction, user):
        return (
            user == interaction.user
            and str(reaction.emoji) in ["\u2705", "\u274c"]
            and reaction.message.id == msg.id
        )

    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=60.0, check=check)

        if str(reaction.emoji) == "\u2705":
            async with vault_lock():
                pull(config.VAULT_PATH)
                success = delete_note(match.current_path)
                if success:
                    from .git_ops import commit, push

                    commit(config.VAULT_PATH, f"delete: {match.title}")
                    push(config.VAULT_PATH)

                    tombstone = Tombstone(
                        note_id=match.note_id,
                        original_path=match.current_path,
                        title=match.title,
                        deleted_at=datetime.now().astimezone(),
                    )
                    insert_tombstone(tombstone)
                    remove_created_note(match.note_id)

            await interaction.channel.send(f"\U0001f5d1\ufe0f Deleted: `{match.current_path}`")
        else:
            await interaction.channel.send("Deletion cancelled.")

    except asyncio.TimeoutError:
        await interaction.channel.send("Deletion timed out — cancelled.")


@delete_command.autocomplete("note")
async def delete_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    cards = get_note_cards()
    choices = []
    current_lower = current.lower()

    for card in cards:
        if current_lower in card.title.lower() or current_lower in card.current_path.lower():
            display = f"{card.title} ({card.current_path})"
            if len(display) > 100:
                display = display[:97] + "..."
            choices.append(
                app_commands.Choice(name=display, value=card.current_path)
            )
            if len(choices) >= 25:
                break

    return choices


# ── /status ───────────────────────────────────────────────────────────────────

@bot.tree.command(name="status", description="Check bot health")
async def status_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    checks = []

    from pathlib import Path

    vault = Path(config.VAULT_PATH)
    if vault.is_dir():
        checks.append("\U0001f4c1 Vault: accessible")
    else:
        checks.append("\U0001f4c1 Vault: NOT FOUND")

    pending = get_pending_count()
    checks.append(f"\U0001f4e5 Pending captures: {pending}")

    last_distill = get_last_distill_time()
    if last_distill:
        checks.append(f"\u23f0 Last distill: {last_distill.strftime('%Y-%m-%d %H:%M')}")
    else:
        checks.append("\u23f0 Last distill: never")

    card_count = len(get_note_cards())
    checks.append(f"\U0001f4cb Note cards cached: {card_count}")

    from .cache import get_folder_archetypes

    arch_count = len(get_folder_archetypes())
    checks.append(f"\U0001f4c2 Folder archetypes: {arch_count}")

    state_dir = Path(config.STATE_DIR)
    db_path = state_dir / "state.db"
    if db_path.exists():
        size_kb = db_path.stat().st_size / 1024
        checks.append(f"\U0001f4be State DB: {size_kb:.0f} KB")
    else:
        checks.append("\U0001f4be State DB: not created yet")

    await interaction.followup.send(
        "**Persona Bot Status**\n\n" + "\n".join(checks)
    )


# ── Entry Point ───────────────────────────────────────────────────────────────

def run_bot() -> None:
    logger.info("Starting Discord bot...")
    bot.run(config.DISCORD_TOKEN, log_handler=None)
