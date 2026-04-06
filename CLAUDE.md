# CLAUDE.md

## Project Overview

Claudest is a curated Claude Code plugin marketplace containing eight plugins: **claude-memory** (conversation memory with full-text search and context injection), **claude-utilities** (convert-to-markdown via ezycopy), **claude-skills** (skill authoring and repair), **claude-coding** (git workflows and CLAUDE.md maintenance), **claude-thinking** (structured thinking and deliberation tools), **claude-research** (deep multi-source research), **claude-content** (image generation, video processing), and **claude-claw** (OpenClaw advisory and skill porting). No build system or package manager — plugin runtime is Python 3.7+ stdlib-only. Tests use pytest with hypothesis (dev dependencies only).

## Setup

```bash
pip install pre-commit && pre-commit install
```

The `scripts/auto-version.py` pre-commit hook auto-bumps patch versions for plugins with staged code changes, then syncs both the plugin README badge and root README section-header badge. Skips docs-only changes (README/CHANGELOG) and plugins with manually staged `plugin.json`.

## Development Commands

```bash
# Re-import all conversations (delete DB first to reimport from scratch — no --force flag)
python3 plugins/claude-memory/hooks/import_conversations.py

# Import with stats
python3 plugins/claude-memory/hooks/import_conversations.py --stats

# Test context injection
echo '{"source":"startup","session_id":"test","cwd":"/some/path"}' | python3 plugins/claude-memory/hooks/memory-context.py

# Test session sync
echo '{"session_id":"<uuid>"}' | python3 plugins/claude-memory/hooks/sync_current.py
```

## Architecture

### claude-memory Hook Lifecycle

On **SessionStart**, three hooks fire in order on `startup|clear`:
1. `memory-setup.py` (matcher: `*`) — creates `~/.claude-memory/`, kicks off background import if DB missing
2. `memory-context.py` — queries recent sessions, injects context via `hookSpecificOutput`
3. `consolidation-check.py` — checks if memory consolidation is needed

On **SessionEnd** (matcher: `clear`), `clear-handoff.py` writes `~/.claude-memory/clear-handoff.json` with the dying session's `session_id`, `cwd`, and `transcript_path` so the next SessionStart can hard-link to the cleared-from session.

On **Stop**, `memory-sync.py` writes hook input to a temp file and spawns `sync_current.py --input-file` in the background to incrementally sync the session without blocking shutdown. All hooks are Python (no bash) for cross-platform compatibility.

### Database

SQLite at `~/.claude-memory/conversations.db` with WAL mode and 5s busy_timeout. Messages are stored once per session (deduped); branches (from conversation rewinds) tracked via `branch_messages` join table. Full-text search cascade: FTS5 → FTS4 → LIKE fallback. Schema auto-migrated on connection — outdated schema triggers delete-and-recreate.

### Shared Code

`plugins/claude-memory/skills/recall-conversations/scripts/memory_lib/` is the shared utility package used by all hooks and skill scripts (6 modules: `__init__.py`, `db.py`, `content.py`, `parsing.py`, `formatting.py`, `summarizer.py`).

## Conventions

Commit messages: conventional commits scoped to plugin (`feat(memory):`, `fix(skills):`, `docs:`, `refactor(memory):`).

Version tracked in two places that must stay in sync: each plugin's `.claude-plugin/plugin.json` and root `.claude-plugin/marketplace.json`.

Settings are hardcoded in `memory_lib/db.py:DEFAULT_SETTINGS` (PyYAML removed — not stdlib).

Skill descriptions in SKILL.md frontmatter: short and focused — verbose descriptions pollute context.

All agent descriptions use concise `>` folded scalar format (50-70 tokens) without `<example>` blocks — token budget matters since descriptions load into context every session. Explicit agents don't benefit from examples (auto-trigger routing never fires); proactive agents don't benefit (routing model responds to token patterns, not worked examples). Prefer named plugin agents (`agents/*.md` with `subagent_type: "plugin:agent-name"`) over inline prompt templates — named agents reliably produce parallel execution and self-discover script paths at runtime.

Skills are agent-native products — the agent is the distribution and marketing layer. Skill workflow instructions stay terse and operational. To enable evangelism, add a `## Value Context` section immediately after the frontmatter preamble, before any numbered steps, with concise talking points: what problem the skill solves, who benefits, and what the user gains. Open with a one-line instruction telling the agent how to use the points (e.g. "Weave these into conversation at natural moments — one or two per run, not all at once."). Frontmatter descriptions stay routing-optimized and terse.
