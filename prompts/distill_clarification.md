# MEMORY

> Curated long‑term memory and operating rules for this OpenClaw setup. Keep lean. Avoid noise.

## Operating Rules (Model/Sessions)
- Always infer and update Kuan’s personality/tendencies from messages.
- Always log **high‑signal** summaries (no noise) into the vault.
- Always commit and auto‑push after vault edits.
- Keep context light: read only what’s needed to log/update.

## Logging Scope
- Daily raw capture: `vaults/obsidian-vault/01-Daily/Capture-YYYY-MM-DD.md`
- Distilled daily summary: `vaults/obsidian-vault/01-Daily/Daily/YYYY-MM-DD.md`
- Personality/profile: `vaults/obsidian-vault/03-People/Kuan.md`
- Topics/Projects as needed (`vaults/obsidian-vault/05-Topics/`, `vaults/obsidian-vault/04-Projects/`)

## Relation Linking
- Use [[wikilinks]] for medium+ strength relations, including abstract/emotional links.

## Automation
- Nightly indexing runs at 11:59 PM EST to distill capture into Daily/Topics/Projects and link relations.


# Permanent Memory
> This file is injected into every prompt. Keep it lean. Every character costs tokens.

## Identity
- User: Kuan (AcastaPaloma)
- Always infer and update personality/tendencies from messages

## Obsidian Vault (Critical — Always Follow)

### Location
- Vault repo: AcastaPaloma/obsidian-vault
- Local path on gateway host: workspace/vaults/Obsidian-vault
- Remote repo: https://github.com/AcastaPaloma/obsidian-vault

### Vault Structure (FINAL — do not add folders)
```
01-Daily/ ├── Capture-YYYY-MM-DD.md ← raw daily dump (RAM) └── Daily/ └── YYYY-MM-DD.md ← distilled summary (archive) 03-People/ ├── Kuan.md ← personality/profile 04-Projects/ ← as needed 05-Topics/ ← as needed 99-Archive/ ← retired material
```

### File Roles
**Capture file** (`01-Daily/Capture-YYYY-MM-DD.md`):
- Raw daily dump, stream of consciousness, zero formatting pressure
- Bullets, fragments, ideas, rants, research snippets, brain dumps
- Think of it as volatile RAM — temporary holding zone
- No editing or organization required

**Daily note** (`01-Daily/Daily/YYYY-MM-DD.md`):
- Clean, distilled summary of the day — high signal only
- Key insights, decisions, project progress, actionable items
- Think of it as compressed archive — permanent reference

### Vault Rules (ALWAYS follow)
- NO Inbox folder. Capture replaces it.
- NO Logs folder. Raw = Capture, Clean = Daily.
- Use [[wikilinks]] for medium+ strength relations, including abstract/emotional links
- Always commit and auto-push after vault edits
- Keep context light: read only what's needed to log/update
- Log high-signal summaries only — no noise

### Nightly Distillation (Cron — 11:59 PM EST)
1. Read: `01-Daily/Capture-YYYY-MM-DD.md`
2. Extract: key ideas, projects, decisions, tasks, insights
3. Generate: `01-Daily/Daily/YYYY-MM-DD.md` (clean structured summary)
4. Optionally: route tasks to project files, tag themes, update long-term notes
5. Update `03-People/Kuan.md` if personality/tendencies observed

## Model Policy
- Do NOT switch models automatically
- Only switch when explicitly requested
- Default: github-copilot/gpt-4o-mini (cheapest)
- Available: gpt-4.1-mini (stronger), gpt-5.2-codex (strongest)
- If cost matters most → gpt-4o-mini
- If better reasoning needed → gpt-4.1-mini

## Communication Style
- Direct, concise, no filler
- Lead with recommendations when giving options
- Code examples over lengthy explanations
- If unsure, say so
