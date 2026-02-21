"""Git operations — pull, commit, push with error handling.

Design principles:
  - Never discard local changes
  - Fail loudly in logs, never silently
  - Handle 'nothing to commit' gracefully
  - Retry push on transient network errors
"""

import logging
import subprocess
import asyncio
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_PUSH_RETRIES = 3
PUSH_RETRY_DELAY = 5  # seconds


def _run_git(vault_path: str, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    cmd = ["git"] + args
    logger.debug("git %s (in %s)", " ".join(args), vault_path)
    result = subprocess.run(
        cmd,
        cwd=vault_path,
        check=check,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        logger.debug("git stdout: %s", result.stdout.strip()[:500])
    if result.stderr.strip():
        logger.debug("git stderr: %s", result.stderr.strip()[:500])
    return result


def has_changes(vault_path: str) -> bool:
    """Check if there are uncommitted changes."""
    result = _run_git(vault_path, ["status", "--porcelain"], check=False)
    return bool(result.stdout.strip())


def pull(vault_path: str) -> None:
    """Pull latest changes with rebase."""
    try:
        _run_git(vault_path, ["pull", "--rebase"])
        logger.info("Git pull succeeded")
    except subprocess.CalledProcessError as e:
        logger.error("Git pull failed: %s", e.stderr[:500] if e.stderr else str(e))
        # Don't raise — we still want to write locally even if pull fails
        # The push will fail later and we'll retry next time
        logger.warning("Continuing with local state (will push on next sync)")


def commit(vault_path: str, message: str = "vault: automated capture") -> bool:
    """Stage all changes and commit. Returns True if a commit was made."""
    if not has_changes(vault_path):
        logger.info("No changes to commit")
        return False

    _run_git(vault_path, ["add", "-A"])
    _run_git(vault_path, ["commit", "-m", message])
    logger.info("Committed: %s", message)
    return True


def push(vault_path: str) -> bool:
    """Push with retry. Returns True on success."""
    for attempt in range(1, MAX_PUSH_RETRIES + 1):
        try:
            _run_git(vault_path, ["push"])
            logger.info("Git push succeeded (attempt %d)", attempt)
            return True
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Git push failed (attempt %d/%d): %s",
                attempt, MAX_PUSH_RETRIES,
                e.stderr[:300] if e.stderr else str(e),
            )
            if attempt < MAX_PUSH_RETRIES:
                import time
                time.sleep(PUSH_RETRY_DELAY)

    logger.error("All push retries exhausted — changes are committed locally")
    return False


def sync_vault(vault_path: str) -> bool:
    """Full sync cycle: pull → commit → push.

    Returns True if the sync completed fully, False if push failed
    (changes are still committed locally and will push next time).
    """
    pull(vault_path)
    committed = commit(vault_path)
    if committed:
        return push(vault_path)
    return True  # nothing to push is still a success