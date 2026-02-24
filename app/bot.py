"""Discord bot — DM-native, seamless info dumping.

Primary interaction: DM the bot anything → it auto-logs to the vault.
Secondary: /log command for guild channels.
Utility: /status for health check, /verbatim for lossless capture.
"""

import logging
import re
import traceback

import discord
from discord import app_commands

from . import config
from .llm import extract_metadata, format_verbatim
from .vault import append_capture, append_verbatim
from .git_ops import pull, commit, push
from .distill import distillation_scheduler

logger = logging.getLogger(__name__)

# ── Verbatim trigger detection ────────────────────────────────────────────────

# Patterns that indicate the user wants verbatim mode in a DM.
# Matched against the start of the message (case-insensitive).
_VERBATIM_TRIGGERS = re.compile(
    r"^(?:/verbatim|!verbatim|verbatim[:\s,]|verbatim$)",
    re.IGNORECASE,
)


def _strip_verbatim_prefix(text: str) -> str:
    """Remove the verbatim trigger prefix from the message text."""
    return _VERBATIM_TRIGGERS.sub("", text).strip()


class PersonaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # Required for DM reading
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Slash commands synced")
        # Start nightly distillation scheduler
        self.loop.create_task(distillation_scheduler(self))

    async def on_ready(self):
        logger.info("Bot online as %s (ID: %s)", self.user.name, self.user.id)
        # Pre-pull vault on startup to ensure we have latest
        try:
            pull(config.VAULT_PATH)
            logger.info("Initial vault pull complete")
        except Exception as e:
            logger.warning("Initial vault pull failed (will retry on first message): %s", e)

    async def on_message(self, message: discord.Message):
        """Handle DMs — any text sent to the bot gets auto-logged."""
        # Ignore our own messages
        if message.author == self.user:
            return

        # Only auto-log DMs (not guild messages)
        if not isinstance(message.channel, discord.DMChannel):
            return

        # Ignore empty messages (e.g. image-only)
        if not message.content or not message.content.strip():
            return

        logger.info("DM from %s: %s", message.author.name, message.content[:100])

        # Check for verbatim trigger
        if _VERBATIM_TRIGGERS.search(message.content):
            body = _strip_verbatim_prefix(message.content)
            if body:
                await self._process_and_log_verbatim(message.channel, body)
            else:
                await message.channel.send("📋 Send `/verbatim` with text, or type `verbatim: <your text>`")
            return

        await self._process_and_log(message.channel, message.content, message.author.name)

    async def _process_and_log(
        self,
        channel: discord.abc.Messageable,
        text: str,
        author: str,
    ) -> None:
        """Core pipeline: extract → append → sync → reply."""
        try:
            # Show typing indicator while processing
            async with channel.typing():
                # 1. Pull latest vault state first
                pull(config.VAULT_PATH)

                # 2. Extract metadata via LLM
                extraction = await extract_metadata(text)

                # 3. Append to vault
                capture_path = append_capture(text, extraction.model_dump())

                # 4. Commit and push (no pull needed, we already pulled)
                commit(config.VAULT_PATH)
                sync_success = push(config.VAULT_PATH)

            # 4. Reply with confirmation
            status = "✅" if sync_success else "⚠️ (saved locally, sync pending)"
            await channel.send(
                f"{status} **Logged** — {extraction.mood}\n"
                f"📝 {extraction.summary}\n"
                f"🏷️ {', '.join(extraction.topics) if extraction.topics else 'no topics'}"
            )

        except Exception as e:
            logger.error("Pipeline error for '%s': %s\n%s", text[:80], e, traceback.format_exc())
            try:
                await channel.send(
                    f"❌ Error logging entry: {type(e).__name__}\n"
                    f"Your message is safe — I'll retry on next sync."
                )
            except Exception:
                logger.error("Failed to send error message to user")

    async def _process_and_log_verbatim(
        self,
        channel: discord.abc.Messageable,
        text: str,
    ) -> None:
        """Verbatim pipeline: format (lossless) → append → sync → reply."""
        try:
            async with channel.typing():
                pull(config.VAULT_PATH)

                # LLM formats/organizes but does NOT summarize
                formatted = await format_verbatim(text)

                capture_path = append_verbatim(formatted)

                commit(config.VAULT_PATH)
                sync_success = push(config.VAULT_PATH)

            status = "✅" if sync_success else "⚠️ (saved locally, sync pending)"
            await channel.send(
                f"{status} 📋 **Verbatim logged** — {len(text)} chars captured"
            )

        except Exception as e:
            logger.error("Verbatim pipeline error: %s\n%s", e, traceback.format_exc())
            try:
                await channel.send(
                    f"❌ Error logging verbatim entry: {type(e).__name__}\n"
                    f"Your message is safe — I'll retry on next sync."
                )
            except Exception:
                logger.error("Failed to send error message to user")


