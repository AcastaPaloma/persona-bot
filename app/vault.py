"""Vault file operations — scanning, reading, wikilink parsing, atomic writes.

All write operations use temp files outside the vault, then atomic move.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import frontmatter

from . import config

logger = logging.getLogger(__name__)

WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def vault_root() -> Path:
    return Path(config.VAULT_PATH)


def scan_tree() -> str:
    """Walk the vault and return a tree string of folder/file names (no content)."""
    lines: list[str] = []
    root = vault_root()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in sorted(dirnames)
            if d not in config.SKIP_DIRS and not d.startswith(".")
        ]

        depth = Path(dirpath).relative_to(root)
        indent = "  " * len(depth.parts)

        if dirpath != str(root):
            lines.append(f"{indent}{Path(dirpath).name}/")

        for f in sorted(filenames):
            if not f.startswith("."):
                lines.append(f"{indent}  {f}")

    return "\n".join(lines)


def scan_all_notes() -> list[dict]:
    """Scan every .md file, return list of {path, title, content, frontmatter, links}."""
    root = vault_root()
    notes: list[dict] = []

    for md_file in root.rglob("*.md"):
        rel = str(md_file.relative_to(root)).replace("\\", "/")

        if any(part in config.SKIP_DIRS or part.startswith(".") for part in md_file.parts):
            continue
        if rel.startswith("01-Daily/Capture-"):
            continue

        try:
            post = frontmatter.load(str(md_file))
        except Exception:
            try:
                text = md_file.read_text(encoding="utf-8", errors="ignore")
                post = frontmatter.Post(text)
            except Exception:
                continue

        content = post.content
        fm = dict(post.metadata) if post.metadata else {}

        title = fm.get("title", "")
        if not title:
            for line in content.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
        if not title:
            title = md_file.stem.replace("_", " ").replace("-", " ").title()

        links = extract_wikilinks(content)
        aliases = fm.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]

        notes.append({
            "path": rel,
            "title": title,
            "content": content,
            "frontmatter": fm,
            "links": links,
            "aliases": aliases,
            "stem": md_file.stem,
        })

    return notes


def extract_wikilinks(text: str) -> list[str]:
    """Extract all [[wikilinks]] from text, deduplicating while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for match in WIKILINK_RE.findall(text):
        link = match.strip()
        if link and link not in seen:
            seen.add(link)
            result.append(link)
    return result


def build_link_graph(notes: list[dict]) -> dict[str, list[str]]:
    """Build adjacency map: stem -> [linked stems]."""
    graph: dict[str, list[str]] = {}
    stem_set = {n["stem"] for n in notes}

    for note in notes:
        outbound: list[str] = []
        for link in note["links"]:
            link_stem = link.replace(" ", "_").replace("-", "_").lower()
            for s in stem_set:
                if s.lower() == link_stem or s.lower() == link.lower():
                    outbound.append(s)
                    break
        graph[note["stem"]] = outbound

    return graph


def compute_backlinks(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    """Invert the graph to get backlinks."""
    backlinks: dict[str, list[str]] = {}
    for source, targets in graph.items():
        for t in targets:
            backlinks.setdefault(t, []).append(source)
    return backlinks


def get_all_basenames() -> set[str]:
    """Return all .md basenames (without extension) in the vault."""
    root = vault_root()
    basenames: set[str] = set()
    for md_file in root.rglob("*.md"):
        if any(
            part in config.SKIP_DIRS or part.startswith(".")
            for part in md_file.parts
        ):
            continue
        basenames.add(md_file.stem)
    return basenames


def read_note(vault_relative_path: str) -> Optional[str]:
    full = vault_root() / vault_relative_path
    if not full.exists():
        return None
    return full.read_text(encoding="utf-8", errors="ignore")


def write_note_atomic(vault_relative_path: str, content: str) -> None:
    """Write content to a temp file in STATE_DIR/tmp, then atomically move into vault."""
    final_path = vault_root() / vault_relative_path
    final_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = Path(config.STATE_DIR) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"_write_{final_path.name}"

    try:
        tmp_path.write_text(content, encoding="utf-8")
        shutil.move(str(tmp_path), str(final_path))
        logger.info("Wrote note: %s", vault_relative_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def append_to_note(vault_relative_path: str, content: str) -> None:
    """Append content to an existing note."""
    full = vault_root() / vault_relative_path
    if not full.exists():
        logger.warning("Cannot append — file does not exist: %s", vault_relative_path)
        return
    with open(full, "a", encoding="utf-8") as f:
        f.write(content)
    logger.info("Appended to: %s", vault_relative_path)


def delete_note(vault_relative_path: str) -> bool:
    full = vault_root() / vault_relative_path
    if not full.exists():
        logger.warning("Cannot delete — file does not exist: %s", vault_relative_path)
        return False
    full.unlink()
    logger.info("Deleted: %s", vault_relative_path)
    return True


def add_related_link(vault_relative_path: str, link_title: str) -> bool:
    """Add a [[link]] to the ## Related section of a note. Returns True if modified."""
    content = read_note(vault_relative_path)
    if content is None:
        return False

    link_str = f"[[{link_title}]]"
    if link_str in content:
        return False

    if "## Related" in content:
        content = content.replace(
            "## Related\n", f"## Related\n- {link_str}\n", 1
        )
    else:
        content = content.rstrip() + f"\n\n## Related\n- {link_str}\n"

    write_note_atomic(vault_relative_path, content)
    return True


def scan_folders() -> list[dict]:
    """Scan vault folders and their child note filenames. For archetype generation."""
    root = vault_root()
    folders: list[dict] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in sorted(dirnames)
            if d not in config.SKIP_DIRS and not d.startswith(".")
        ]

        rel = str(Path(dirpath).relative_to(root)).replace("\\", "/")
        if rel == ".":
            continue

        md_children = [f for f in filenames if f.endswith(".md") and not f.startswith(".")]
        if not md_children:
            continue

        parts = rel.split("/")
        root_cat = parts[0] if parts else ""

        folders.append({
            "path": rel,
            "root_category": root_cat,
            "children": [Path(f).stem for f in md_children],
            "child_filenames": md_children,
        })

    return folders
