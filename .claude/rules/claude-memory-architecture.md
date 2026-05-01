# claude-memory Architecture

## Hook Lifecycle

On **SessionStart**, three hooks fire in order on `startup|clear`:
1. `memory-setup.py` (matcher: `*`) — creates `~/.claude-memory/`, kicks off background import if DB missing
2. `memory-context.py` — queries recent sessions, injects context via `hookSpecificOutput`
3. `consolidation-check.py` — checks if memory consolidation is needed

On **SessionEnd** (matcher: `clear`), `clear-handoff.py` writes `~/.claude-memory/clear-handoff.json` with the dying session's `session_id`, `cwd`, and `transcript_path` so the next SessionStart can hard-link to the cleared-from session.

On **Stop**, `memory-sync.py` writes hook input to a temp file and spawns `sync_current.py --input-file` in the background to incrementally sync the session without blocking shutdown. All hooks are Python (no bash) for cross-platform compatibility.

## Database

SQLite at `~/.claude-memory/conversations.db` with WAL mode and 5s busy_timeout. Messages are stored once per session (deduped); branches (from conversation rewinds) tracked via `branch_messages` join table. Full-text search cascade: FTS5 → FTS4 → LIKE fallback. Schema auto-migrated on connection — outdated schema triggers delete-and-recreate.

Shared utility package: `plugins/claude-memory/skills/recall-conversations/scripts/memory_lib/` (6 modules: `db.py`, `content.py`, `parsing.py`, `formatting.py`, `summarizer.py`, `__init__.py`). Hook scripts reach it via `sys.path.insert` at runtime.

Settings hardcoded in `memory_lib/db.py:DEFAULT_SETTINGS` — PyYAML removed intentionally (not stdlib). Do not introduce non-stdlib dependencies in plugin runtime code.

## Development Commands

```bash
# Incremental import (append-only — safe to run anytime):
python3 plugins/claude-memory/hooks/import_conversations.py

# Import with stats
python3 plugins/claude-memory/hooks/import_conversations.py --stats

# Testing: always duplicate first, never touch the live DB
cp ~/.claude-memory/conversations.db ~/.claude-memory/conversations-test.db

# Test context injection
echo '{"source":"startup","session_id":"test","cwd":"/some/path"}' | python3 plugins/claude-memory/hooks/memory-context.py

# Test session sync
echo '{"session_id":"<uuid>"}' | python3 plugins/claude-memory/hooks/sync_current.py
```
