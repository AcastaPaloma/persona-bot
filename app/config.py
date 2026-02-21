"""Centralized configuration — loads and validates all settings on import."""

import os
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _require_env(key: str) -> str:
    """Return an env var or exit with a clear error."""
    value = os.getenv(key)
    if not value:
        logger.critical("Missing required environment variable: %s", key)
        sys.exit(1)
    return value


# ── Required ──────────────────────────────────────────────────────────────────
DISCORD_TOKEN: str = ""
GITHUB_TOKEN: str = ""
VAULT_PATH: str = ""

# ── Optional (with defaults) ─────────────────────────────────────────────────
TIMEZONE: str = "America/Toronto"
LLM_MODEL: str = "openai/gpt-4.1-mini"
LLM_ENDPOINT: str = "https://models.github.ai/inference/chat/completions"
DISTILL_HOUR: int = 23  # 24-h clock, when nightly distillation runs
DISTILL_MINUTE: int = 59


def load() -> None:
    """Call once at startup after dotenv is loaded."""
    global DISCORD_TOKEN, GITHUB_TOKEN, VAULT_PATH, TIMEZONE
    global LLM_MODEL, LLM_ENDPOINT, DISTILL_HOUR, DISTILL_MINUTE

    DISCORD_TOKEN = _require_env("DISCORD_TOKEN")
    GITHUB_TOKEN = _require_env("GITHUB_TOKEN")
    VAULT_PATH = _require_env("VAULT_PATH")

    TIMEZONE = os.getenv("TIMEZONE", TIMEZONE)
    LLM_MODEL = os.getenv("LLM_MODEL", LLM_MODEL)
    LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", LLM_ENDPOINT)
    DISTILL_HOUR = int(os.getenv("DISTILL_HOUR", str(DISTILL_HOUR)))
    DISTILL_MINUTE = int(os.getenv("DISTILL_MINUTE", str(DISTILL_MINUTE)))

    # Validate vault path exists
    vault = Path(VAULT_PATH)
    if not vault.is_dir():
        logger.critical("VAULT_PATH does not exist: %s", VAULT_PATH)
        sys.exit(1)
    if not (vault / ".git").is_dir():
        logger.critical("VAULT_PATH is not a git repo: %s", VAULT_PATH)
        sys.exit(1)

    logger.info("Config loaded — vault: %s, model: %s", VAULT_PATH, LLM_MODEL)
