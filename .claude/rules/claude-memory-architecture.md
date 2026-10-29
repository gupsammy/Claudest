---
paths:
  - "plugins/claude-memory/**"
---
# claude-memory Architecture

## Hook Lifecycle

On **SessionStart**, four hooks fire in `hooks.json` order:
1. `memory-setup.py` (matcher `*`, so also resume/compact) — creates `~/.claude-memory/`; spawns background import if the DB is missing, plus `backfill_summaries.py` (pending migrations, summaries) and `embed_index.py` (embedding backlog)
2. `onboarding.py` (`startup`)
3. `memory-context.py` (`startup|clear`) — queries recent sessions, injects context via `hookSpecificOutput`
4. `consolidation-check.py` (`startup|clear`) — checks if memory consolidation is needed

`onboarding.py` runs the full walkthrough until `onboarding_completed`. After a `CURRENT_ONBOARDING_VERSION` bump, earlier users get only the questions in `NEW_QUESTION_KEYS` whose config key is absent; every answer, including no, is written by `write_config.py`, so nothing is asked twice. Add each new question's key there.

On **SessionEnd** (matcher: `clear`), `clear-handoff.py` writes `~/.claude-memory/clear-handoff.json` with the dying session's `session_id`, `cwd`, and `timestamp`; the next SessionStart links to it when the normalized cwd matches and the file is under 30 s old.

On **Stop**, `memory-sync.py` writes hook input to a temp file and spawns `sync_current.py --input-file` in the background to incrementally sync the session without blocking shutdown; when new messages land and `semantic_search` is on, `sync_current.py` also calls `embed_index.run` in the same background process. Under Codex (`CODEX_THREAD_ID` or a `/.codex/sessions/` path) `memory-sync.py` prints bare `{}`. Every hook runs as `bash run.sh <script>.py`; hook logic stays Python for cross-platform compatibility.

## Database

SQLite at `~/.claude-memory/conversations.db` with WAL mode and 5s busy_timeout. Messages are stored once per session (deduped); branches (from conversation rewinds) tracked via `branch_messages` join table. Every recall path reads only the active branch, so `find_all_branches` must keep each typed turn on it: it follows a `compact_boundary`'s `logicalParentUuid` across compactions, treats only a typed user turn (not a tool result from parallel tool calls) as rewind evidence, and each sync prunes branch rows the fresh parse no longer yields (`prune_stale_branches`). A slash command is stored as `/name args`. Full-text search cascade: FTS5 → FTS4 → LIKE fallback. Opt-in vectors: `exchange_embeddings` per typed turn; `session_embeddings` holds each session's mean, stale when its `n` differs from the session's vector count, refreshed by `embed_index.py` and read by `memory-context.py` for related sessions. Every connection runs `migrate_db()`; it rebuilds only pre-v3 DBs (`sessions` without `branches`), after a `pre-v3-nuke` backup, and refuses if that backup fails. All later changes are additive DDL plus `user_version`-gated data migrations (below).

Shared utility package: `plugins/claude-memory/skills/recall-conversations/scripts/memory_lib/` (`db.py`, `content.py`, `parsing.py`, `formatting.py`, `summarizer.py`, `embeddings.py`, `cli_common.py`). Hook scripts reach it via `sys.path.insert` at runtime.

Settings defaults in `memory_lib/db.py:DEFAULT_SETTINGS` (overridden by `~/.claude-memory/config.json`, written by `write_config.py`) — PyYAML removed intentionally (not stdlib). Do not introduce non-stdlib dependencies in plugin runtime code.

## Migrations and Locks

Schema DDL (column adds, triggers) runs on every `get_db_connection`. Version-gated data migrations (`_data_migrations`, `PRAGMA user_version`) run only with `migrate=True`, under `migrate.lock` beside the DB, re-reading the version once locked so concurrent hooks migrate and back up once. Foreground hooks (`memory-setup`, `memory-context`) pass `migrate=False`; `memory-setup` hands a pending migration to `backfill_summaries.py`. Fresh DBs are stamped `LATEST_DATA_VERSION`; bump it with every new migration or new DBs re-run it.

All locks (`import.lock`, `migrate.lock`, `backfill.lock`, `embed.lock`) use `acquire_lock`/`release_lock` in `db.py`: an OS lock (`flock`/`msvcrt`) the kernel drops when the holder dies. Never reintroduce PID files — read-then-steal races let two processes both hold the lock. Lock files stay on disk after release.

Migration backups go next to the DB as `conversations.pre-<label>-<ts>.db` via a `.partial` name renamed only after `quick_check`, in rollback-journal mode (a WAL-mode copy cannot be opened read-only).

Import is append-only, so a normalization fix reaches only sessions imported after it; existing rows need a fold in `_migrate_project_paths`. A migration that moves or deletes rows must sit behind a `user_version` bump stamped only after `_backup_db_before_migration` succeeds, roll back fully on any failure (a later hook commit would persist a half-applied fold), and break ties with an explicit `ORDER BY` (unordered SQLite ties flip per connection).

