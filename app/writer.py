"""Note rendering and atomic writes using strict templates.

Handles:
- Creating new notes from NotePlan
- Appending to existing notes
- Adding cross-links to existing notes
"""

import logging
import re
from datetime import datetime

from . import templates
from .schemas import CrossLink, NotePlan
from .vault import add_related_link, append_to_note, read_note, write_note_atomic

logger = logging.getLogger(__name__)


def to_filename(title: str, convention: str = "lowercase_snake_case") -> str:
    """Convert a title to a filename following the given convention."""
    clean = re.sub(r"[^\w\s-]", "", title)
    clean = clean.strip()

    if convention == "lowercase_snake_case":
        return re.sub(r"[\s-]+", "_", clean).lower()
    elif convention == "lowercase_kebab_case":
        return re.sub(r"[\s_]+", "-", clean).lower()
    elif convention == "Title-Case":
        words = clean.split()
        return "-".join(w.capitalize() for w in words)
    else:
        return re.sub(r"[\s-]+", "_", clean).lower()


def render_note(plan: NotePlan, created: datetime | None = None) -> str:
    """Render a note from a plan using the appropriate strict template."""
    now = created or datetime.now().astimezone()

    if plan.note_type == "topic":
        return templates.topic_note(
            title=plan.title,
            summary=_extract_section(plan.content, "Summary") or plan.content[:200],
            content=_extract_section(plan.content, "Content") or plan.content,
            related_notes=plan.related_notes,
            created=now,
        )
    elif plan.note_type == "person":
        return templates.person_note(
            title=plan.title,
            summary=_extract_section(plan.content, "Summary") or plan.content[:200],
            notes_content=_extract_section(plan.content, "Notes") or plan.content,
            related_notes=plan.related_notes,
            created=now,
        )
    elif plan.note_type == "project":
        return templates.project_note(
            title=plan.title,
            summary=_extract_section(plan.content, "Summary") or plan.content[:200],
            progress=_extract_section(plan.content, "Progress") or plan.content,
            related_notes=plan.related_notes,
            created=now,
        )
    elif plan.note_type == "technique":
        domain = _infer_domain(plan.target_path)
        return templates.technique_note(
            title=plan.title,
            summary=_extract_section(plan.content, "Summary") or plan.content[:200],
            details=_extract_section(plan.content, "Details") or plan.content,
            related_notes=plan.related_notes,
            domain=domain,
            created=now,
        )
    elif plan.note_type == "school":
        subject = _infer_subject(plan.target_path)
        return templates.school_note(
            title=plan.title,
            summary=_extract_section(plan.content, "Summary") or plan.content[:200],
            content=_extract_section(plan.content, "Content") or plan.content,
            related_notes=plan.related_notes,
            subject=subject,
            created=now,
        )
    else:
        return templates.topic_note(
            title=plan.title,
            summary=plan.content[:200],
            content=plan.content,
            related_notes=plan.related_notes,
            created=now,
        )


def execute_create(plan: NotePlan) -> bool:
    """Create a new note from a plan. Returns True on success."""
    content = render_note(plan)

    # Validate minimum viable note
    if not _validate_minimum(content, plan.title):
        logger.error("Note %s does not meet minimum requirements", plan.title)
        return False

    try:
        write_note_atomic(plan.target_path, content)
        return True
    except Exception as e:
        logger.error("Failed to create note %s: %s", plan.target_path, e)
        return False


def execute_append(plan: NotePlan, date_str: str) -> bool:
    """Append content to an existing note with a dated section header."""
    existing = read_note(plan.target_path)
    if existing is None:
        logger.warning("Cannot append — note not found: %s", plan.target_path)
        return False

    append_content = f"\n\n## {date_str}\n{plan.content}\n"

    # Also add any new related links
    for link_title in plan.related_notes:
        link_str = f"[[{link_title}]]"
        if link_str not in existing:
            add_related_link(plan.target_path, link_title)

    append_to_note(plan.target_path, append_content)
    return True


def execute_cross_links(cross_links: list[CrossLink]) -> int:
    """Add cross-links between existing notes. Returns count of links added."""
    added = 0
    for cl in cross_links:
        if add_related_link(cl.source_path, cl.target_title):
            added += 1
            logger.info("Cross-linked %s -> [[%s]]", cl.source_path, cl.target_title)
    return added


def _extract_section(content: str, section_name: str) -> str:
    """Try to extract a section from LLM-generated content."""
    pattern = rf"##\s*{section_name}\s*\n(.*?)(?=\n##|\Z)"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _infer_domain(path: str) -> str:
    parts = path.lower().split("/")
    if "bjj" in parts:
        return "BJJ"
    if "school" in parts:
        return "academics"
    return ""


def _infer_subject(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 2:
        return parts[1].replace("-", " ").replace("_", " ")
    return ""


def _validate_minimum(content: str, title: str) -> bool:
    """Validate a note meets minimum viable note requirements."""
    has_frontmatter = content.startswith("---")
    has_title = f"# {title}" in content or "# " in content
    has_summary = "## Summary" in content
    has_related = "## Related" in content

    if not all([has_frontmatter, has_title, has_summary, has_related]):
        logger.warning(
            "Note validation failed for '%s': fm=%s title=%s summary=%s related=%s",
            title,
            has_frontmatter,
            has_title,
            has_summary,
            has_related,
        )
        return False
    return True
