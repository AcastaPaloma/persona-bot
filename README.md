# Persona Bot — V1 Functional Specification

A Discord-to-Obsidian knowledge automation system. Capture raw text via Discord, organize nightly via LLM, sync via Git. Runs 24/7 on a Raspberry Pi with zero ongoing cost beyond light Anthropic API credits.

> **This document is the single source of truth for implementation.**
> Every schema, flow, safety rule, and edge case is specified here.
> Reference the chat conversation history for additional rationale and discussion context behind each design decision.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Core Concepts](#2-core-concepts)
3. [Vault Structure and Rules](#3-vault-structure-and-rules)
4. [Note Templates](#4-note-templates)
5. [Schemas](#5-schemas)
6. [Capture Flow](#6-capture-flow)
7. [Distillation Flow](#7-distillation-flow)
8. [Delete Flow](#8-delete-flow)
9. [Discord Interface](#9-discord-interface)
10. [Git Rules](#10-git-rules)
11. [Machine State Management](#11-machine-state-management)
12. [Context System](#12-context-system)
13. [Safety Rules and Edge Cases](#13-safety-rules-and-edge-cases)
14. [Weekly Opus Reorg](#14-weekly-opus-reorg)
15. [Tech Stack](#15-tech-stack)
16. [Environment Variables](#16-environment-variables)
17. [Deployment](#17-deployment)
18. [Project File Structure](#18-project-file-structure)

---

## 1. Architecture Overview

```
Discord DM / /log
  |
  v
Capture Ingest (no LLM)
  |---> Append to 01-Daily/Capture-YYYY-MM-DD.md
  |---> Record capture_event in SQLite (status=pending)
  |---> Git commit + push
  |
  v
Nightly / Manual Distillation (Anthropic LLM)
  |---> Load undistilled capture_events from SQLite
  |---> Refresh folder archetypes + note cards from vault
  |---> Atomize captures into concepts/entities/facts
  |---> Retrieve candidate notes (fuzzy + semantic + graph)
  |---> Plan actions: create / append / link
  |---> Resolve same-batch references (temp IDs -> final paths)
  |---> Validate plan (no duplicates, no broken links, no root-level files)
  |---> Write notes using strict templates
  |---> Git commit + push
  |---> Mark events as distilled ONLY after successful push
  |
  v
Weekly Opus Reorg (manual, human-triggered)
  |---> Full vault scan
  |---> Opus plans moves / renames / new subfolders / link rewrites
  |---> Apply changes
  |---> Full rebuild of folder archetypes + note cards + graph
  |---> Git commit + push
```

### Design Principles

- **Capture is fast and cheap**: no LLM call at ingest time.
- **Organization is batched**: nightly distillation sees the full day and makes better decisions.
- **Structure is sacred**: existing folder conventions, naming styles, and link integrity are first-class concerns.
- **Machine state stays outside the vault**: no temp files, caches, or metadata inside the Obsidian vault.
- **Git is the reconciliation layer**: every mutation follows pull -> apply -> commit -> push.
- **Idempotency is enforced**: explicit markers prevent re-distillation. Tombstones prevent re-creation of deleted notes.

---

## 2. Core Concepts

### Capture Event
One Discord message becomes one capture event. This is the atomic unit of idempotency.

| Field      | Type     | Description                                      |
|------------|----------|--------------------------------------------------|
| id         | str      | Stable unique ID (UUID or Discord message ID)    |
| timestamp  | datetime | When the message was sent (timezone-aware)       |
| author     | str      | Discord username                                 |
| raw_text   | str      | Full message content                             |
| status     | enum     | `pending` / `distilled`                          |
| distilled_at | datetime | When distillation processed this event (nullable) |

### Daily Capture File
Append-only, human-readable log of the day's capture events. Lives at `01-Daily/Capture-YYYY-MM-DD.md`. This is a vault file visible in Obsidian but is NOT machine state. Machine state lives in SQLite.

### Distillation
Batch processing of undistilled capture events into vault actions: note creation, note appends, link creation. Runs nightly on schedule or manually via `/distill`. Uses explicit markers in SQLite to guarantee idempotency.

### Note Card
Compressed representation of a single note, used as LLM context instead of full note content. Generated from vault files and stored in external cache. Rebuilt on demand.

### Folder Archetype
Compressed representation of a folder or subfolder. Captures the semantic role, naming conventions, and child note patterns. Generated from vault structure and stored in external cache. Rebuilt on demand.

### Tombstone
Record of a deleted note. Prevents the nightly organizer from recreating the same note from stale capture context. Stored in SQLite with TTL.

---

## 3. Vault Structure and Rules

### Fixed Root Folders (immutable)

```
01-Daily/           <- daily captures + distilled summaries
03-People/          <- personality profiles, people notes
04-Projects/        <- project folders with notes inside
05-Topics/          <- topic notes, grouped in subfolders
06-School/          <- academic notes
99-Archive/         <- retired material
LifeOutside/        <- external life notes (BJJ, etc.)
LifeInside/         <- internal life notes (cooking, reflection, etc.)
_Templates/         <- note templates (not indexed as content)
```

These root folders must never be created, deleted, or renamed by the bot or weekly reorg. They are the fixed taxonomy.

### Subfolder Rules

- **Nightly organizer**: must strongly prefer existing subfolders. If no good subfolder exists, place the note directly under the correct root folder. Must NOT create new subfolders.
- **Weekly Opus reorg**: may create, remove, or restructure subfolders. May move files between subfolders. Must preserve root folder taxonomy.

### Naming Conventions

- **Filenames**: `lowercase_snake_case.md` (e.g., `rear_naked_choke.md`, `fine_cuisine.md`)
- **In-note titles**: `# Title Case` (e.g., `# Rear Naked Choke`)
- **Globally unique basenames**: no two notes in the entire vault may share the same filename, regardless of folder. This prevents ambiguous `[[wikilinks]]`.
- **Convention inheritance**: when placing a note in an existing subfolder, the bot must infer and follow the naming convention of sibling notes in that folder. If `05-Topics/BJJ/techniques/` contains `double_leg_takedown.md`, `side_control.md`, `close_guard.md`, then a new technique must follow the same `lowercase_snake_case` style.

### Wikilink Rules

- Use `[[Note Title]]` style links.
- Only link to notes that exist in the vault or are being created in the same batch.
- Never hallucinate links to nonexistent notes.
- References go in the `## Related` section at the bottom of the note, not forced inline.
- Obsidian backlinks provide reverse awareness automatically; the system does not need to edit old notes just to create a reverse link.
- Old notes should only be edited to add explicit links when the relationship is strong enough to deserve visible mention.

---

## 4. Note Templates

Every note created by the bot must follow a strict template based on its category. This reduces model drift and ensures vault consistency.

### Daily Summary

Path: `01-Daily/Daily/YYYY-MM-DD.md`

```markdown
---
date: YYYY-MM-DD
type: daily-summary
created: YYYY-MM-DDTHH:MM:SS
tags:
  - daily
---

# Daily Summary — Weekday, Month DD, YYYY

## Highlights
- Key insight, event, or decision from the day.
- Another highlight.

## Captures
Synthesized summary of what was captured today. High-signal only.

## Related
- [[note_a]]
- [[note_b]]
```

### Topic Note

Path: `05-Topics/<optional-subfolder>/<note_name>.md`

```markdown
---
title: Note Title
type: topic
created: YYYY-MM-DDTHH:MM:SS
aliases: []
---

# Note Title

## Summary
1-3 sentence description of what this topic covers.

## Content
Main content, observations, or knowledge.

## Related
- [[note_a]]
- [[note_b]]
```

### Person Note

Path: `03-People/<person_name>.md`

```markdown
---
title: Person Name
type: person
created: YYYY-MM-DDTHH:MM:SS
aliases: []
---

# Person Name

## Summary
Who they are, relationship context, and key traits.

## Notes
Ongoing observations and interactions.

## Related
- [[note_a]]
- [[note_b]]
```

### Project Note

Path: `04-Projects/<project-folder>/README.md` or `04-Projects/<project_name>.md`

```markdown
---
title: Project Name
type: project
created: YYYY-MM-DDTHH:MM:SS
aliases: []
status: active
---

# Project Name

## Summary
What the project is and its current state.

## Progress
Updates, milestones, and recent work.

## Related
- [[note_a]]
- [[note_b]]
```

### Technique Note

Path: varies by domain (e.g., `05-Topics/BJJ/techniques/<technique_name>.md`)

```markdown
---
title: Technique Name
type: technique
created: YYYY-MM-DDTHH:MM:SS
aliases: []
domain: <domain>
---

# Technique Name

## Summary
Brief description of the technique.

## Details
Step-by-step breakdown or key points.

## Related
- [[note_a]]
- [[note_b]]
```

### School Note

Path: `06-School/<subject-folder>/<note_name>.md`

```markdown
---
title: Note Title
type: school
created: YYYY-MM-DDTHH:MM:SS
aliases: []
subject: <subject>
---

# Note Title

## Summary
Brief academic summary.

## Content
Main academic content.

## Related
- [[note_a]]
- [[note_b]]
```

### Minimum Viable Note

Even a thin, newly created note must contain at minimum:
- YAML frontmatter with title, type, created, aliases
- `# Title`
- `## Summary` with at least one sentence
- `## Related` with at least one link (even if only to the daily summary)

Empty shells are never acceptable.

---

## 5. Schemas

All schemas are defined as Pydantic models.

### CaptureEvent

```python
class CaptureEvent(BaseModel):
    id: str                     # stable unique ID (UUID or discord message ID)
    timestamp: datetime         # timezone-aware, when message was sent
    author: str                 # discord username
    raw_text: str               # full message content
    status: Literal["pending", "distilled"] = "pending"
    distilled_at: Optional[datetime] = None
```

### NoteCard

Compressed representation of one vault note. Used as LLM context instead of full note content.

```python
class NoteCard(BaseModel):
    note_id: str                # stable internal ID (survives renames/moves)
    current_path: str           # vault-relative path (e.g. "05-Topics/BJJ/techniques/close_guard.md")
    title: str                  # human-readable title from frontmatter or H1
    aliases: list[str]          # alternative names, old titles after rename
    summary: str                # 1-3 sentence description of the note
    qualities: list[str]        # latent traits (e.g. "precision", "delicacy", "craftsmanship")
    concepts: list[str]         # key topics/entities mentioned
    entities: list[str]         # people, places, projects referenced
    outbound_links: list[str]   # wikilinks this note contains
    backlinks: list[str]        # notes that link TO this note
    representative_snippets: list[str]  # 2-5 short excerpts that capture the note's character
    fingerprint: str            # content hash for identity across moves/renames
    created: Optional[datetime] = None
    updated: Optional[datetime] = None
```

**Why `qualities`**: A title + summary alone cannot surface deep cross-domain connections. A note about fine cuisine might have qualities like `["precision", "delicacy", "timing"]` which connect it to a BJJ technique note with similar qualities. This field is the bridge that enables non-obvious linking.

**Why `representative_snippets`**: Summaries are lossy. Snippets preserve specific phrasing and detail that summaries compress away. Example: a summary says "overview of dishes on a Paris trip" but a snippet says "focused on avoiding minute errors in preparation" — only the snippet connects to dexterity.

**Why `fingerprint`**: Note paths change during weekly reorg. The fingerprint (based on title + content hash) lets the system recognize a moved note as the same logical entity, preventing duplicate creation.

### FolderArchetype

Compressed representation of one folder or subfolder. Captures conventions that new notes must follow.

```python
class FolderArchetype(BaseModel):
    path: str                   # vault-relative path (e.g. "05-Topics/BJJ/techniques")
    root_category: str          # which root folder this belongs to (e.g. "05-Topics")
    semantic_role: str          # what kind of notes live here (e.g. "BJJ technique reference notes")
    child_note_kinds: list[str] # types of notes found (e.g. ["technique"])
    naming_convention: str      # inferred pattern (e.g. "lowercase_snake_case")
    common_terms: list[str]     # vocabulary common across children
    example_children: list[str] # 3-5 representative note basenames
    confidence: float           # 0.0-1.0, how confident the inference is
```

**Folder archetypes are generated, not handwritten.** They are inferred from the current children of each folder and refreshed:
- incrementally after each nightly distillation (update only affected folders)
- fully after each weekly reorg (rebuild all)

### NotePlan

Internal planning structure for distillation. Not persisted long-term.

```python
class NotePlan(BaseModel):
    planned_id: str             # temporary ID for same-batch cross-referencing
    action: Literal["create", "append", "link"]
    target_path: str            # vault-relative path (final, after resolution)
    note_type: str              # template category (topic, person, project, technique, school)
    title: str                  # human-readable title
    content: str                # rendered markdown content
    related_notes: list[str]    # note basenames to link in ## Related
    resolved: bool = False      # whether temp references have been replaced with final paths
```

### Tombstone

Prevents re-creation of deleted notes from stale capture context.

```python
class Tombstone(BaseModel):
    note_id: str                # note_id from the deleted NoteCard
    original_path: str          # where the note was before deletion
    title: str                  # note title for reference
    deleted_at: datetime
    reason: str                 # "user_delete" or other
    expires_at: datetime        # tombstone TTL (default: 30 days)
```

---

## 6. Capture Flow

This is the hot path. It must be fast, reliable, and cheap.

**Trigger**: Any DM to the bot, or `/log <text>` in a guild channel.

**Steps**:

1. Receive Discord message.
2. Ignore if: empty, from the bot itself, or non-text.
3. Generate a stable `capture_event` ID (use Discord message ID for natural deduplication).
4. Acquire global operation lock.
5. `git pull --rebase` on the vault.
6. Append raw text to `01-Daily/Capture-YYYY-MM-DD.md` with timestamp header.
7. Insert `CaptureEvent` into SQLite with `status=pending`.
8. `git add -A && git commit -m "capture: <truncated text>" && git push`.
9. Release lock.
10. Reply to user with a minimal confirmation (checkmark emoji or short ack).

**No LLM call happens during capture.** This keeps latency low and cost at zero for the capture path.

**Capture file format** (append-only):

```markdown
---
date: YYYY-MM-DD
type: daily-capture
---

# Captures — Weekday, Month DD, YYYY

## HH:MM
<raw text from message>

---

## HH:MM
<raw text from next message>

---
```

Each `## HH:MM` block corresponds to one capture event. The separator `---` between entries keeps them visually distinct in Obsidian.

---

## 7. Distillation Flow

This is the core intelligence of the system. It transforms raw captures into organized, linked vault notes.

**Trigger**: Nightly schedule (default 23:59) or manual `/distill` command.

**Steps**:

### Phase 0: Setup
1. Acquire global operation lock.
2. `git pull --rebase` on the vault.
3. Load all `CaptureEvent` records with `status=pending` from SQLite. If none exist, exit early.
4. Refresh folder archetypes from current vault state.
5. Refresh note cards from current vault state.

### Phase 1: Atomize
6. Concatenate all pending capture events (grouped by day, ordered by timestamp).
7. Send to Anthropic with a system prompt instructing it to extract atomic knowledge items.

Each atomic item should be classified as one of:
- **entity**: a person, place, organization
- **concept**: a topic, idea, theme
- **fact**: a specific observation or piece of information
- **technique**: a skill, method, or procedure
- **project_update**: progress on an existing project
- **task**: an actionable item
- **reflection**: a personal thought, feeling, or insight

Output: list of atoms, each with a type, content summary, and candidate keywords.

### Phase 2: Retrieve Candidates
8. For each atom, find candidate existing notes using (in order of priority):
   - Exact title/alias match (from note cards)
   - Fuzzy title match via RapidFuzz (threshold >= 80)
   - Keyword overlap with note card concepts/qualities
   - Graph neighborhood expansion (notes linked to matched candidates)
   - Folder prior (if the atom's type maps to a known root folder)

9. For each atom, also find the best candidate folder using folder archetypes:
   - Match atom type to folder semantic roles
   - Score naming convention fit
   - Prefer the most specific existing subfolder whose children share the same semantic class

### Phase 3: Plan Actions
10. Send atoms + top candidate note cards + relevant folder archetypes to Anthropic.

The model decides for each atom:
- **append**: add information to an existing note (specify which note)
- **create**: create a new note (specify proposed title, path, type, content)
- **link**: only add a link reference, no new content needed
- **daily_only**: include only in the daily summary, not worth a permanent note

For `create` actions, the model must:
- Propose a filename following the naming convention of the target folder
- Propose content using the strict template for that note type
- List all related notes (existing + other planned notes in this batch)

### Phase 4: Resolve Same-Batch References
11. Planned notes that reference each other use temporary IDs during planning.
12. After all plans are finalized, resolve temp IDs to actual file paths and titles.
13. Update all `## Related` sections with resolved references.

### Phase 5: Validate
14. Reject any plan that:
    - Creates a note with a basename that already exists in the vault
    - Creates a note at the vault root (outside fixed root folders)
    - Links to a note that does not exist and is not being created in this batch
    - Creates an empty note (must meet minimum viable note requirements)
    - Creates a note whose basename matches a tombstone within TTL
    - Creates a new subfolder (reserved for weekly reorg)

### Phase 6: Write
15. For each validated plan:
    - Render the note content using the strict template for its type
    - Write to a temp file outside the vault (`STATE_DIR/tmp/`)
    - Validate the rendered markdown (frontmatter parses, links are correct)
    - Atomically move the temp file into the vault at the target path
16. For append actions: read existing note, append content (with dated section header `## YYYY-MM-DD`), write back.
17. For link-only actions: read existing note, add links to `## Related` section if not already present, write back.

### Phase 7: Cross-Link Old Notes
18. If a new capture reveals that two **existing** notes should be connected (even if neither was created or appended to in this batch), add links between them.
19. This is a core feature, not optional. New information should strengthen the vault's graph, not just add nodes.

### Phase 8: Daily Summary
20. Generate the daily summary note (`01-Daily/Daily/YYYY-MM-DD.md`) using the daily summary template.
21. The daily summary links to every note touched during this distillation run.

### Phase 9: Commit and Finalize
22. `git add -A && git commit -m "distill: YYYY-MM-DD (<N> notes created, <M> appended, <L> linked)" && git push`.
23. **Only after successful push**: update all processed `CaptureEvent` records to `status=distilled` with `distilled_at=now()`.
24. Update affected folder archetypes and note cards in cache.
25. Release lock.

**Critical**: If push fails, do NOT mark events as distilled. They will be retried on the next run.

---

## 8. Delete Flow

**Trigger**: `/delete` slash command with autocomplete.

**Steps**:

1. User invokes `/delete`. Autocomplete populates from the note card index (title + path).
2. User selects a note.
3. Bot shows confirmation message containing:
   - Note title
   - Full vault-relative path
   - Short summary (from note card)
   - Outbound link count
   - Backlink count
   - Created/last updated timestamps
4. User confirms (button or reaction).
5. Acquire global operation lock.
6. `git pull --rebase`.
7. Delete the file from the vault.
8. `git add -A && git commit -m "delete: <note_title>" && git push`.
9. Record a `Tombstone` in SQLite (default TTL: 30 days).
10. Remove the note from the note card cache.
11. Update folder archetype cache if the parent folder changed.
12. Release lock.
13. Confirm deletion to user with the path that was removed.

**Tombstone purpose**: The nightly organizer might see old captures that reference concepts from the deleted note. Without a tombstone, it could recreate the note. The tombstone prevents this for a configurable window.

---

## 9. Discord Interface

### Natural Language (default)
Any DM to the bot is treated as raw capture input. No commands, no formatting, no structure required. The user just dumps text.

### Slash Commands

| Command    | Description                                           | Details                                      |
|------------|-------------------------------------------------------|----------------------------------------------|
| `/log`     | Log a thought from a guild channel                    | Same as DM capture but works in servers      |
| `/distill` | Manually trigger distillation of pending captures     | Processes ALL pending events across all days  |
| `/delete`  | Delete a vault note                                   | Autocomplete + confirmation before deletion  |
| `/status`  | Bot health check                                      | Pending captures, last distill, push status  |

### What the bot does NOT do via chat
- Edit note content (use Obsidian for that)
- Manually assign folders or paths
- Rich markdown authoring
- Note search or browsing (future feature)

### Feedback Policy
- Capture confirmation: minimal (checkmark or short ack)
- Distillation: silent. The user checks results in Obsidian's graph view.
- Delete: explicit confirmation before and after.
- The bot does not explain its organizational reasoning by default.

---

## 10. Git Rules

### Every vault mutation follows this sequence:
1. Acquire global operation lock (prevents concurrent mutations)
2. `git pull --rebase`
3. Apply changes to vault files
4. `git add -A`
5. `git commit -m "<descriptive message>"`
6. `git push` (with retry, max 3 attempts, 5s backoff)
7. Release lock

### Commit messages by action type:
- Capture: `capture: <first 50 chars of text>`
- Distillation: `distill: YYYY-MM-DD (<N> created, <M> appended, <L> linked)`
- Delete: `delete: <note_title>`
- Weekly reorg: `reorg: <brief description>`

### Failure handling:
- If `git pull` fails: log warning, continue with local state. Push will fail and retry next time.
- If `git push` fails after all retries: changes are committed locally. Do NOT mark distillation events as processed. Next cycle will retry.
- Never force push.
- Never rebase over local unpushed commits.
- Never discard local changes.

---

## 11. Machine State Management

### Location
All machine state lives **completely outside the vault** at a configurable path.

Default: `~/.local/share/persona-bot/` (Linux/Pi) or `%LOCALAPPDATA%/persona-bot/` (Windows).

Set via `STATE_DIR` environment variable.

### Directory Layout

```
STATE_DIR/
  state.db              <- SQLite: capture events, distill markers, tombstones
  cache/
    note_cards.json     <- current note card index
    folder_archetypes.json <- current folder archetype index
    graph.json          <- current link graph (adjacency map)
  tmp/                  <- render temp files (never inside vault)
  logs/                 <- operational logs (if not using project-local logs/)
```

### Why outside the vault?
- Prevents accidental indexing of machine artifacts
- Prevents Obsidian plugins from interfering
- Prevents human confusion
- Prevents the LLM from learning from its own metadata
- Prevents git noise from cache churn

### Rebuilding
All cached state (note cards, folder archetypes, graph) is **derived** and can be regenerated from the vault at any time.

- After nightly distillation: incrementally update affected entries.
- After weekly reorg: full rebuild of all derived state.
- On corruption or loss: full rebuild from scratch by scanning the vault.

### Note Card Generation
Note cards are generated by:
1. Scanning each `.md` file in the vault (excluding `_Templates/`, hidden dirs, capture files).
2. Parsing frontmatter for title, type, aliases, created date.
3. Extracting outbound `[[wikilinks]]`.
4. Computing backlinks from the global link graph.
5. Using Anthropic to generate: summary, qualities, concepts, entities, representative_snippets.
6. Computing a fingerprint from title + content hash.

This LLM call happens during cache rebuild, not on every capture. Cost is controlled by only regenerating cards for new or changed notes.

### Folder Archetype Generation
Folder archetypes are generated by:
1. Walking the vault directory tree.
2. For each folder with 2+ child notes, infer:
   - Semantic role from child note titles and types
   - Naming convention from child filenames
   - Common vocabulary from child note concepts
   - Example children (top 3-5 by representativeness)
3. Confidence score based on child count and naming consistency.

This is purely deterministic (no LLM needed). Pattern matching and string analysis.

---

## 12. Context System

The model should have vault-wide awareness without receiving full vault content. This is achieved through a multi-resolution context system.

### Level 1: Global Context (always provided)
- All folder archetypes (compressed folder index)
- Summary statistics (total notes, notes per root folder, recent growth)

### Level 2: Note Cards (provided for relevant notes)
- Retrieved via candidate matching (exact, fuzzy, keyword, graph neighborhood)
- Typically 5-20 note cards per distillation run

### Level 3: Representative Snippets (provided when needed)
- Pulled from note cards for top candidates when the model needs more detail
- 2-5 short excerpts per note

### Level 4: Full Note Content (escalation only)
- Read the actual `.md` file content for 1-3 notes maximum
- Only when the model signals low confidence about append vs. create decisions

### Retrieval Strategy
For each atomic item extracted from captures:

1. **Exact match**: title or alias matches a note card title/alias exactly
2. **Fuzzy match**: RapidFuzz ratio >= 80 against note card titles/aliases
3. **Keyword overlap**: atom concepts intersect with note card concepts/qualities
4. **Graph expansion**: if a fuzzy match is found, also retrieve its direct neighbors (outbound + backlinks)
5. **Folder prior**: atom type maps to expected root folder, narrow search to that subtree

This ensures the model sees relevant context without blowing the context window.

---

## 13. Safety Rules and Edge Cases

### File Safety
- No machine files, temp files, or cache files may ever exist inside the vault.
- Render temp files in `STATE_DIR/tmp/`, validate, then atomically move into vault.
- If a write fails mid-operation, the temp file is cleaned up and the vault remains untouched.

### Idempotency
- Distillation markers (`CaptureEvent.status`) are the only source of truth for "already processed."
- Never infer processing status from note content.
- Mark events as distilled ONLY after successful git push.
- `/distill` processes all pending events across all days, not just today.

### Duplicate Prevention
- Globally unique basenames enforced before any note creation.
- Check against: existing vault files + notes planned in the same batch.
- RapidFuzz similarity check (threshold >= 90) against existing basenames to catch near-duplicates.

### Tombstone Protection
- Deleted notes are recorded as tombstones with TTL (default 30 days).
- Nightly organizer checks tombstones before creating any new note.
- If a proposed note basename matches a tombstone within TTL, reject the creation.

### Naming Consistency
- Before creating a note in an existing subfolder, inspect sibling note filenames.
- Infer the local naming convention (snake_case, kebab-case, Title-Case, etc.).
- New note filename must follow the same convention.
- If the folder is empty or has no clear convention, default to `lowercase_snake_case`.

### Structure Placement
- New notes must land inside a fixed root folder (never at vault root).
- Prefer the most specific existing subfolder whose children share the same semantic class.
- If no good subfolder exists, place directly under the correct root.
- Never create new subfolders during nightly distillation (defer to weekly reorg).

### Link Integrity
- Never create links to notes that don't exist and aren't being created in the same batch.
- After note deletion, dangling links may exist in other notes until weekly reorg cleanup.
- Weekly reorg includes a broken-link sweep.

### Partial Failure
- If distillation crashes after writing some notes but before pushing:
  - Notes are on disk but not committed. Next run will see them.
  - Capture events remain `pending`. Next run will reprocess.
  - The reprocessing must detect already-written notes (by basename) and skip re-creation.
- If push fails: events stay `pending`, changes stay committed locally, retry next cycle.

### Capture Edge Cases
- Empty messages: ignored.
- Image-only messages: ignored (text-only for V1).
- Extremely long messages: accept and capture, but truncate for LLM context if needed.
- Rapid burst messages: each is its own capture event. Nightly distillation may cluster nearby events by topic/time during atomization.

### Concurrency
- Global operation lock prevents race conditions between capture, distill, and delete.
- Only one vault mutation can happen at a time.
- Lock must be released in a `finally` block to prevent deadlocks on crash.

### Weekly Reorg Fallout
- After weekly reorg, the system must detect that paths have changed.
- Note card `fingerprint` field enables matching moved notes to their old identity.
- Old aliases are preserved so stale references still resolve.
- Full cache rebuild is mandatory after every weekly reorg.

---

## 14. Weekly Opus Reorg

### Purpose
The nightly organizer creates notes within existing structure but never creates new subfolders. Over time, root-level notes accumulate and the taxonomy may need restructuring. Weekly reorg is a human-initiated pass using a stronger model (Claude Opus) to reorganize the vault's folder structure.

### Frequency
Weekly, or whenever the user feels the vault has grown enough to need reorganization.

### Process
1. User generates the current vault state (vault tree + folder archetypes + note cards + recent growth).
2. User pastes the reorg prompt (below) into Claude Opus with the vault state.
3. Opus outputs a structured JSON plan.
4. User reviews the plan.
5. User runs the plan (a script can be built later to automate application).
6. After application: full rebuild of all derived schemas.
7. Git commit + push.

### Reorg Prompt Template

Copy-paste this to Claude Opus, filling in the bracketed sections:

```
You are a knowledge vault architect reviewing an Obsidian vault for structural reorganization.

## Hard Rules
- Root folders are IMMUTABLE. You must NOT create, delete, or rename any of these:
  01-Daily/, 03-People/, 04-Projects/, 05-Topics/, 06-School/, 99-Archive/, LifeOutside/, LifeInside/, _Templates/
- You must NOT rewrite note content. Only move/rename files and update wikilinks.
- You must NOT delete any notes.
- Globally unique basenames must be preserved. If you rename a file, no other file may share the new name.
- Filename convention: lowercase_snake_case.md
- Minimize disruption. Only reorganize when there is a clear structural benefit.
- Prefer consolidation over proliferation. Do not create a subfolder unless it will contain 3+ notes.

## Current Vault Tree
```
[PASTE VAULT TREE HERE]
```

## Current Folder Archetypes
```json
[PASTE FOLDER ARCHETYPES JSON HERE]
```

## Current Note Cards (titles and paths only for brevity)
```json
[PASTE NOTE TITLES AND PATHS HERE]
```

## Notes Created Since Last Reorg
```json
[PASTE RECENT NOTE PATHS AND TITLES HERE]
```

## Your Task
Analyze the vault structure and propose reorganization. Focus on:
1. Notes sitting directly under root folders that should be grouped into subfolders.
2. Subfolders that have grown enough to warrant further subdivision.
3. Notes that are clearly misplaced and should be moved.
4. Naming inconsistencies that should be corrected.
5. Any empty or redundant subfolders that should be removed.

## Output Format
Return ONLY valid JSON, no explanation, no markdown fences:

{
  "moves": [
    {"from": "old/path/note.md", "to": "new/path/note.md", "reason": "brief reason"}
  ],
  "new_folders": [
    {"path": "05-Topics/NewCategory", "reason": "brief reason"}
  ],
  "removed_folders": [
    {"path": "05-Topics/EmptyOld", "reason": "brief reason"}
  ],
  "link_rewrites": [
    {"file": "path/to/referencing_note.md", "old_link": "[[Old Title]]", "new_link": "[[New Title]]"}
  ],
  "alias_additions": [
    {"file": "new/path/renamed_note.md", "alias": "Old Title"}
  ],
  "reasoning": "2-3 sentence summary of what was reorganized and why."
}
```

### After Applying Reorg
1. Apply all file moves/renames.
2. Apply all link rewrites.
3. Add aliases to frontmatter for renamed notes.
4. Remove empty folders.
5. `git add -A && git commit -m "reorg: <reasoning summary>" && git push`.
6. Full rebuild of: folder archetypes, note cards, graph index.
7. Verify no broken links remain.

---

## 15. Tech Stack

### Core

| Component         | Tool                  | Purpose                                              |
|-------------------|-----------------------|------------------------------------------------------|
| Language          | Python 3.10+          | Best fit for Pi, filesystem, markdown, git            |
| Bot framework     | discord.py            | Stable, slash commands, autocomplete, DM handling     |
| Schemas           | Pydantic              | Strict validation for all data structures             |
| State storage     | SQLite                | Capture events, markers, tombstones, note cards cache |
| Fuzzy matching    | RapidFuzz             | Title/alias/path matching, naming convention inference|
| LLM               | anthropic SDK         | Distillation planning, note synthesis, atomization    |
| Markdown parsing  | python-frontmatter    | Reliable YAML frontmatter + content extraction        |
| Git               | subprocess (git CLI)  | Pull, commit, push — same as current implementation   |

### Optional (V1 can defer)

| Component          | Tool       | Purpose                                    |
|--------------------|------------|--------------------------------------------|
| Graph analysis     | NetworkX   | Orphan detection, component analysis       |
| Local embeddings   | sentence-transformers | Semantic similarity without API cost |

### What is NOT in the stack

| Excluded            | Reason                                               |
|---------------------|------------------------------------------------------|
| Redis / Postgres    | Overkill for single-user Pi deployment               |
| Docker              | Unnecessary complexity on Pi                         |
| n8n                 | Core logic belongs in Python, not a visual workflow   |
| Qdrant / pgvector   | SQLite is sufficient for V1 scale                    |
| LangChain           | Direct Anthropic SDK calls are simpler and cheaper   |
| GitHub Models API   | Replaced by Anthropic for better structured output   |

### Anthropic Model Strategy

Budget-conscious usage. The LLM is called only during distillation, never during capture.

| Stage              | Recommended Model      | Why                                              |
|--------------------|------------------------|--------------------------------------------------|
| Atomization        | claude-sonnet-4-20250514      | Good structured output, moderate cost            |
| Action planning    | claude-sonnet-4-20250514      | Nuanced categorization, candidate comparison     |
| Note synthesis     | claude-sonnet-4-20250514      | Following strict templates reliably              |
| Note card generation | claude-sonnet-4-20250514    | Summary + qualities + snippets extraction        |
| Weekly reorg       | claude-opus-4-20250514 (manual) | Deeper reasoning, broader context, user-triggered|

All model names should be configurable via environment variable.

---

## 16. Environment Variables

```env
# Required
DISCORD_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
VAULT_PATH=/path/to/obsidian/vault

# Optional (with defaults)
STATE_DIR=~/.local/share/persona-bot    # machine state directory
TIMEZONE=America/Toronto                 # timezone for timestamps
DISTILL_HOUR=23                          # nightly distillation hour (24h)
DISTILL_MINUTE=59                        # nightly distillation minute
ANTHROPIC_MODEL=claude-sonnet-4-20250514        # model for distillation stages
LOG_LEVEL=INFO                           # logging verbosity
```

---

## 17. Deployment

### Prerequisites
- Python 3.10+
- Git configured with SSH or credential helper for the vault repo
- Discord bot token with Message Content Intent enabled
- Anthropic API key

### Install

```bash
git clone https://github.com/AcastaPaloma/persona-bot.git
cd persona-bot
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# Edit .env with your values
```

### Run

```bash
python main.py
```

### Raspberry Pi — systemd Service

Create `/etc/systemd/system/persona-bot.service`:

```ini
[Unit]
Description=Persona Bot
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/persona-bot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable persona-bot
sudo systemctl start persona-bot
sudo journalctl -u persona-bot -f  # view logs
```

### First Run Checklist
1. Ensure `VAULT_PATH` points to a valid git repo with an upstream remote.
2. Ensure the vault contains the expected root folders.
3. Run `python main.py` and verify the bot comes online in Discord.
4. Send a test DM — it should append to the capture file and push.
5. Run `/distill` manually to verify the full pipeline works end to end.
6. Check `STATE_DIR` for the SQLite database and cache files.

---

## 18. Project File Structure

```
persona-bot/
  main.py                   # entry point, logging setup
  requirements.txt          # dependencies
  .env                      # secrets (not committed)
  .env.example              # template
  .gitignore
  README.md                 # this file
  logs/
    agent.log               # rotating operational logs
  app/
    __init__.py
    config.py               # env var loading and validation
    bot.py                  # discord bot, slash commands, DM handling
    capture.py              # capture event ingestion (no LLM)
    distill.py              # nightly distillation pipeline
    planner.py              # action planning (atomize, retrieve, decide)
    writer.py               # note rendering using strict templates
    vault.py                # vault file operations (read, write, scan)
    git_ops.py              # git pull/commit/push with locking
    state.py                # SQLite state management (events, tombstones)
    cache.py                # note card + folder archetype generation/refresh
    schemas.py              # all Pydantic models
    retriever.py            # candidate note retrieval (fuzzy, keyword, graph)
    templates.py            # strict note templates per category
  prompts/
    atomize.md              # system prompt for capture atomization
    plan_actions.md         # system prompt for action planning
    synthesize_note.md      # system prompt for note content generation
    generate_note_card.md   # system prompt for note card extraction
    weekly_reorg.md         # opus reorg prompt template
```

---

## Design Rationale Summary

These decisions emerged from extensive discussion. Reference the chat history for full context.

| Decision | Rationale |
|----------|-----------|
| Capture-first, organize-nightly | Organizing on every message is too slow, too expensive, and produces worse decisions with less context. |
| No hashtags or ghost references | They create noise and fake structure. Latent traits live in note cards, not visible notes. |
| Strict templates | Constraining output format makes the model more reliable and the vault more consistent. |
| External machine state | Prevents accidental indexing, Obsidian interference, and LLM learning from its own metadata. |
| Folder archetypes | The model must understand folder conventions, not just note content. This is the key to correct placement. |
| Note card qualities field | Summaries are too lossy for cross-domain connections. Qualities like "precision" bridge cooking to BJJ. |
| Representative snippets | Preserve specific detail that summaries compress away. |
| Globally unique basenames | Prevents ambiguous wikilinks across the vault. |
| Weekly reorg by stronger model | Folder creation is a higher-level structural decision that benefits from more context and human review. |
| Same-batch linking via temp IDs | Notes created together must be able to reference each other, resolved in a two-phase plan. |
| Tombstones for deleted notes | Prevents the organizer from recreating deleted notes from stale capture context. |
| Immediate push on capture | Raw input is the most valuable thing to preserve. Push immediately to prevent loss. |
| Mark distilled only after push | Prevents double-distillation if push fails. |
| Cross-link old notes | New information should strengthen the entire graph, not just add isolated nodes. |
