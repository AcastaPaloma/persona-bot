"""Centralized configuration — loads and validates all settings on import."""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Fixed root folders in the vault taxonomy (immutable)
ROOT_FOLDERS = frozenset([
    "01-Daily",
    "03-People",
    "04-Projects",
    "05-Topics",
    "06-School",
    "_Templates",
])

# Folders to skip when scanning the vault
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "_Templates"}


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        logger.critical("Missing required environment variable: %s", key)
        sys.exit(1)
    return value


# ── Required ──────────────────────────────────────────────────────────────────
DISCORD_TOKEN: str = ""
ANTHROPIC_API_KEY: str = ""
VAULT_PATH: str = ""

# ── Optional ──────────────────────────────────────────────────────────────────
STATE_DIR: str = ""
TIMEZONE: str = "America/Toronto"
DISTILL_HOUR: int = 23
DISTILL_MINUTE: int = 59
ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
LOG_LEVEL: str = "INFO"


def _resolve_state_dir() -> str:
    explicit = os.getenv("STATE_DIR")
    if explicit:
        return str(Path(explicit).expanduser().resolve())

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
        return str(Path(base) / "persona-bot")

    return str(Path.home() / ".local" / "share" / "persona-bot")


def load() -> None:
    """Call once at startup after dotenv is loaded."""
    global DISCORD_TOKEN, ANTHROPIC_API_KEY, VAULT_PATH
    global STATE_DIR, TIMEZONE, DISTILL_HOUR, DISTILL_MINUTE
    global ANTHROPIC_MODEL, LOG_LEVEL

    DISCORD_TOKEN = _require_env("DISCORD_TOKEN")
    ANTHROPIC_API_KEY = _require_env("ANTHROPIC_API_KEY")
    VAULT_PATH = _require_env("VAULT_PATH")

    STATE_DIR = _resolve_state_dir()
    TIMEZONE = os.getenv("TIMEZONE", TIMEZONE)
    DISTILL_HOUR = int(os.getenv("DISTILL_HOUR", str(DISTILL_HOUR)))
    DISTILL_MINUTE = int(os.getenv("DISTILL_MINUTE", str(DISTILL_MINUTE)))
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL)
    LOG_LEVEL = os.getenv("LOG_LEVEL", LOG_LEVEL)

    vault = Path(VAULT_PATH)
    if not vault.is_dir():
        logger.critical("VAULT_PATH does not exist: %s", VAULT_PATH)
        sys.exit(1)
    if not (vault / ".git").is_dir():
        logger.critical("VAULT_PATH is not a git repo: %s", VAULT_PATH)
        sys.exit(1)

    state = Path(STATE_DIR)
    state.mkdir(parents=True, exist_ok=True)
    (state / "cache").mkdir(exist_ok=True)
    (state / "tmp").mkdir(exist_ok=True)

    logger.info(
        "Config loaded — vault: %s, state: %s, model: %s",
        VAULT_PATH,
        STATE_DIR,
        ANTHROPIC_MODEL,
    )
