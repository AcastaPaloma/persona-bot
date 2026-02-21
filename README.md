# Persona Agent

A zero-cost, headless **Discord → Obsidian vault** automation system. DM the bot, it extracts metadata, logs structured entries to your vault, and syncs via Git — fully automatically.

## Quick Start

### Prerequisites
- Python 3.10+
- Git configured with SSH/credential for your vault repo
- Discord bot token ([create one here](https://discord.com/developers/applications))
- GitHub PAT with `models:read` scope (Education plan for free LLM access)

### 1. Clone & Install

```bash
git clone https://github.com/AcastaPaloma/persona-bot.git
cd persona-bot
pip install -r requirements.txt
```

### 2. Configure

Copy `.env.example` or create `.env`:

```env
DISCORD_TOKEN=your_discord_bot_token
GITHUB_TOKEN=your_github_pat_with_models_read
VAULT_PATH=C:/Users/YourName/Obsidian Vault
TIMEZONE=America/Toronto
```

Optional settings:
```env
LLM_MODEL=openai/gpt-4o-mini          # default
LLM_ENDPOINT=https://models.github.ai/inference/chat/completions
DISTILL_HOUR=23                        # nightly summary hour (24h)
DISTILL_MINUTE=59                      # nightly summary minute
```

### 3. Discord Bot Setup

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Create application → Bot → copy token to `.env`
2. **Bot → Privileged Gateway Intents** → enable **Message Content Intent**
3. **OAuth2 → URL Generator** → select `bot` + `applications.commands`
4. Required permissions: Send Messages, Read Message History
5. Use the generated URL to invite the bot to your server

### 4. Run

```bash
python main.py
```

## Usage

### DM Mode (Primary)
Just DM the bot anything. It will automatically:
1. Extract mood, topics, projects, and a summary via LLM
2. Append a structured entry to `01-Daily/Capture-YYYY-MM-DD.md`
3. Git commit and push

### Slash Commands
| Command   | Description                                  |
|-----------|----------------------------------------------|
| `/log`    | Log a thought from any channel               |
| `/status` | Health check (vault, git, LLM connectivity)  |

### Nightly Distillation
Runs automatically at the configured time (default 23:59). Summarizes the day's captures into `01-Daily/Summary-YYYY-MM-DD.md`.

## Architecture

```
Discord DM/Command
  ↓
PersonaBot (discord.py)
  ↓
extract_metadata() → GitHub Models API (gpt-4o-mini)
  ↓
append_capture() → 01-Daily/Capture-YYYY-MM-DD.md
  ↓
sync_vault() → git pull --rebase → commit → push
```

## Vault Structure

```
Obsidian Vault/
├── 01-Daily/
│   ├── Capture-2026-02-21.md    ← daily entries
│   └── Summary-2026-02-21.md    ← nightly distillation
└── ...your other vault content
```

Each capture file has YAML frontmatter and entries with collapsible metadata blocks.

## Raspberry Pi Deployment

Create a systemd service for always-on operation:

```ini
# /etc/systemd/system/persona-agent.service
[Unit]
Description=Persona Agent
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/persona-bot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable persona-agent
sudo systemctl start persona-agent
sudo journalctl -u persona-agent -f  # view logs
```

## File Structure

```
persona-bot/
├── main.py            # Entry point, logging setup
├── requirements.txt
├── .env               # Secrets (not committed)
├── .gitignore
├── logs/
│   └── agent.log      # Rotating operational logs
└── app/
    ├── __init__.py
    ├── config.py       # Centralized settings
    ├── bot.py          # Discord bot + commands
    ├── llm.py          # GitHub Models API integration
    ├── vault.py        # Obsidian vault operations
    ├── git_ops.py      # Git pull/commit/push
    ├── schemas.py      # Pydantic models
    └── distill.py      # Nightly summarization
```
