# Persona Bot — User Guide

Operational handbook covering daily usage, weekly reorg, troubleshooting, and extensibility.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Daily Usage](#2-daily-usage)
3. [Slash Commands Reference](#3-slash-commands-reference)
4. [Weekly Opus Reorg — Step by Step](#4-weekly-opus-reorg--step-by-step)
5. [How Distillation Works](#5-how-distillation-works)
6. [Troubleshooting](#6-troubleshooting)
7. [Functionality Breakdown](#7-functionality-breakdown)
8. [Expandability Roadmap](#8-expandability-roadmap)

---

## 1. Quick Start

```bash
# 1. Clone and install
git clone https://github.com/AcastaPaloma/persona-bot.git
cd persona-bot
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env — fill in DISCORD_TOKEN, ANTHROPIC_API_KEY, VAULT_PATH

# 3. Run
python main.py
```

**First run**: The bot builds a cache of your vault (folder archetypes + note cards). This is fast and doesn't call the LLM. You'll see it in the logs.

**Verify**: Send a test DM to the bot. You should get a checkmark reaction back, and `01-Daily/Capture-YYYY-MM-DD.md` should appear in your vault.

---

## 2. Daily Usage

Your daily workflow is extremely simple:

1. **Dump text into Discord DMs.** Anything. Thoughts, links, observations, techniques, meeting notes, reflections. No formatting needed.
2. **That's it.** The bot captures immediately, commits, and pushes.
3. **At 23:59** (or your configured time), the nightly distillation runs automatically. It atomizes your captures, creates/updates notes, and links everything.
4. **Open Obsidian the next morning.** Check your graph view. New notes will be there, linked, organized.

### What happens when you send a DM

```
You → "Practiced rear naked choke from back control today. 
       Coach said my grip needs to be tighter on the neck."
        ↓
Bot → ✅  (instant, <2 seconds)
        ↓
Vault → 01-Daily/Capture-2026-03-06.md gets appended
        ↓
SQLite → capture event recorded (status: pending)
        ↓
Git → commit + push
```

No LLM is called. Zero API cost on capture.

---

## 3. Slash Commands Reference

### `/log <text>`
**Where**: Any Discord server channel where the bot is present.
**What**: Same as a DM capture, but from a guild channel.
**When to use**: When you want to capture a thought mid-conversation in a server.

### `/distill`
**Where**: DM or any channel.
**What**: Manually triggers distillation of ALL pending captures (across all days).
**When to use**: When you don't want to wait for the nightly run. Useful after a heavy brain-dump session.
**Duration**: 30 seconds to a few minutes depending on capture volume.
**Output**: Summary message with counts of notes created/appended/linked.

### `/delete <note>`
**Where**: DM or any channel.
**What**: Deletes a vault note with confirmation.
**How it works**:
1. Start typing — autocomplete shows matching notes.
2. Select a note.
3. Bot shows the note's title, path, summary, and link counts.
4. React ✅ to confirm or ❌ to cancel.
5. On confirm: file deleted, tombstone recorded (30-day protection), git pushed.

### `/status`
**Where**: DM or any channel.
**What**: Health check showing vault accessibility, pending count, last distill time, cache sizes, DB size.

---

## 4. Weekly Opus Reorg — Step by Step

The nightly distillation never creates new subfolders. Over time, notes pile up directly under root folders. The weekly reorg uses a stronger model (Claude Opus) to restructure.

### When to do it

- Weekly, or whenever you notice clutter (5+ notes sitting at root level in a category).
- There's no automated trigger — you decide when.

### Step-by-step

#### Step 1: Generate your vault state

Open a terminal in your `persona-bot` project directory and run:

```bash
python -c "
from dotenv import load_dotenv
load_dotenv()
from app import config
config.load()
from app.vault import scan_tree
from app.cache import get_folder_archetypes, get_note_cards, rebuild_folder_archetypes, rebuild_note_cards
from app.state import get_recent_created_notes
import json

# Refresh caches
rebuild_folder_archetypes()
rebuild_note_cards(use_llm=False)

# 1. Vault tree
print('=== VAULT TREE ===')
print(scan_tree())

# 2. Folder archetypes
print('\n=== FOLDER ARCHETYPES ===')
archs = get_folder_archetypes()
print(json.dumps([a.model_dump() for a in archs], indent=2))

# 3. Note cards (titles + paths only)
print('\n=== NOTE CARDS ===')
cards = get_note_cards()
summary = [{'title': c.title, 'path': c.current_path} for c in cards]
print(json.dumps(summary, indent=2))

# 4. Recent bot-created notes
print('\n=== RECENT NOTES ===')
recent = get_recent_created_notes(limit=100)
print(json.dumps(recent, indent=2))
"
```

This prints four blocks of data.

#### Step 2: Assemble the prompt

Open the file `prompts/weekly_reorg.md`. It's a template with `[PASTE ... HERE]` placeholders.

Copy the entire template, then replace:
- `[PASTE VAULT TREE HERE]` → paste the vault tree output
- `[PASTE FOLDER ARCHETYPES JSON HERE]` → paste the archetypes JSON
- `[PASTE NOTE TITLES AND PATHS HERE]` → paste the note cards JSON
- `[PASTE RECENT NOTE PATHS AND TITLES HERE]` → paste the recent notes JSON

#### Step 3: Send to Claude Opus

Go to [claude.ai](https://claude.ai) (or the API). Select Claude Opus as the model. Paste the assembled prompt. Send.

Opus will return a JSON plan like:

```json
{
  "moves": [
    {"from": "05-Topics/rear_naked_choke.md", "to": "05-Topics/BJJ/techniques/rear_naked_choke.md", "reason": "BJJ technique belongs with other techniques"}
  ],
  "new_folders": [
    {"path": "05-Topics/BJJ/techniques", "reason": "3+ BJJ technique notes warrant a subfolder"}
  ],
  "removed_folders": [],
  "link_rewrites": [],
  "alias_additions": [],
  "reasoning": "Grouped 4 BJJ technique notes into a new techniques subfolder."
}
```

#### Step 4: Review the plan

Read it carefully. Check that:
- No root folders are being created/deleted/renamed.
- Moves make sense to you.
- New subfolders will actually contain 3+ notes (not just 1).
- No note content is being rewritten.

#### Step 5: Apply the plan

For now, apply manually:
1. Move/rename files in your vault as specified.
2. If there are `link_rewrites`, update the wikilinks in those files.
3. If there are `alias_additions`, add the alias to the note's frontmatter `aliases: []` array.
4. Delete any empty folders listed in `removed_folders`.

Then in your vault directory:

```bash
cd /path/to/your/vault
git add -A
git commit -m "reorg: <paste the reasoning field>"
git push
```

#### Step 6: Rebuild caches

Back in the persona-bot directory:

```bash
python -c "
from dotenv import load_dotenv
load_dotenv()
from app import config
config.load()
from app.cache import rebuild_all
rebuild_all(use_llm=False)
print('Cache rebuild complete')
"
```

This regenerates all folder archetypes and note cards from the new vault structure.

#### Done

The bot now knows about the new folder structure. Future distillations will place notes in the new subfolders.

---

## 5. How Distillation Works

A quick mental model of the 9-phase pipeline:

```
Phase 0: Lock vault, git pull, load pending captures from SQLite
    ↓
Phase 1: ATOMIZE — LLM breaks raw text into atomic knowledge items
         (concepts, entities, facts, techniques, reflections, tasks)
    ↓
Phase 2: RETRIEVE — For each atom, find candidate existing notes
         (fuzzy title match, keyword overlap, graph neighbors)
    ↓
Phase 3: PLAN — LLM decides: create new note? append to existing? just link? daily-only?
         Also identifies cross-links between existing notes
    ↓
Phase 4: RESOLVE — Same-batch references (temp IDs → real titles)
    ↓
Phase 5: VALIDATE — Reject bad plans (duplicate basenames, tombstoned, root-level, new subfolders)
    ↓
Phase 6: WRITE — Create/append notes using strict templates, atomic writes
    ↓
Phase 7: CROSS-LINK — Add links between existing notes that the new info connects
    ↓
Phase 8: DAILY SUMMARY — Generate 01-Daily/Daily/YYYY-MM-DD.md
    ↓
Phase 9: git commit + push → ONLY THEN mark events as "distilled"
```

**Key safety**: If push fails, events stay "pending" and get retried next run. You never lose data.

---

## 6. Troubleshooting

### Bot won't start

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Missing required environment variable: DISCORD_TOKEN` | `.env` not configured | Copy `.env.example` to `.env`, fill in all required values |
| `VAULT_PATH does not exist` | Path is wrong or drive not mounted | Check `VAULT_PATH` in `.env`. On Pi, ensure the disk is mounted. |
| `VAULT_PATH is not a git repo` | Vault directory has no `.git` folder | Run `git init` in the vault, set up a remote |
| Import errors | Dependencies not installed | Run `pip install -r requirements.txt` |

### Capture not working

| Symptom | Cause | Fix |
|---------|-------|-----|
| No ✅ reaction after DM | Bot not receiving DMs | Enable "Message Content Intent" in Discord Developer Portal → Bot settings |
| ✅ appears but file not in vault | Git push failed | Check `logs/agent.log` for push errors. Verify SSH keys / credential helper. |
| ❌ error message | Vault lock contention or file write error | Check logs. Usually resolves on next attempt. |
| Duplicate captures | Message processed twice | The system uses `INSERT OR IGNORE` on Discord message IDs — duplicates are silently skipped |

### Distillation not working

| Symptom | Cause | Fix |
|---------|-------|-----|
| `/distill` says "No pending captures" | All events already distilled | Check `/status` to confirm. If captures are missing, check if they were recorded in SQLite. |
| Distillation runs but creates 0 notes | LLM decided everything was `daily_only` | Check `logs/agent.log` for the LLM's reasoning. Your captures might be too brief. |
| Distillation errors in log | Anthropic API error | Check your API key. Check your credit balance. Model name might be wrong. |
| Notes created in wrong folder | Folder archetypes stale or vault structure changed | Run cache rebuild (see Step 6 in reorg). |
| `REJECTED: basename already exists` | A note with that filename already exists | This is correct behavior — the system won't create duplicates. |
| `REJECTED: matches active tombstone` | You deleted this note recently | Wait 30 days, or clear the tombstone from SQLite if intentional. |

### Git problems

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Git pull failed` | Merge conflict or network issue | SSH into your Pi, `cd` to vault, run `git status` and resolve manually. |
| `All push retries exhausted` | Remote unreachable | Check internet. Bot will retry next cycle — local commits are safe. |
| Vault out of sync | Someone edited vault outside the bot | `git pull --rebase` in the vault directory manually. |

### Cache/state problems

| Symptom | Cause | Fix |
|---------|-------|-----|
| Note cards empty | Cache never built | Run `python -c "from dotenv import load_dotenv; load_dotenv(); from app import config; config.load(); from app.cache import rebuild_all; rebuild_all(use_llm=False)"` |
| Folder archetypes missing folders | New folders created after last rebuild | Same as above — rebuild caches. |
| SQLite errors | Corrupted DB | Delete `STATE_DIR/state.db` — it will be recreated. Pending capture events will be lost (but the capture files in the vault still exist). |

### Clearing tombstones manually

If you want to allow a previously deleted note to be recreated:

```bash
python -c "
from dotenv import load_dotenv
load_dotenv()
from app import config
config.load()
from app.state import _get_conn
conn = _get_conn()
# List all tombstones
for row in conn.execute('SELECT * FROM tombstones'):
    print(dict(row))
# Delete a specific one
# conn.execute('DELETE FROM tombstones WHERE title = ?', ('Note Title',))
# conn.commit()
"
```

### Viewing the state DB

```bash
python -c "
from dotenv import load_dotenv
load_dotenv()
from app import config
config.load()
from app.state import _get_conn
conn = _get_conn()
print('=== Pending captures ===')
for r in conn.execute('SELECT id, timestamp, author, substr(raw_text, 1, 50) FROM capture_events WHERE status=\"pending\"'):
    print(dict(r))
print('=== Recent distilled ===')
for r in conn.execute('SELECT id, timestamp, distilled_at FROM capture_events WHERE status=\"distilled\" ORDER BY distilled_at DESC LIMIT 5'):
    print(dict(r))
"
```

---

## 7. Functionality Breakdown

### Capture Pipeline (zero-cost path)

```
Discord message
  → Validate (non-empty, non-bot, text-only)
  → Acquire vault lock
  → git pull
  → Append to 01-Daily/Capture-YYYY-MM-DD.md
  → INSERT into SQLite capture_events (status=pending)
  → git commit + push
  → Release lock
  → Reply ✅
```

**Cost**: $0. No LLM call.
**Latency**: 1-3 seconds (mostly git).
**Failure mode**: If anything fails, the error is logged and the user gets an ❌ with the error type.

### Distillation Pipeline (LLM-powered)

Two LLM calls per run:
1. **Atomize**: Raw captures → structured atoms (1 API call)
2. **Plan**: Atoms + candidates + archetypes → action plan (1 API call)

Note content is written by the plan itself (the LLM generates it during planning). No third call for synthesis unless you extend the system.

**Cost**: ~$0.02-0.10 per distillation depending on capture volume.
**Latency**: 30-120 seconds depending on capture count.

### Delete Pipeline

```
/delete → autocomplete → user selects → confirmation message
  → User reacts ✅
  → Acquire lock → git pull → delete file → git commit + push
  → Record tombstone (30-day TTL)
  → Remove from note card cache
```

### Cache System

Two caches, both derived (rebuildable):

| Cache | Storage | Rebuild cost |
|-------|---------|-------------|
| **Note cards** | `STATE_DIR/cache/note_cards.json` | Fast without LLM, richer with LLM |
| **Folder archetypes** | `STATE_DIR/cache/folder_archetypes.json` | Instant (purely deterministic) |
| **Link graph** | `STATE_DIR/cache/graph.json` | Instant (derived from wikilinks) |

### Safety Layers

| Layer | What it prevents |
|-------|-----------------|
| Global asyncio lock | Concurrent vault mutations |
| Tombstones (30d TTL) | Re-creation of deleted notes |
| Unique basename check | Ambiguous wikilinks |
| Near-duplicate check (90% fuzzy) | Subtle duplicates like `side_control` vs `side_controll` |
| Root folder immutability | Bot can't restructure the taxonomy |
| No subfolder creation | Only weekly reorg (human-reviewed) can create folders |
| Atomic writes (temp → move) | Half-written files in the vault |
| Mark-after-push | Double-distillation if push fails |

---

## 8. Expandability Roadmap

The system is designed to be extended. Here's what can be added without architectural changes:

### Tier 1: Easy wins (hours of work)

| Feature | How |
|---------|-----|
| **Audio transcription** | Pre-process audio → text before calling `ingest_capture`. Add a `/voice` command or detect audio attachments in DMs. Use Whisper API or local whisper.cpp. |
| **PDF ingestion** | Extract text from PDFs, feed into capture. Libraries: `pymupdf` or `pdfplumber`. |
| **Image OCR** | Extract text from images via Tesseract or a vision model. |
| **Custom distill schedule** | Change `DISTILL_HOUR` / `DISTILL_MINUTE` in `.env`. Or add a cron expression env var. |
| **Multiple users** | Filter captures by `author` field. Currently all captures go to one vault — trivial to add author-based routing. |
| **Richer /status** | Add vault size, note count per folder, graph density metrics. |

### Tier 2: Moderate effort (days)

| Feature | How |
|---------|-----|
| **Automated weekly reorg** | Write a `reorg.py` that calls Opus API directly, applies the JSON plan, and rebuilds caches. Add a `/reorg` slash command. |
| **Semantic search (local)** | Add `sentence-transformers` for local embeddings. Store vectors in SQLite or a `.npy` file. Use cosine similarity in the retriever alongside fuzzy matching. Zero API cost. |
| **Note search via Discord** | `/search <query>` → return top 5 matching note cards with paths. Uses the retriever. |
| **Show related notes** | `/related <note>` → return the note's backlinks and outbound links. |
| **Conflict resolution** | Detect git merge conflicts and either auto-resolve (prefer remote) or alert the user via DM. |
| **Batch LLM enrichment** | A command to run LLM enrichment on all note cards that have empty summaries/qualities. Useful after first setup. |

### Tier 3: Major features (weeks)

| Feature | How |
|---------|-----|
| **Web dashboard** | Flask/FastAPI app showing vault stats, recent distillations, graph visualization. |
| **Obsidian plugin companion** | Plugin that syncs with the bot's state DB to show note cards, qualities, and suggested links inside Obsidian. |
| **Multi-vault** | Support multiple vaults per user with vault selection in Discord. |
| **Real-time distillation mode** | Option to distill after every N captures instead of nightly. Trade-off: more API cost, faster organization. |
| **Knowledge graph visualization** | Export the link graph to a web-based interactive graph (d3.js, Cytoscape). |
| **Smart daily briefing** | Bot sends you a DM each morning summarizing yesterday's distillation and suggesting connections you might want to explore. |

### How to add a new note type

1. Add the template function in `app/templates.py` (follow the existing pattern).
2. Add the type string to the `TEMPLATE_MAP` dict.
3. Update the `render_note` function in `app/writer.py` with the new type.
4. Update the atomize prompt (`prompts/atomize.md`) to recognize the new atom type.
5. Update the plan prompt (`prompts/plan_actions.md`) to know about the new note type.
6. Rebuild caches.

### How to add a new slash command

1. In `app/bot.py`, add a new function decorated with `@bot.tree.command(...)`.
2. Use `await interaction.response.defer(thinking=True)` for anything slow.
3. Use the vault lock if you're mutating the vault.
4. The command auto-syncs on bot startup via `setup_hook`.

### How to change the LLM model

- **For all stages**: Set `ANTHROPIC_MODEL` in `.env`.
- **Per-stage**: Modify `_call_anthropic()` in `app/planner.py` to accept a model parameter, or create separate functions for different stages.
- **Switch to OpenAI**: Replace the `anthropic` client calls with `openai` equivalents. The prompt files stay the same — they're model-agnostic.

---

## Quick Reference Card

| Action | How |
|--------|-----|
| Capture a thought | DM the bot or `/log <text>` |
| Trigger distillation now | `/distill` |
| Delete a note | `/delete` + autocomplete |
| Check bot health | `/status` |
| Weekly reorg | Generate state → paste prompt to Opus → review → apply → rebuild cache |
| View logs | `logs/agent.log` or `journalctl -u persona-bot -f` on Pi |
| Rebuild caches | `python -c "..."` (see Section 4, Step 6) |
| Clear a tombstone | See Troubleshooting section |
| View pending captures | See "Viewing the state DB" section |
