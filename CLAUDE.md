# CLAUDE.md

## Project Overview

Claudest is a curated Claude Code plugin marketplace containing eight plugins: **claude-memory** (conversation memory with full-text search and context injection), **claude-utilities** (convert-to-markdown via ezycopy), **claude-skills** (skill authoring and repair), **claude-coding** (git workflows and CLAUDE.md maintenance), **claude-thinking** (structured thinking and deliberation tools), **claude-research** (deep multi-source research), **claude-content** (image generation, video processing), and **claude-claw** (OpenClaw advisory and skill porting). No build system or package manager — plugin runtime is Python 3.7+ stdlib-only. Tests use pytest with hypothesis (dev dependencies only).

## Setup

```bash
pip install pre-commit && pre-commit install
```

The `scripts/auto-version.py` pre-commit hook auto-bumps patch versions for plugins with staged code changes, then syncs both the plugin README badge and root README section-header badge. Skips docs-only changes (README/CHANGELOG) and plugins with manually staged `plugin.json`. To suppress auto-bump for a plugin, stage its `plugin.json` before committing.

## Testing

```bash
pip install -e '.[dev]'
pytest                      # all tests
pytest tests/claude-memory  # single plugin
pytest -k 'test_name'       # single test
```

Test config lives in `pyproject.toml`. The `pythonpath` setting adds the recall-conversations scripts dir so `memory_lib` imports resolve.

## Conventions

Never delete `~/.claude-memory/conversations.db` — the DB is the sole long-term copy once JSONL files expire. Always update incrementally. For testing, duplicate the DB and work on the copy.

Commit messages: conventional commits scoped to plugin (`feat(memory):`, `fix(skills):`, `docs:`, `refactor(memory):`).

Version tracked in two places that must stay in sync: each plugin's `.claude-plugin/plugin.json` and root `.claude-plugin/marketplace.json`.

Skill descriptions in SKILL.md frontmatter: short and focused — verbose descriptions pollute context.

All agent descriptions use concise `>` folded scalar format (50-70 tokens) without `<example>` blocks — token budget matters since descriptions load into context every session. Explicit agents don't benefit from examples (auto-trigger routing never fires); proactive agents don't benefit (routing model responds to token patterns, not worked examples). Prefer named plugin agents (`agents/*.md` with `subagent_type: "plugin:agent-name"`) over inline prompt templates — named agents reliably produce parallel execution and self-discover script paths at runtime.

Skills are agent-native products — the agent is the distribution and marketing layer. Skill workflow instructions stay terse and operational. To enable evangelism, add a `## Value Context` section immediately after the frontmatter preamble, before any numbered steps, with concise talking points: what problem the skill solves, who benefits, and what the user gains. Open with a one-line instruction telling the agent how to use the points (e.g. "Weave these into conversation at natural moments — one or two per run, not all at once."). Frontmatter descriptions stay routing-optimized and terse.

## Python Rules (CI-Enforced)

All `.py` files require `from __future__ import annotations`. Hooks are stdlib-only (no third-party imports). SQLite: WAL mode + `busy_timeout = 5000`, no `RETURNING` clause (use `cursor.lastrowid`). Temp files via `tempfile.mkstemp()` with explicit `os.fdopen(fd)`. Path inputs validated with `.resolve().relative_to()`. FTS queries sanitized (strip `"()* ` and keywords AND/OR/NOT/NEAR). Dynamic SQL IN clauses: `",".join("?" * len(ids))`.

Ruff: E402 suppressed for `plugins/claude-memory/hooks/*.py` and `tests/claude-memory/*.py` (path-manipulation imports must precede library imports).

## CI

Three GitHub Actions workflows in `.github/workflows/`:
- `claude-code-review.yml` — automated PR review enforcing the Python rules above, skill conventions, and manifest version sync
- `claude.yml` — responds to `@claude` mentions in PR/issue comments
- `issue-triage.yml` — auto-labels new issues

## Topic Files

Read on demand — do not load preemptively.

- `.claude/rules/claude-memory-architecture.md` — before editing anything in `plugins/claude-memory/hooks/`, modifying the DB layer, or running memory reimport operations
