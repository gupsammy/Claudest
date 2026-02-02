#!/bin/bash
# Stop hook - async incremental sync for current session
# Reads stdin, backgrounds the sync, returns immediately

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Read hook input from stdin
INPUT=$(cat)

# Background the sync
echo "$INPUT" | python3 "$SCRIPT_DIR/sync_current.py" &>/dev/null &

# Return immediately (don't block Claude)
echo '{"continue": true}'