Detached hooks (`memory-setup`, `memory-sync`) send stderr to DEVNULL, so a crashing background import fails silently. The symptom is work repeating every SessionStart (backups, full imports) because the sentinel never advances; rerun the script in the foreground against a DB copy. Trust a JSONL `cwd` as a project path only if it encodes back to the project's directory key — sessions can start elsewhere and `/cd` later.

## Semantic Search

Opt-in (`semantic_search`, off by default; `hooks/semantic_setup.py --check/--enable`, offered during onboarding). Local Ollama embeds each typed turn at 256 dims (`embeddings.py`). Recall fuses FTS and vector ranks with equal-weight RRF and drops matches under `semantic_min_score` (0.70); `--lexical` forces FTS only. Related sessions in `memory-context.py` use session means with floor `RELATED_MIN_SCORE` (0.55). New embeddings arrive via the Stop sync; `memory-setup` only drains backlog under `embed.lock`.

## Token Insights

`skills/get-token-insights/scripts/ingest_token_data.py` writes `token_snapshots` into the same `conversations.db`; wrap stored timestamps in `datetime()` for every SQL comparison (ISO `T` vs space separator). `install_cache_hooks.py` copies the cache hooks (`cache-warn-stop.py` on Stop, `cache-expiry-warn.py` on UserPromptSubmit, 1 h TTL) into `~/.claude/hooks/` and `~/.claude/settings.json`, removing retired hooks; edit the plugin assets, then reinstall.

## Codex Desktop Integration

Codex sessions land in the same `conversations.db` via a minimal adapter. `parse_codex_session` normalizes Codex JSONL events into Claude-shaped message dicts, then routes through the shared `sync_entries()`. Origin column is set to `codex`; deterministic synthetic UUIDs (`uuid5(NAMESPACE_URL, "codex:<sid>:<kind>:<ord>")`) keep re-imports idempotent.

The adapter is intentionally minimal. It imports user turns from `event_msg.user_message`, or from `event_msg.item_completed` `UserMessage` items in files without that event (newer Codex builds), and `response_item.message phase=final_answer`. Legacy rows keep their kind-plus-ordinal UUIDs (ordinal counted over legacy rows only); item rows key on the item id, so reading items never forks stored rows. Sessions whose cwd contains `DAYFLOW_CWD_MARKER` (Dayflow's automated `codex exec` runs) are logged in `import_log` and never imported; every parsed Codex file is logged with `mtime_ns`, so an unchanged file is skipped before hashing. Commentary, tool calls, function-call results, and reasoning content are skipped. **Recall-quality consequence**: a Codex session where the meaningful work happened across `phase=commentary` updates and tool calls will surface in recall as just the question and final answer — thinner than a Claude session with comparable activity. Don't blame the FTS ranker for missing intermediate context; the omission is by design.

`SessionStart` triggers a bulk Codex import via `import_conversations.py --include-codex --backup-on-import` when (a) the DB is missing, (b) any `import_log.file_hash` is NULL, or (c) any transcript under `~/.codex/sessions` is newer than `~/.claude-memory/.last-codex-import`. The bulk path is gated by `~/.claude-memory/import.lock` so racing SessionStart hooks don't double-import. Backups in `~/.claude-memory/backups/` are full page copies, so `backup_database` takes at most one per 24 h (`BACKUP_MIN_INTERVAL_HOURS`) and keeps the last 3 (`BACKUP_RETENTION`). Off-site copies are outside the plugin (a `VACUUM INTO` snapshot compresses ~7x with zstd).

Codex sessions without `session_meta.cwd` route to a single sentinel project at `/(unknown-codex)` rather than fabricating a project per Codex date directory.

Folderless Claude chats (`<account>/scratch-workspaces/<id>/<id>/scratch-<date>`) fold into one sentinel project `/(scratch)` via `normalize_cwd` and `normalize_project_key`; `_migrate_project_paths` merges older per-chat rows.

Worktree sessions fold into their main project by design — they are about the same project and must recall like main-checkout sessions. `resolve_project_cwd` maps Claude `/.claude/worktrees/` by path and Codex `~/.codex/worktrees/<id>/<repo>` to the single non-worktree project with that name (none or several → stays its own project). `sessions.cwd` keeps the origin. Re-key folded rows (`_rekeyed_worktree_key`) or the next import recreates the duplicate project.

## Development Commands

```bash
# Incremental import (append-only — safe to run anytime):
python3 plugins/claude-memory/hooks/import_conversations.py

# Import with stats
python3 plugins/claude-memory/hooks/import_conversations.py --stats

# Testing: always duplicate first, never touch the live DB.
# Hooks other than import_conversations.py have no --db; the two commands below write to the live DB.
cp ~/.claude-memory/conversations.db ~/.claude-memory/conversations-test.db

# Test context injection
echo '{"source":"startup","session_id":"test","cwd":"/some/path"}' | python3 plugins/claude-memory/hooks/memory-context.py

# Test session sync
echo '{"session_id":"<uuid>"}' | python3 plugins/claude-memory/hooks/sync_current.py
```
