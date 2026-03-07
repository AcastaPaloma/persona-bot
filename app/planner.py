"""Action planning — atomize captures, retrieve candidates, decide actions.

This is the LLM-powered brain of the distillation pipeline.
"""

import json
import logging
from pathlib import Path

import anthropic

from . import config
from .cache import get_folder_archetypes
from .retriever import check_near_duplicate, retrieve_candidates
from .schemas import Atom, CrossLink, NotePlan
from .state import is_tombstoned
from .vault import get_all_basenames

logger = logging.getLogger(__name__)


def _load_prompt(name: str) -> str:
    path = Path(__file__).parent.parent / "prompts" / name
    return path.read_text(encoding="utf-8")


def _call_anthropic(system: str, user: str, max_tokens: int = 4096) -> str:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": user}],
        system=system,
    )
    return response.content[0].text.strip()


def _parse_json(raw: str) -> dict | list:
    """Parse JSON from LLM output, stripping markdown fences if present."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
        text = text.strip()
    return json.loads(text)


# ── Phase 1: Atomize ─────────────────────────────────────────────────────────

def atomize_captures(capture_texts: list[str]) -> list[Atom]:
    """Extract atomic knowledge items from concatenated capture texts."""
    if not capture_texts:
        return []

    combined = "\n\n---\n\n".join(capture_texts)
    system_prompt = _load_prompt("atomize.md")

    logger.info("Atomizing %d capture texts (%d chars)...", len(capture_texts), len(combined))

    try:
        raw = _call_anthropic(system_prompt, combined)
        atoms_data = _parse_json(raw)

        if not isinstance(atoms_data, list):
            logger.error("Atomize returned non-list: %s", type(atoms_data))
            return []

        atoms = []
        for item in atoms_data:
            try:
                atoms.append(Atom.model_validate(item))
            except Exception as e:
                logger.warning("Skipping invalid atom: %s — %s", item, e)

        logger.info("Extracted %d atoms from captures", len(atoms))
        return atoms

    except Exception as e:
        logger.error("Atomization failed: %s", e)
        return []


# ── Phase 2+3: Retrieve + Plan ───────────────────────────────────────────────

def plan_actions(
    atoms: list[Atom],
) -> tuple[list[NotePlan], list[CrossLink], list[str], list[str]]:
    """Retrieve candidates and plan actions for all atoms.

    Returns:
        (plans, cross_links, daily_highlights, daily_related)
    """
    if not atoms:
        return [], [], [], []

    # Retrieve candidates for each atom
    all_candidates: list[dict] = []
    for atom in atoms:
        candidates = retrieve_candidates(atom, top_k=5)
        all_candidates.append({
            "atom": atom.model_dump(),
            "candidates": [
                {
                    "path": c.current_path,
                    "title": c.title,
                    "summary": c.summary,
                    "qualities": c.qualities,
                    "concepts": c.concepts,
                    "outbound_links": c.outbound_links[:5],
                }
                for c in candidates
            ],
        })

    # Build folder context
    archetypes = get_folder_archetypes()
    archetype_context = json.dumps(
        [
            {
                "path": a.path,
                "semantic_role": a.semantic_role,
                "naming_convention": a.naming_convention,
                "example_children": a.example_children,
                "child_note_kinds": a.child_note_kinds,
            }
            for a in archetypes
        ],
        indent=1,
    )

    system_prompt = _load_prompt("plan_actions.md")
    user_prompt = f"""## Atoms and Candidates
{json.dumps(all_candidates, indent=1)}

## Folder Archetypes
{archetype_context}

## Existing Note Basenames (for uniqueness check)
{json.dumps(sorted(get_all_basenames())[:200])}

