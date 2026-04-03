# claude-memory ![v0.8.37](https://img.shields.io/badge/v0.8.37-blue?style=flat-square)

Searchable conversation memory for Claude Code. Auto-syncs sessions to a SQLite database with full-text search, injects previous session context on startup, and provides on-demand recall of past conversations.

## Why

Agents don't remember anything between sessions. Claude Code gives them core memory (CLAUDE.md), procedural memory (skills, tools), and archival memory (auto memory notes). But there was no recall memory: the ability to search and retrieve the actual conversations from previous sessions.

claude-memory fills that gap. It stores conversation history in SQLite with FTS5 full-text search, makes it available through automatic context injection and on-demand search, and provides a skill that distills learnings from past conversations into persistent knowledge. Zero external dependencies. Just Python's standard library and SQLite.

## Requirements

Python 3.7+ (stdlib only, no pip packages needed)

## Installation

```
/plugin marketplace add gupsammy/claudest
/plugin install claude-memory@claudest
```

Everything runs automatically from there. Sessions sync on stop, context injects on start, and search is always available when you need to look back.

## How It Works

### Automatic context injection

On every session start, a hook queries the database for recent sessions from the same project. It selects the most recent meaningful one (skipping noise like single-exchange sessions, collecting short ones but continuing to look for something substantial). The agent begins every conversation knowing what happened in the previous one: what files were modified, what was discussed, where things left off.

This is what makes the plan-in-one-session, implement-in-the-next workflow possible. Without context injection, clearing context means starting over. With it, the agent picks up where you left off automatically.

### On-demand search

The recall-conversations skill provides two tools: keyword search (FTS5 with BM25 ranking) and chronological session browsing. The agent invokes these during a session when it needs to look something up. It also includes a lens system for structured analysis: restore context, find knowledge gaps, run retrospectives, extract decisions.

The skill triggers naturally on phrases like "what did we discuss," "remember when we worked on," "continue where we left off," and "as I mentioned before."

The search works because the agent constructs the queries, not the user. When you ask about "the database migration," the agent extracts substantive keywords and sends them to FTS5. If the first results aren't good enough, it iterates: refining terms, trying different queries, narrowing by project. The agent compensates for the simplicity of the search engine. Conversation content helps too. People say the same thing multiple ways across a conversation ("the migration," "the schema change," "the ALTER TABLE"), giving keyword search plenty of entry points.

### Background session sync

On session stop, a sync hook fires asynchronously. It reads the session's JSONL file (where Claude Code stores raw conversation data), parses it into structured messages, detects conversation branches from rewinds using UUID parent chain analysis, and writes everything to the database. This never blocks Claude Code's shutdown.

### A route from recall to archival memory

The extract-learnings skill reads past conversations and identifies non-obvious insights worth preserving: debugging gotchas, architectural decisions, workflow patterns, behavioral corrections. It proposes placing each learning at the correct layer in the memory hierarchy (global CLAUDE.md, repo CLAUDE.md, MEMORY.md, or topic files) with diffs and rationale, then writes only after explicit approval.

This is the route from recall memory (raw conversations) into archival memory (curated, persistent knowledge). Learnings that would otherwise evaporate when context resets get distilled and placed where the agent will see them in future sessions.

## Architecture

### Storage

SQLite at `~/.claude-memory/conversations.db` with WAL mode and 5-second busy_timeout for concurrent access. The key design: messages are stored once per session (deduped by UUID), and conversation branches from rewinds are tracked via a many-to-many `branch_messages` table. Each branch's messages are concatenated into a single document and indexed for full-text search. This branch-level aggregation is what makes multi-word search reliable: instead of matching against individual message fragments, FTS5 matches against entire conversation threads.

### FTS cascade

Full-text search uses a platform-adaptive cascade: FTS5 with BM25 ranking on systems that support it, FTS4 with MATCH and snippet on older builds, LIKE fallback on systems without any FTS support. The cascade is detected at startup via `PRAGMA compile_options`.

### Schema (v3)

| Table | Purpose |
|-------|---------|
| projects | Project metadata derived from directory structure |
| sessions | One row per conversation (UUID, timestamps, git branch) |
| branches | Conversation branches from rewinds, with aggregated content for FTS |
| messages | User/assistant messages, deduped by UUID |
| branch_messages | Many-to-many mapping between branches and messages |
| messages_fts / branches_fts | Full-text search indexes (FTS5, FTS4, or absent) |
| import_log | Tracks which JSONL files have been imported |

### Hooks

| Event | Hook | Action |
|-------|------|--------|
| SessionStart | memory-setup.py | Creates `~/.claude-memory/` directory, triggers initial import if DB missing |
| SessionStart | memory-context.py | Injects previous session context (on startup and clear events) |
| Stop | memory-sync.py | Writes session data to a temp file and spawns `sync_current.py` asynchronously |

All hooks are Python for cross-platform compatibility (macOS, Linux, Windows).

### Skills

| Skill | Trigger phrases | What it does |
|-------|----------------|--------------|
| recall-conversations | "what did we discuss", "continue where we left off", "remember when", "search my conversations" | Keyword search (FTS5/BM25) and chronological browsing with structured analysis lenses |
| extract-learnings | "extract learnings", "save this for next time", "remember this pattern", "add this to memory" | Reads past conversations, identifies insights worth preserving, proposes placements in the memory hierarchy |

### Security

Input validation and hardening added in v0.7.0: TOCTOU race prevention in `memory-sync.py` using `tempfile.mkstemp()` with 0o600 permissions, path traversal prevention in `sync_current.py` via UUID format validation and `resolve().relative_to()` boundary checks, FTS injection prevention through `sanitize_fts_term()`, and `PRAGMA foreign_keys = ON` enforcement. Teammate coordination messages and prompt_suggestion subagent noise are filtered from context and search (v0.7.1). `sanitize_fts_term()` was moved to `memory_lib/content.py` as the single canonical definition in v0.8.1 and hardened in v0.8.2 to strip the `-` (NOT shorthand) and `^` (initial-token boost) FTS5 operators.

## Manual Usage

Inside a Claude Code session, use the `/manage-memory` command for database management tasks (sync, search, stats, import). Outside of sessions, the underlying scripts can be run directly:

```bash
# Import all conversations into the DB
python3 plugins/claude-memory/hooks/import_conversations.py

# Import with stats output
python3 plugins/claude-memory/hooks/import_conversations.py --stats

# Search conversations by keyword
python3 plugins/claude-memory/skills/recall-conversations/scripts/search_conversations.py --query "authentication OAuth"

# Browse recent sessions
python3 plugins/claude-memory/skills/recall-conversations/scripts/recent_chats.py --n 5
```

No `--force` flag for import. Delete `~/.claude-memory/conversations.db` and re-run to reimport from scratch.

## License

MIT
