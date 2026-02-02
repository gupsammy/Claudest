#!/bin/bash
# SessionStart hook - auto-setup memory database and settings if missing
# Runs async to not block session start

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="$HOME/.claude-memory/conversations.db"
SETTINGS_PATH="$HOME/.claude-memory/settings.local.md"

# Create directory if needed
mkdir -p "$HOME/.claude-memory"

# Check if DB exists, run initial import if not
if [ ! -f "$DB_PATH" ]; then
    nohup python3 "$SCRIPT_DIR/import_conversations.py" &>/dev/null &
    disown
fi

# Create default settings file if missing
if [ ! -f "$SETTINGS_PATH" ]; then
    cat > "$SETTINGS_PATH" << 'EOF'
---
db_path: ~/.claude-memory/conversations.db
auto_inject_context: true
max_context_sessions: 2
exclude_projects: []
context_truncation_limit: 2000
logging_enabled: false
sync_on_stop: true
---

# Claude Memory Settings

This file configures the claude-memory plugin behavior.

## Settings

- `db_path`: Path to the SQLite database
- `auto_inject_context`: Whether to inject previous session context on startup
- `max_context_sessions`: Maximum number of sessions to include in context
- `exclude_projects`: List of project names to exclude from import
- `context_truncation_limit`: Max characters per message in context
- `logging_enabled`: Enable logging to ~/.claude-memory/memory.log
- `sync_on_stop`: Whether to sync session on Stop hook
EOF
fi

# Return immediately (don't block Claude)
echo '{"continue": true}'