Now decide actions for each atom. Return JSON only."""

    logger.info("Planning actions for %d atoms...", len(atoms))

    try:
        raw = _call_anthropic(system_prompt, user_prompt, max_tokens=8192)
        result = _parse_json(raw)

        if not isinstance(result, dict):
            logger.error("Plan returned non-dict: %s", type(result))
            return [], [], [], []

        plans_data = result.get("plans", [])
        cross_links_data = result.get("cross_links", [])
        daily_highlights = result.get("daily_highlights", [])
        daily_related = result.get("daily_related", [])

        plans = []
        for p in plans_data:
            try:
                plan = NotePlan.model_validate(p)
                plans.append(plan)
            except Exception as e:
                logger.warning("Skipping invalid plan: %s — %s", p, e)

        cross_links = []
        for cl in cross_links_data:
            try:
                cross_links.append(CrossLink.model_validate(cl))
            except Exception as e:
                logger.warning("Skipping invalid cross-link: %s — %s", cl, e)

        logger.info(
            "Planned %d actions, %d cross-links, %d highlights",
            len(plans),
            len(cross_links),
            len(daily_highlights),
        )
        return plans, cross_links, daily_highlights, daily_related

    except Exception as e:
        logger.error("Action planning failed: %s", e)
        return [], [], [], []


# ── Phase 4: Resolve Same-Batch References ────────────────────────────────────

def resolve_batch_references(plans: list[NotePlan]) -> list[NotePlan]:
    """Resolve temp IDs in related_notes to actual note titles."""
    id_to_title: dict[str, str] = {}
    for plan in plans:
        if plan.action == "create":
            id_to_title[plan.planned_id] = plan.title

    for plan in plans:
        resolved_related: list[str] = []
        for ref in plan.related_notes:
            if ref in id_to_title:
                resolved_related.append(id_to_title[ref])
            else:
                resolved_related.append(ref)
        plan.related_notes = resolved_related
        plan.resolved = True

    return plans


# ── Phase 5: Validate ────────────────────────────────────────────────────────

def validate_plans(plans: list[NotePlan]) -> tuple[list[NotePlan], list[str]]:
    """Validate plans and reject invalid ones. Returns (valid_plans, errors)."""
    valid: list[NotePlan] = []
    errors: list[str] = []
    existing_basenames = get_all_basenames()
    planned_basenames: set[str] = set()

    for plan in plans:
        if plan.action != "create":
            valid.append(plan)
            continue

        basename = Path(plan.target_path).stem

        # Check globally unique basename
        if basename.lower() in {b.lower() for b in existing_basenames}:
            errors.append(
                f"REJECTED: '{plan.title}' — basename '{basename}' already exists"
            )
            continue

        # Check planned batch uniqueness
        if basename.lower() in {b.lower() for b in planned_basenames}:
            errors.append(
                f"REJECTED: '{plan.title}' — duplicate basename in batch"
            )
            continue

        # Check near-duplicate
        near_dup = check_near_duplicate(basename)
        if near_dup:
            errors.append(
                f"REJECTED: '{plan.title}' — near-duplicate of '{near_dup}'"
            )
            continue

        # Check tombstone
        if is_tombstoned(f"{basename}.md"):
            errors.append(
                f"REJECTED: '{plan.title}' — matches active tombstone"
            )
            continue

        # Check root placement
        parts = plan.target_path.replace("\\", "/").split("/")
        if len(parts) < 2 or parts[0] not in config.ROOT_FOLDERS:
            errors.append(
                f"REJECTED: '{plan.title}' — not inside a root folder"
            )
            continue

        # Check subfolder creation
        target_dir = Path(config.VAULT_PATH) / Path(plan.target_path).parent
        if not target_dir.exists():
            parent_of_new = target_dir.parent
            if parent_of_new.exists():
                errors.append(
                    f"REJECTED: '{plan.title}' — would create new subfolder '{target_dir.name}'"
                )
                continue
            else:
                errors.append(
                    f"REJECTED: '{plan.title}' — target directory tree doesn't exist"
                )
                continue

        planned_basenames.add(basename)
        valid.append(plan)

    if errors:
        for e in errors:
            logger.warning(e)

    logger.info("Validation: %d valid, %d rejected", len(valid), len(errors))
    return valid, errors
