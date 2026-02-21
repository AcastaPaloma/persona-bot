"""Pydantic models for structured LLM output."""

from typing import List, Optional
from pydantic import BaseModel, Field


class Extraction(BaseModel):
    """Metadata extracted from a user message by the LLM."""

    mood: str = Field(default="neutral", description="Overall emotional tone")
    topics: List[str] = Field(default_factory=list, description="Key topics mentioned")
    projects: List[str] = Field(default_factory=list, description="Projects referenced")
    summary: str = Field(default="(no summary)", description="One-line summary")

    @classmethod
    def fallback(cls, reason: str = "LLM unavailable") -> "Extraction":
        """Return a safe default when extraction fails."""
        return cls(
            mood="unknown",
            topics=[],
            projects=[],
            summary=f"[auto-logged — {reason}]",
        )