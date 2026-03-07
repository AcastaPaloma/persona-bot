"""Persona Bot — entry point.

Sets up logging, loads config, initializes state, and starts the Discord bot.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv


def _setup_logging() -> None:
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_dir / "agent.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[file_handler, console_handler],
    )

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def main() -> None:
    load_dotenv()
    _setup_logging()

    logger = logging.getLogger("persona")
    logger.info("Starting Persona Bot...")

    from app import config
    config.load()

    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.getLogger().setLevel(log_level)

    from app.cache import rebuild_all
    from app.state import get_pending_count

    logger.info("Checking cache state...")
    cache_dir = Path(config.STATE_DIR) / "cache"
    note_cards_path = cache_dir / "note_cards.json"

    if not note_cards_path.exists():
        logger.info("No cached note cards found — running initial cache build...")
        rebuild_all(use_llm=False)
        logger.info("Initial cache build complete (LLM enrichment deferred)")
    else:
        logger.info("Cache exists — loading from disk")

    pending = get_pending_count()
    logger.info("Pending captures: %d", pending)

    from app.bot import run_bot
    run_bot()


if __name__ == "__main__":
    main()
