# claude-memory

Searchable conversation memory for Claude Code. Auto-syncs your sessions to a SQLite database with FTS5 full-text search.

## Features

- **Auto-sync**: Sessions automatically sync to database on Stop hook
- **Auto-setup**: Database created on first session if missing
- **Full-text search**: FTS5 with Porter stemming and BM25 ranking
- **Lens system**: Structured analysis workflows (restore-context, extract-learnings, find-gaps, etc.)

## Installation

Add the claudest marketplace to Claude Code:

```bash
claude /plugin add-marketplace ~/repos/myrepos/claudest
claude /plugin install claude-memory@claudest
```

## Database Location

`~/.claude-memory/conversations.db`

## Usage

The skill triggers automatically on phrases like:
- "what did we discuss"
- "remember when we worked on..."
- "continue where we left off"
- "as I mentioned before"

### Manual Tool Usage

```bash
# Recent sessions
python3 ~/.../skills/past-conversations/scripts/recent_chats.py --n 5

# Search
python3 ~/.../skills/past-conversations/scripts/search_conversations.py --query "authentication OAuth"
```

## Hooks

| Event | Action |
|-------|--------|
| SessionStart | Creates database if missing, runs initial import |
| Stop | Incrementally syncs current session |

Both hooks run asynchronously to avoid blocking Claude.

## Schema

- **projects**: Project metadata (path, name)
- **sessions**: Conversation sessions (uuid, timestamps, git branch)
- **messages**: User/assistant messages with FTS5 indexing
- **import_log**: Tracks imported files for incremental sync

## License

MIT
