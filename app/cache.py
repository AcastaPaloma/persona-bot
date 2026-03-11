"""Note card + folder archetype generation and caching.

Derived state — can be regenerated from the vault at any time.
Stored in STATE_DIR/cache/ as JSON files.
"""

import json
import logging
import re
from collections import Counter
from pathlib import Path

import anthropic

from . import config
from .schemas import FolderArchetype, NoteCard
from .vault import (
    build_link_graph,
    compute_backlinks,
    scan_all_notes,
    scan_folders,
)

logger = logging.getLogger(__name__)

_note_cards: list[NoteCard] = []
_folder_archetypes: list[FolderArchetype] = []


def _cache_dir() -> Path:
    return Path(config.STATE_DIR) / "cache"


def _note_cards_path() -> Path:
    return _cache_dir() / "note_cards.json"


def _folder_archetypes_path() -> Path:
    return _cache_dir() / "folder_archetypes.json"


def _graph_path() -> Path:
    return _cache_dir() / "graph.json"


# ── Note Cards ────────────────────────────────────────────────────────────────

def get_note_cards() -> list[NoteCard]:
    global _note_cards
    if not _note_cards:
        _note_cards = _load_note_cards()
    return _note_cards


def _load_note_cards() -> list[NoteCard]:
    path = _note_cards_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [NoteCard.model_validate(d) for d in data]
        except Exception as e:
            logger.warning("Failed to load note cards cache: %s", e)
    return []


def save_note_cards(cards: list[NoteCard]) -> None:
    global _note_cards
    _note_cards = cards
    path = _note_cards_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [c.model_dump(mode="json") for c in cards]
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.info("Saved %d note cards to cache", len(cards))


def rebuild_note_cards(use_llm: bool = True) -> list[NoteCard]:
    """Full rebuild of note cards from vault scan. Optionally uses LLM for enrichment."""
    notes = scan_all_notes()
    graph = build_link_graph(notes)
    backlinks = compute_backlinks(graph)

    save_graph(graph)

    cards: list[NoteCard] = []
    for note in notes:
        fingerprint = NoteCard.compute_fingerprint(note["title"], note["content"])

        snippets = _extract_snippets(note["content"])

        card = NoteCard(
            note_id=fingerprint,
            current_path=note["path"],
            title=note["title"],
            aliases=note.get("aliases", []),
            summary="",
            qualities=[],
            concepts=[],
            entities=[],
            outbound_links=note["links"],
            backlinks=backlinks.get(note["stem"], []),
            representative_snippets=snippets,
            fingerprint=fingerprint,
        )
        cards.append(card)

    if use_llm and cards:
        cards = _enrich_cards_with_llm(cards)

    save_note_cards(cards)
    return cards


def _extract_snippets(content: str, max_snippets: int = 5) -> list[str]:
    """Extract representative sentences from note content."""
    lines = [
        line.strip()
        for line in content.split("\n")
        if line.strip()
        and not line.startswith("#")
        and not line.startswith("---")
        and not line.startswith("- [[")
        and len(line.strip()) > 20
    ]
    return lines[:max_snippets]


def _enrich_cards_with_llm(cards: list[NoteCard]) -> list[NoteCard]:
    """Use Anthropic to generate summary, qualities, concepts, entities for each card."""
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    batch_size = 10
    for i in range(0, len(cards), batch_size):
        batch = cards[i : i + batch_size]
        notes_text = ""
        for idx, card in enumerate(batch):
            snippets = "\n".join(card.representative_snippets[:3])
            notes_text += f"""
--- Note {idx} ---
Path: {card.current_path}
Title: {card.title}
Links: {', '.join(card.outbound_links[:10])}
Snippets:
{snippets}
"""

        try:
            response = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=2000,
                messages=[
                    {
                        "role": "user",
                        "content": f"""For each note below, generate a JSON array where each element has:
- "summary": 1-2 sentence description
- "qualities": list of 3-5 latent traits/qualities (e.g. "precision", "reflection", "strategy")
- "concepts": list of 3-7 key topics/keywords
- "entities": list of people, places, or projects mentioned

Return ONLY a valid JSON array with {len(batch)} elements, one per note. No markdown fences.

{notes_text}""",
                    }
                ],
            )

            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            enrichments = json.loads(raw)
            for idx, enrichment in enumerate(enrichments):
                if idx < len(batch):
                    batch[idx].summary = enrichment.get("summary", "")
                    batch[idx].qualities = enrichment.get("qualities", [])
                    batch[idx].concepts = enrichment.get("concepts", [])
                    batch[idx].entities = enrichment.get("entities", [])

        except Exception as e:
            logger.error("LLM enrichment failed for batch %d: %s", i, e)

    return cards


