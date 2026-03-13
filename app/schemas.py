"""Pydantic models for every data structure in the system."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CaptureEvent(BaseModel):
    """One Discord message = one capture event. Atomic unit of idempotency."""

    id: str = Field(description="Stable unique ID (Discord message ID)")
    timestamp: datetime = Field(description="When the message was sent (tz-aware)")
    author: str = Field(description="Discord username")
    raw_text: str = Field(description="Full message content")
    status: Literal["pending", "distilled", "failed"] = "pending"
    attempts: int = Field(default=0, description="Number of times distillation was attempted")
    distilled_at: Optional[datetime] = None


class NoteCard(BaseModel):
    """Compressed representation of a vault note for LLM context."""

    note_id: str = Field(description="Stable internal ID (survives moves)")
    current_path: str = Field(description="Vault-relative path")
    title: str = Field(description="Human-readable title")
    aliases: list[str] = Field(default_factory=list)
    summary: str = Field(default="", description="1-3 sentence description")
    qualities: list[str] = Field(
        default_factory=list,
        description="Latent traits (e.g. precision, delicacy, craftsmanship)",
    )
    concepts: list[str] = Field(default_factory=list, description="Key topics")
    entities: list[str] = Field(
        default_factory=list, description="People, places, projects"
    )
    outbound_links: list[str] = Field(default_factory=list)
    backlinks: list[str] = Field(default_factory=list)
    representative_snippets: list[str] = Field(
        default_factory=list, description="2-5 short excerpts"
    )
    fingerprint: str = Field(default="", description="Content hash for identity")
    created: Optional[datetime] = None
    updated: Optional[datetime] = None

    @staticmethod
    def compute_fingerprint(title: str, content: str) -> str:
        raw = f"{title.strip().lower()}|{content.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class FolderArchetype(BaseModel):
    """Compressed representation of a folder's conventions."""

    path: str = Field(description="Vault-relative folder path")
    root_category: str = Field(description="Root folder (e.g. 05-Topics)")
    semantic_role: str = Field(default="", description="What kind of notes live here")
    child_note_kinds: list[str] = Field(default_factory=list)
    naming_convention: str = Field(default="lowercase_snake_case")
    common_terms: list[str] = Field(default_factory=list)
    example_children: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class Atom(BaseModel):
    """Single atomic knowledge item extracted from captures."""

    atom_type: Literal[
        "entity",
        "concept",
        "fact",
        "technique",
        "project_update",
        "task",
        "reflection",
    ]
    content: str = Field(description="Summary of the knowledge item")
    keywords: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(
        default_factory=list, description="Capture event IDs this came from"
    )


class NotePlan(BaseModel):
    """Internal planning structure for a single note action during distillation."""

    planned_id: str = Field(description="Temp ID for same-batch cross-referencing")
    action: Literal["create", "append", "link"]
    target_path: str = Field(description="Vault-relative path (final after resolution)")
    note_type: str = Field(description="Template category")
    title: str
    content: str = Field(default="")
    related_notes: list[str] = Field(
        default_factory=list, description="Note titles for ## Related"
    )
    resolved: bool = False


class CrossLink(BaseModel):
    """Instruction to add a link between two existing notes."""

    source_path: str
    target_title: str


class DistillationResult(BaseModel):
    """Output summary of a distillation run."""

    notes_created: int = 0
    notes_appended: int = 0
    notes_linked: int = 0
    cross_links_added: int = 0
    daily_summary_path: Optional[str] = None
    errors: list[str] = Field(default_factory=list)


class Tombstone(BaseModel):
    """Record of a deleted note to prevent re-creation."""

    note_id: str
    original_path: str
    title: str
    deleted_at: datetime
    reason: str = "user_delete"
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now().astimezone() + timedelta(days=30)
    )

    def is_expired(self) -> bool:
        return datetime.now().astimezone() >= self.expires_at
