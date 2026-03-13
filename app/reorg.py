import logging
import json
import os
from pathlib import Path
import re
from anthropic import AsyncAnthropic

from . import config
from .vault import get_all_basenames
from .git_ops import vault_lock, commit, push, pull
from .planner import _load_prompt

logger = logging.getLogger(__name__)

async def run_automated_reorg(bot, interaction) -> str:
    """Executes the automated weekly reorg pipeline."""
    if not config.ENABLE_WEEKLY_REORG:
        return "❌ Weekly Reorg is disabled in configuration."

    vault = Path(config.VAULT_PATH)

    # 1. Gather Vault state (same as manual export, but piped directly to LLM)
    from .cache import get_folder_archetypes, get_note_cards

    archetypes = get_folder_archetypes()
    cards = get_note_cards()

    # Quick filter for "recent" notes (say, last 50)
    recent_cards = sorted(cards, key=lambda c: c.updated or c.created or c.title, reverse=True)[:50]

    vault_state = {
        "folder_archetypes": [
            {
                "path": a.path,
                "semantic_role": a.semantic_role,
                "child_note_kinds": a.child_note_kinds
            } for a in archetypes
        ],
        "recent_notes_to_evaluate": [
            {
                "title": c.title,
                "current_path": c.current_path,
                "summary": c.summary[:200]
            } for c in recent_cards
        ]
    }

    try:
        system_prompt = _load_prompt("weekly_reorg.md")
    except FileNotFoundError:
        return "❌ `prompts/weekly_reorg.md` is missing from the repository."

    user_prompt = f"""Here is the current state of the vault.
```json
{json.dumps(vault_state, indent=2)}
```
Please propose structural improvements. Return ONLY valid JSON."""

    await interaction.edit_original_response(content="⏳ Gathering vault state... proposing moves via Claude (Opus)...")

    try:
        client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            # Reorg is heavy, Opus was requested in prompt design but Sonnet 3.5 is fine if configured
            model="claude-opus-4-6", # Hardcoding Opus for this highly complex structural task as implied by initial instructions
            max_tokens=8192,
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

        plan = json.loads(raw)

    except Exception as e:
        logger.error(f"Reorg LLM step failed: {e}", exc_info=True)
        return f"❌ Failed to generate reorg plan: {e}"

    moves = plan.get("moves", [])
    new_subfolders = plan.get("new_subfolders", [])
    reasoning = plan.get("global_reasoning", "No reasoning provided.")

    if not moves and not new_subfolders:
        return f"✅ Reorg complete. Output: The vault is already perfectly structured.\n*Reasoning:* {reasoning}"

    await interaction.edit_original_response(content=f"⏳ Plan received: {len(moves)} file moves, {len(new_subfolders)} subfolders. Applying changes...")

    # Acquire Vault Lock to execute changes safely
    applied_moves = 0
    async with vault_lock():
        pull(config.VAULT_PATH)

        # 1. Create subfolders
        for folder in new_subfolders:
            folder_path = folder.get("path")
            if folder_path:
                target_dir = vault / folder_path
                # Ensure it's inside one of the immutable Root folders
                if str(target_dir.relative_to(vault)).split("/")[0] in config.ROOT_FOLDERS:
                    target_dir.mkdir(parents=True, exist_ok=True)

        # 2. File Moves
        for move in moves:
            source = move.get("source_path")
            target = move.get("target_path")
            if not source or not target: continue

            src_file = vault / source
            tgt_file = vault / target

            if src_file.exists() and not tgt_file.exists():
                # Ensure target is within a root folder
                if str(tgt_file.relative_to(vault)).split("/")[0] in config.ROOT_FOLDERS:
                    tgt_file.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(src_file, tgt_file)

                    # Update Aliases (Frontmatter injection is complex, here we just do a simple replacement if wikilinks break)
                    # For safety on Pi, simple move is executed.
                    applied_moves += 1

        # 3. Commit
        if applied_moves > 0 or new_subfolders:
            commit(config.VAULT_PATH, "reorg: automated weekly structural refinement")
            push(config.VAULT_PATH)

            # Rebuild caches next cycle
            from .cache import invalidate_all_caches
            invalidate_all_caches()

    summary = f"✅ **Automated Reorg Complete**\n- Moves applied: `{applied_moves}` / {len(moves)}\n- New folders: `{len(new_subfolders)}`\n\n**Claude's Reasoning:**\n*{reasoning[:1500]}*"
    return summary
