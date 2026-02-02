---
name: claude-memory
description: Manage conversation memory database
allowed-tools: [Bash, Read]
argument-hint: "<subcommand: sync|search|stats|import>"
---

# Memory Management Command

You are handling the `/claude-memory` command for managing the conversation memory database.

## Available Subcommands

| Subcommand | Description |
|------------|-------------|
| `sync` | Force sync current session to database |
| `search <query>` | Quick FTS search, return snippets |
| `stats` | Show DB size, project/session/message counts |
| `import` | Re-import all conversations from ~/.claude/projects |

## Implementation

Based on the user's subcommand, execute the appropriate action:

### sync

Force sync the current session to the database immediately:

```bash
# Get session ID from environment and sync
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/sync_current.py" <<< '{"session_id": "'"$CLAUDE_SESSION_ID"'"}'
```

Then report the result to the user.

### search <query>

Search conversations using the FTS5 full-text search:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/past-conversations/scripts/search_conversations.py" --query "<query>" --max-results 5
```

Display the results in a readable format.

### stats

Show database statistics:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/import_conversations.py" --stats
```

Display the stats including database size, number of projects, sessions, and messages.

### import

Re-import all conversations:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/import_conversations.py"
```

Report the import results.

## User Arguments

The user provided: `$ARGUMENTS`

Parse the subcommand and any additional arguments, then execute the appropriate action. If no subcommand is provided or an unknown subcommand is given, show the available subcommands.