# ── Folder Archetypes ─────────────────────────────────────────────────────────

def get_folder_archetypes() -> list[FolderArchetype]:
    global _folder_archetypes
    if not _folder_archetypes:
        _folder_archetypes = _load_folder_archetypes()
    return _folder_archetypes


def _load_folder_archetypes() -> list[FolderArchetype]:
    path = _folder_archetypes_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [FolderArchetype.model_validate(d) for d in data]
        except Exception as e:
            logger.warning("Failed to load folder archetypes cache: %s", e)
    return []


def save_folder_archetypes(archetypes: list[FolderArchetype]) -> None:
    global _folder_archetypes
    _folder_archetypes = archetypes
    path = _folder_archetypes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [a.model_dump(mode="json") for a in archetypes]
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.info("Saved %d folder archetypes to cache", len(archetypes))


def rebuild_folder_archetypes() -> list[FolderArchetype]:
    """Full rebuild of folder archetypes from vault scan. Purely deterministic."""
    folders = scan_folders()
    archetypes: list[FolderArchetype] = []

    for folder in folders:
        children = folder["children"]
        filenames = folder["child_filenames"]

        naming = _infer_naming_convention(filenames)
        role = _infer_semantic_role(folder["path"], children)
        terms = _extract_common_terms(children)

        note_kinds: list[str] = []
        if "People" in folder["root_category"]:
            note_kinds = ["person"]
        elif "Projects" in folder["root_category"]:
            note_kinds = ["project"]
        elif "School" in folder["root_category"]:
            note_kinds = ["school"]
        elif "Topics" in folder["root_category"]:
            if any(
                kw in folder["path"].lower()
                for kw in ["technique", "drill", "move"]
            ):
                note_kinds = ["technique"]
            else:
                note_kinds = ["topic"]
        else:
            note_kinds = ["topic"]

        confidence = min(1.0, len(children) / 5.0)

        archetypes.append(
            FolderArchetype(
                path=folder["path"],
                root_category=folder["root_category"],
                semantic_role=role,
                child_note_kinds=note_kinds,
                naming_convention=naming,
                common_terms=terms[:10],
                example_children=children[:5],
                confidence=round(confidence, 2),
            )
        )

    save_folder_archetypes(archetypes)
    return archetypes


def _infer_naming_convention(filenames: list[str]) -> str:
    """Infer naming convention from sibling filenames."""
    if not filenames:
        return "lowercase_snake_case"

    stems = [Path(f).stem for f in filenames]
    snake = sum(1 for s in stems if "_" in s and s == s.lower())
    kebab = sum(1 for s in stems if "-" in s and s == s.lower())
    title = sum(1 for s in stems if s[0:1].isupper() and " " not in s and "-" in s)
    spaced = sum(1 for s in stems if " " in s)

    counts = {
        "lowercase_snake_case": snake,
        "lowercase_kebab_case": kebab,
        "Title-Case": title,
        "Title Case": spaced,
    }

    best = max(counts, key=counts.get)  # type: ignore[arg-type]
    if counts[best] == 0:
        return "lowercase_snake_case"
    return best


def _infer_semantic_role(path: str, children: list[str]) -> str:
    """Simple heuristic to describe folder purpose."""
    parts = path.lower().split("/")
    if "technique" in parts or "techniques" in parts:
        return "technique collection"
    if "people" in parts:
        return "people profiles"
    if "project" in parts or "projects" in parts:
        return "project documentation"
    if "school" in parts:
        return "academic notes"
    if "daily" in parts:
        return "daily notes"
    if len(children) > 0:
        return f"notes about {parts[-1]}"
    return "general notes"


def _extract_common_terms(stems: list[str]) -> list[str]:
    """Extract common terms from note stems."""
    words: list[str] = []
    for stem in stems:
        parts = re.split(r"[_\-\s]+", stem.lower())
        words.extend(parts)
    counter = Counter(words)
    return [w for w, _ in counter.most_common(10) if len(w) > 2]


# ── Graph ─────────────────────────────────────────────────────────────────────

def save_graph(graph: dict[str, list[str]]) -> None:
    path = _graph_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2), encoding="utf-8")


def load_graph() -> dict[str, list[str]]:
    path = _graph_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ── Full Rebuild ──────────────────────────────────────────────────────────────

def rebuild_all(use_llm: bool = True) -> None:
    """Full rebuild of all derived state from the vault."""
    logger.info("Starting full cache rebuild...")
    rebuild_note_cards(use_llm=use_llm)
    rebuild_folder_archetypes()
    logger.info("Full cache rebuild complete")
