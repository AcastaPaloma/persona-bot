"""Candidate note retrieval — fuzzy, keyword, graph neighborhood.

Multi-strategy retrieval to find the best existing notes for an atom.
"""

import logging
from typing import Optional

from rapidfuzz import fuzz

from .cache import get_folder_archetypes, get_note_cards, load_graph
from .schemas import Atom, FolderArchetype, NoteCard

logger = logging.getLogger(__name__)

FUZZY_THRESHOLD = 80
NEAR_DUPLICATE_THRESHOLD = 90


def retrieve_candidates(
    atom: Atom,
    top_k: int = 10,
) -> list[NoteCard]:
    """Find candidate notes for an atom using multi-strategy retrieval."""
    cards = get_note_cards()
    if not cards:
        return []

    scored: dict[str, tuple[float, NoteCard]] = {}

    for card in cards:
        score = _score_card(atom, card)
        if score > 0:
            prev = scored.get(card.note_id)
            if prev is None or score > prev[0]:
                scored[card.note_id] = (score, card)

    # Graph expansion: for high-scoring matches, also include their neighbors
    graph = load_graph()
    neighbors_to_add: list[str] = []
    for note_id, (score, card) in list(scored.items()):
        if score >= 50:
            stem = card.current_path.rsplit("/", 1)[-1].replace(".md", "")
            for neighbor_stem in graph.get(stem, []):
                neighbors_to_add.append(neighbor_stem)
            for neighbor_stem in _get_backlink_stems(card, cards):
                neighbors_to_add.append(neighbor_stem)

    for stem in neighbors_to_add:
        for card in cards:
            card_stem = card.current_path.rsplit("/", 1)[-1].replace(".md", "")
            if card_stem == stem and card.note_id not in scored:
                scored[card.note_id] = (10.0, card)

    ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
    return [card for _, card in ranked[:top_k]]


def _score_card(atom: Atom, card: NoteCard) -> float:
    """Score a note card's relevance to an atom."""
    score = 0.0

    # Exact title match
    atom_lower = atom.content.lower()
    if card.title.lower() in atom_lower:
        score += 100
    for alias in card.aliases:
        if alias.lower() in atom_lower:
            score += 90

    # Fuzzy title match against keywords
    for kw in atom.keywords:
        title_ratio = fuzz.ratio(kw.lower(), card.title.lower())
        if title_ratio >= FUZZY_THRESHOLD:
            score += title_ratio * 0.5
        for alias in card.aliases:
            alias_ratio = fuzz.ratio(kw.lower(), alias.lower())
            if alias_ratio >= FUZZY_THRESHOLD:
                score += alias_ratio * 0.4

    # Keyword overlap with concepts
    atom_kw_set = {k.lower() for k in atom.keywords}
    concept_set = {c.lower() for c in card.concepts}
    quality_set = {q.lower() for q in card.qualities}

    concept_overlap = atom_kw_set & concept_set
    quality_overlap = atom_kw_set & quality_set

    score += len(concept_overlap) * 20
    score += len(quality_overlap) * 15

    # Entity overlap
    entity_set = {e.lower() for e in card.entities}
    entity_overlap = atom_kw_set & entity_set
    score += len(entity_overlap) * 25

    return score


def _get_backlink_stems(card: NoteCard, _all_cards: list[NoteCard]) -> list[str]:
    """Get stems of notes that link to this card."""
    stems: list[str] = []
    for bl in card.backlinks:
        stems.append(bl)
    return stems


def find_best_folder(
    atom: Atom,
    proposed_type: str,
) -> Optional[FolderArchetype]:
    """Find the most specific folder archetype for a given atom type."""
    archetypes = get_folder_archetypes()
    if not archetypes:
        return None

    scored: list[tuple[float, FolderArchetype]] = []

    type_to_root = {
        "entity": ["03-People"],
        "person": ["03-People"],
        "concept": ["05-Topics"],
        "fact": ["05-Topics"],
        "technique": ["05-Topics"],
        "project_update": ["04-Projects"],
        "task": ["04-Projects"],
        "reflection": ["05-Topics"],
        "topic": ["05-Topics"],
        "school": ["06-School"],
    }

    expected_roots = type_to_root.get(atom.atom_type, []) + type_to_root.get(
        proposed_type, []
    )

    for arch in archetypes:
        score = 0.0

        if arch.root_category in expected_roots:
            score += 50
        elif not expected_roots:
            score += 10

        # Keyword overlap with common terms
        atom_kw_set = {k.lower() for k in atom.keywords}
        term_set = {t.lower() for t in arch.common_terms}
        score += len(atom_kw_set & term_set) * 15

        # Note type match
        if proposed_type in arch.child_note_kinds:
            score += 30

        # Prefer deeper (more specific) folders
        depth = arch.path.count("/")
        score += depth * 5

        score *= arch.confidence

        if score > 0:
            scored.append((score, arch))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def check_basename_exists(basename: str) -> bool:
    """Check if a basename already exists among note cards."""
    cards = get_note_cards()
    existing = {
        c.current_path.rsplit("/", 1)[-1].replace(".md", "").lower() for c in cards
    }
    return basename.lower() in existing


def check_near_duplicate(basename: str) -> Optional[str]:
    """Check if a basename is a near-duplicate of an existing one."""
    cards = get_note_cards()
    for card in cards:
        existing_stem = card.current_path.rsplit("/", 1)[-1].replace(".md", "")
        ratio = fuzz.ratio(basename.lower(), existing_stem.lower())
        if ratio >= NEAR_DUPLICATE_THRESHOLD:
            return existing_stem
    return None
