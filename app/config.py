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

# ── Feature Toggles ───────────────────────────────────────────────────────────
ENABLE_WEEKLY_REORG: bool = True
ENABLE_ERROR_RECOVERY: bool = True
ENABLE_AUDIO_CAPTURE: bool = True
ENABLE_PDF_CAPTURE: bool = True
ENABLE_SMART_DISTILL_CRON: bool = True
ENABLE_MORNING_BRIEFING: bool = True
ENABLE_DISCORD_SEARCH: bool = True

LANGEXTRACT_MODEL: str = "gemini-3.1-flash-lite-preview"
GOOGLE_API_KEY: str = ""  # If using gemini for PDFs


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

    # Feature Toggles
    global set_bool
    def set_bool(env_key: str, default: bool) -> bool:
        val = os.getenv(env_key, str(default)).lower()
        return val in ("true", "1", "yes", "on")

    global ENABLE_WEEKLY_REORG, ENABLE_ERROR_RECOVERY, ENABLE_AUDIO_CAPTURE
    global ENABLE_PDF_CAPTURE, ENABLE_SMART_DISTILL_CRON, ENABLE_MORNING_BRIEFING, ENABLE_DISCORD_SEARCH

    ENABLE_WEEKLY_REORG = set_bool("ENABLE_WEEKLY_REORG", ENABLE_WEEKLY_REORG)
    ENABLE_ERROR_RECOVERY = set_bool("ENABLE_ERROR_RECOVERY", ENABLE_ERROR_RECOVERY)
    ENABLE_AUDIO_CAPTURE = set_bool("ENABLE_AUDIO_CAPTURE", ENABLE_AUDIO_CAPTURE)
    ENABLE_PDF_CAPTURE = set_bool("ENABLE_PDF_CAPTURE", ENABLE_PDF_CAPTURE)
    ENABLE_SMART_DISTILL_CRON = set_bool("ENABLE_SMART_DISTILL_CRON", ENABLE_SMART_DISTILL_CRON)
    ENABLE_MORNING_BRIEFING = set_bool("ENABLE_MORNING_BRIEFING", ENABLE_MORNING_BRIEFING)
    ENABLE_DISCORD_SEARCH = set_bool("ENABLE_DISCORD_SEARCH", ENABLE_DISCORD_SEARCH)

    global LANGEXTRACT_MODEL, GOOGLE_API_KEY
    LANGEXTRACT_MODEL = os.getenv("LANGEXTRACT_MODEL", LANGEXTRACT_MODEL)
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", GOOGLE_API_KEY)

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
