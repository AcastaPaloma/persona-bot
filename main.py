"""Persona Agent — entry point.

Sets up logging, loads config, and starts the Discord bot.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv


def _setup_logging() -> None:
    """Configure structured logging with console + rotating file output."""
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler — rotates at 5 MB, keeps 3 backups
    file_handler = RotatingFileHandler(
        log_dir / "agent.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[file_handler, console_handler],
    )

    # Quiet noisy libraries
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    load_dotenv()
    _setup_logging()

    logger = logging.getLogger("persona")
    logger.info("Starting Persona Agent...")

    # Load and validate config (exits on failure)
    from app import config
    config.load()

    # Import bot after config is loaded so config values are available
    from app.bot import run_bot
    run_bot()


if __name__ == "__main__":
    main()