bot = PersonaBot()


# ── Slash Commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="log", description="Log a thought to your vault")
@app_commands.describe(text="Your thought, note, or brain dump")
async def log_command(interaction: discord.Interaction, text: str):
    """Slash command alternative for guild channels."""
    await interaction.response.defer(thinking=True)

    try:
        pull(config.VAULT_PATH)
        extraction = await extract_metadata(text)
        capture_path = append_capture(text, extraction.model_dump())
        commit(config.VAULT_PATH)
        sync_success = push(config.VAULT_PATH)

        status = "✅" if sync_success else "⚠️ (saved locally, sync pending)"
        await interaction.followup.send(
            f"{status} **Logged** — {extraction.mood}\n"
            f"📝 {extraction.summary}\n"
            f"🏷️ {', '.join(extraction.topics) if extraction.topics else 'no topics'}"
        )
    except Exception as e:
        logger.error("Slash command error: %s", e)
        await interaction.followup.send(f"❌ Error: {type(e).__name__} — {e}")


@bot.tree.command(name="verbatim", description="Log information verbatim — lossless, no summarization")
@app_commands.describe(text="Raw info dump — will be formatted but never summarized")
async def verbatim_command(interaction: discord.Interaction, text: str):
    """Slash command for lossless verbatim capture."""
    await interaction.response.defer(thinking=True)

    try:
        pull(config.VAULT_PATH)
        formatted = await format_verbatim(text)
        capture_path = append_verbatim(formatted)
        commit(config.VAULT_PATH)
        sync_success = push(config.VAULT_PATH)

        status = "✅" if sync_success else "⚠️ (saved locally, sync pending)"
        await interaction.followup.send(
            f"{status} 📋 **Verbatim logged** — {len(text)} chars captured"
        )
    except Exception as e:
        logger.error("Verbatim slash command error: %s", e)
        await interaction.followup.send(f"❌ Error: {type(e).__name__} — {e}")


@bot.tree.command(name="status", description="Check bot health")
async def status_command(interaction: discord.Interaction):
    """Health check — shows vault path, git status, LLM reachability."""
    await interaction.response.defer(thinking=True)

    checks = []

    # Vault check
    from pathlib import Path
    vault = Path(config.VAULT_PATH)
    if vault.is_dir():
        checks.append("📁 Vault: ✅ accessible")
    else:
        checks.append("📁 Vault: ❌ not found")

    # Git check
    from .git_ops import _run_git
    try:
        result = _run_git(config.VAULT_PATH, ["status", "--short"], check=True)
        changes = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
        checks.append(f"🔄 Git: ✅ ({changes} pending changes)")
    except Exception as e:
        checks.append(f"🔄 Git: ❌ {e}")

    # LLM check
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                config.LLM_ENDPOINT,
                json={
                    "model": config.LLM_MODEL,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                },
                headers={
                    "Authorization": f"Bearer {config.GITHUB_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code == 200:
                checks.append("🤖 LLM: ✅ reachable")
            else:
                checks.append(f"🤖 LLM: ⚠️ status {resp.status_code}")
    except Exception as e:
        checks.append(f"🤖 LLM: ❌ {type(e).__name__}")

    await interaction.followup.send(
        "**Persona Agent Status**\n\n" + "\n".join(checks)
    )


def run_bot() -> None:
    """Start the bot (blocking)."""
    logger.info("Starting Discord bot...")
    bot.run(config.DISCORD_TOKEN, log_handler=None)  # We handle logging ourselves