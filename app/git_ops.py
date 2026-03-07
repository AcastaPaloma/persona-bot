"""Git operations — pull, commit, push with global operation lock.

Safety guarantees:
  - Global asyncio lock prevents concurrent vault mutations
  - Never force push or discard local changes
  - Fail loudly in logs, never silently
"""

import asyncio
import logging
import subprocess

logger = logging.getLogger(__name__)

MAX_PUSH_RETRIES = 3
PUSH_RETRY_DELAY = 5

_vault_lock = asyncio.Lock()


def vault_lock() -> asyncio.Lock:
    return _vault_lock


def _run_git(
    vault_path: str, args: list[str], check: bool = True
) -> subprocess.CompletedProcess:
    cmd = ["git"] + args
    logger.debug("git %s (in %s)", " ".join(args), vault_path)
    result = subprocess.run(
        cmd, cwd=vault_path, check=check, capture_output=True, text=True
    )
    if result.stdout.strip():
        logger.debug("git stdout: %s", result.stdout.strip()[:500])
    if result.stderr.strip():
        logger.debug("git stderr: %s", result.stderr.strip()[:500])
    return result


def has_changes(vault_path: str) -> bool:
    result = _run_git(vault_path, ["status", "--porcelain"], check=False)
    return bool(result.stdout.strip())


def pull(vault_path: str) -> bool:
    try:
        _run_git(vault_path, ["pull", "--rebase"])
        logger.info("Git pull succeeded")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            "Git pull failed: %s", e.stderr[:500] if e.stderr else str(e)
        )
        logger.warning("Continuing with local state (will push on next sync)")
        return False


def commit(vault_path: str, message: str = "vault: automated update") -> bool:
    if not has_changes(vault_path):
        logger.info("No changes to commit")
        return False
    try:
        _run_git(vault_path, ["add", "-A"])
        _run_git(vault_path, ["commit", "-m", message])
        logger.info("Committed: %s", message)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            "Git commit failed: %s", e.stderr[:500] if e.stderr else str(e)
        )
        return False


def push(vault_path: str) -> bool:
    import time

    for attempt in range(1, MAX_PUSH_RETRIES + 1):
        try:
            _run_git(vault_path, ["push"])
            logger.info("Git push succeeded (attempt %d)", attempt)
            return True
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Git push failed (attempt %d/%d): %s",
                attempt,
                MAX_PUSH_RETRIES,
                e.stderr[:300] if e.stderr else str(e),
            )
            if attempt < MAX_PUSH_RETRIES:
                time.sleep(PUSH_RETRY_DELAY)

    logger.error("All push retries exhausted — changes committed locally")
    return False


def sync_vault(vault_path: str, message: str = "vault: automated update") -> bool:
    """Full sync: pull -> commit -> push. Returns True on full success."""
    pull(vault_path)
    committed = commit(vault_path, message)
    if committed:
        return push(vault_path)
    return True
