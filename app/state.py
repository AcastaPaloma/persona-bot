"""SQLite state management — capture events, tombstones, bot-created notes.

All machine state lives outside the vault in STATE_DIR/state.db.
"""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config
from .schemas import CaptureEvent, Tombstone

logger = logging.getLogger(__name__)

_connection: Optional[sqlite3.Connection] = None


def _db_path() -> Path:
    return Path(config.STATE_DIR) / "state.db"


def _get_conn() -> sqlite3.Connection:
    global _connection
    if _connection is None:
        path = _db_path()
        _connection = sqlite3.connect(str(path), check_same_thread=False)
        _connection.row_factory = sqlite3.Row
        _connection.execute("PRAGMA journal_mode=WAL")
        _connection.execute("PRAGMA foreign_keys=ON")
        _init_tables(_connection)
        logger.info("SQLite state DB opened: %s", path)
    return _connection


@contextmanager
def transaction():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS capture_events (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            author TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            distilled_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_capture_status
            ON capture_events(status);

        CREATE TABLE IF NOT EXISTS tombstones (
            note_id TEXT PRIMARY KEY,
            original_path TEXT NOT NULL,
            title TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT 'user_delete',
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bot_created_notes (
            note_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            distill_run TEXT
        );
    """)

    # SQLite ALTER TABLE for existing DBs to add new columns from recent updates
    try:
        conn.execute("ALTER TABLE capture_events ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        logger.info("Migrated capture_events table to include 'attempts' column")
    except sqlite3.OperationalError:
        pass # Column already exists


# ── Capture Events ────────────────────────────────────────────────────────────

def insert_capture(event: CaptureEvent) -> None:
    with transaction() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO capture_events
               (id, timestamp, author, raw_text, status, attempts, distilled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.timestamp.isoformat(),
                event.author,
                event.raw_text,
                event.status,
                event.attempts,
                event.distilled_at.isoformat() if event.distilled_at else None,
            ),
        )


def get_pending_captures() -> list[CaptureEvent]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM capture_events WHERE status = 'pending' ORDER BY timestamp"
    ).fetchall()

    return [
        CaptureEvent(
            id=r["id"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
            author=r["author"],
            raw_text=r["raw_text"],
            status=r["status"],
            attempts=r["attempts"],
            distilled_at=(
                datetime.fromisoformat(r["distilled_at"])
                if r["distilled_at"]
                else None
            ),
        )
        for r in rows
    ]


def mark_events_distilled(event_ids: list[str]) -> None:
    if not event_ids:
        return
    now = datetime.now().astimezone().isoformat()
    with transaction() as conn:
        conn.executemany(
            "UPDATE capture_events SET status = 'distilled', distilled_at = ? WHERE id = ?",
            [(now, eid) for eid in event_ids],
        )
    logger.info("Marked %d capture events as distilled", len(event_ids))


def increment_capture_attempts(event_ids: list[str]) -> list[str]:
    """Increment attempts for events. If attempts >= 3 and recovery enabled, mark as failed.

    Returns:
        list[str]: IDs of events that were just marked as 'failed' (Dead Letter Queue).
    """
    if not event_ids:
        return []

    failed_ids = []
    with transaction() as conn:
        # Increment all
        conn.executemany(
            "UPDATE capture_events SET attempts = attempts + 1 WHERE id = ?",
            [(eid,) for eid in event_ids]
        )

        if config.ENABLE_ERROR_RECOVERY:
            # Find which ones hit the limit
            rows = conn.execute(
                f"SELECT id FROM capture_events WHERE attempts >= 3 AND status = 'pending' AND id IN ({','.join(['?']*len(event_ids))})",
                event_ids
            ).fetchall()
            failed_ids = [r["id"] for r in rows]

            # Mark them as failed
            if failed_ids:
                conn.executemany(
                    "UPDATE capture_events SET status = 'failed' WHERE id = ?",
                    [(fid,) for fid in failed_ids]
                )
                logger.warning("Marked %d captures as failed (Dead-Letter Queue)", len(failed_ids))

    return failed_ids


def get_pending_count() -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM capture_events WHERE status = 'pending'"
    ).fetchone()
    return row["cnt"]


def get_last_distill_time() -> Optional[datetime]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT MAX(distilled_at) as last_dt FROM capture_events WHERE status = 'distilled'"
    ).fetchone()
    if row and row["last_dt"]:
        return datetime.fromisoformat(row["last_dt"])
    return None


# ── Tombstones ────────────────────────────────────────────────────────────────

def insert_tombstone(tombstone: Tombstone) -> None:
    with transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO tombstones
               (note_id, original_path, title, deleted_at, reason, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                tombstone.note_id,
                tombstone.original_path,
                tombstone.title,
                tombstone.deleted_at.isoformat(),
                tombstone.reason,
                tombstone.expires_at.isoformat(),
            ),
        )


def is_tombstoned(basename: str) -> bool:
    """Check if a basename matches any active (non-expired) tombstone."""
    conn = _get_conn()
    now = datetime.now().astimezone().isoformat()
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM tombstones
           WHERE original_path LIKE ? AND expires_at > ?""",
        (f"%/{basename}" if "/" not in basename else f"%{basename}", now),
    ).fetchone()
    return row["cnt"] > 0


def cleanup_expired_tombstones() -> int:
    now = datetime.now().astimezone().isoformat()
    with transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM tombstones WHERE expires_at <= ?", (now,)
        )
        return cursor.rowcount


# ── Bot-Created Notes ─────────────────────────────────────────────────────────

def record_created_note(
    note_id: str, path: str, title: str, distill_run: Optional[str] = None
) -> None:
    now = datetime.now().astimezone().isoformat()
    with transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO bot_created_notes
               (note_id, path, title, created_at, distill_run)
               VALUES (?, ?, ?, ?, ?)""",
            (note_id, path, title, now, distill_run),
        )


def get_recent_created_notes(limit: int = 50) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM bot_created_notes ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def remove_created_note(note_id: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM bot_created_notes WHERE note_id = ?", (note_id,))


def close() -> None:
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
        logger.info("SQLite state DB closed")
