#!/bin/bash
# SessionStart hook - auto-setup memory database if missing
# Runs async to not block session start

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="$HOME/.claude-memory/conversations.db"

# Check if DB exists
if [ ! -f "$DB_PATH" ]; then
    # Create directory and run initial import in background
    mkdir -p "$HOME/.claude-memory"
    python3 "$SCRIPT_DIR/import_conversations.py" &>/dev/null &
fi

# Return immediately (don't block Claude)
echo '{"continue": true}'
