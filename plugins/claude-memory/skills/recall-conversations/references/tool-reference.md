# Tool Reference

`recent_chats.py` and `search_conversations.py` share a common flag taxonomy. Default scope: **current project, auto-detected from CWD**. Use `--project NAME[,NAME]` to override, or `--all-projects` to widen scope.

## recent_chats.py

Retrieve recent conversation sessions with all messages.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recall-conversations/scripts/recent_chats.py --limit 5
```

| Option                    | Effect                                                              |
| ------------------------- | ------------------------------------------------------------------- |
| `--limit N`, `-n N`       | Number of sessions (1-50, default: 5)                               |
| `--project NAME`          | Filter by project name(s), comma-separated. Default: auto-detected. |
| `--all-projects`          | Widen scope to all projects (overrides auto-detect)                 |
| `--sort-order desc\|asc`  | Sort order (default: desc)                                          |
| `--before DATE`           | Sessions before this datetime (ISO)                                 |
| `--after DATE`            | Sessions after this datetime (ISO)                                  |
| `--verbose`, `-v`         | Include files_modified, commits, tool_counts                        |
| `--summary`               | Emit precomputed per-session digests instead of full content (~3× smaller, single-pass). Skips the message fetch. |
| `--timeline`              | Compact index, one line per session: date, exchanges/tool calls, branch, project, uuid, title. No message bodies; ignores `--limit`, `--verbose`, `--summary`. Uncapped, so a long-lived project returns hundreds of lines — window with `--after`. JSON shape: `{"timeline": [...], "count", "scope"}`. |
| `--session ID[,ID]`       | Fetch sessions by uuid or 8+ char hex prefix (markdown output prints 8). Ignores project scope, `--before`/`--after`, and `--limit`; composes with `--verbose`, `--summary`, `--json`. A prefix matching nothing prints a warning to stderr. Cannot combine with `--timeline`. |
| `--format markdown\|json` | Output format (default: markdown)                                   |
| `--json`                  | Alias for `--format json`                                           |
| `--include-notifications` | Include turns the harness wrote: subagent results, compaction summaries, CI and inter-session messages |
| `--db PATH`               | Database path (default: `~/.claude-memory/conversations.db`)        |
| `--cwd PATH`              | Override CWD for project auto-detect                                |
| `--version`               | Print version                                                       |
| `-h, --help`              | Show help with examples                                             |

`--project NAME` matches by name, and one name can belong to several project paths; all of them are merged. Resolve names with `list_projects.py` first.

Use `--verbose` for lenses that need file/commit context (restore-context, review-process, run-retro).

Use `--summary` for broad/multi-session lenses (run-retro, find-gaps, find-antipatterns, extract-decisions) or `--all-projects` scope to keep context small. When retrieved content exceeds the volume budget, markdown mode prints an `INFO: …` signal to stderr and JSON mode adds three keys to `meta`: `content_chars`, `summary_suggested` (large full-content pull — switch to `--summary`), and `fanout_suggested` (large even after `--summary` — escalate to per-project subagent fan-out).

`--summary` composes with `--verbose`: you still get the files-modified / commits / tool-counts metadata header, only the conversation body is replaced by the precomputed summary. In JSON, summary mode reports `total_summaries` instead of `total_messages`.

## search_conversations.py

Search for sessions containing keywords using full-text search (FTS5/FTS4/LIKE cascade).

Matching: the query is split on whitespace and FTS special characters are removed from each term. FTS5 OR-joins the terms and ranks by BM25; FTS4 OR-joins and orders by recency; the LIKE fallback requires every term and orders by recency. Each hit returns the whole session by default, its summary with `--summary`, or its matching passages with `--excerpts`.

With `semantic_search` on in `config.json`, the keyword ranking is fused (reciprocal rank) with a ranking by embedding similarity of each exchange, so paraphrases match. A session found only by meaning has no keyword passage; under `--excerpts` it shows the message that matched. Ollama down → keyword results plus a stderr warning.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recall-conversations/scripts/search_conversations.py --query "keyword"
```

| Option                    | Effect                                                              |
| ------------------------- | ------------------------------------------------------------------- |
| `--query TERMS`, `-q`     | Required — substantive keywords                                     |
| `--limit N`, `-n N`       | Number of sessions (1-50, default: 5)                               |
| `--project NAME`          | Filter by project name(s), comma-separated. Default: auto-detected. |
| `--all-projects`          | Widen scope to all projects (overrides auto-detect)                 |
| `--verbose`, `-v`         | Include files_modified, commits                                     |
| `--summary`               | Emit precomputed per-session digests instead of full content (~3× smaller, single-pass). Skips the message fetch. |
| `--excerpts`              | Emit up to 3 matching passages per session (~48-token windows, hits in `**bold**`, conversation order) instead of full content. Same index and stemming as the ranking. Cannot combine with `--summary`. JSON: each session carries `excerpts: [{role, timestamp, text}]`; the envelope reports `total_excerpts`. |
| `--lexical`               | Keyword ranking only, even with semantic search on                  |
| `--format markdown\|json` | Output format (default: markdown)                                   |
| `--json`                  | Alias for `--format json`                                           |
| `--include-notifications` | Include turns the harness wrote: subagent results, compaction summaries, CI and inter-session messages |
| `--db PATH`               | Database path (default: `~/.claude-memory/conversations.db`)        |
| `--cwd PATH`              | Override CWD for project auto-detect                                |
| `--version`               | Print version                                                       |
| `-h, --help`              | Show help with examples                                             |

## list_projects.py

Map project names to paths. Names are not unique: one basename can belong to many paths.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recall-conversations/scripts/list_projects.py --match NAME
```

| Option                    | Effect                                                              |
| ------------------------- | ------------------------------------------------------------------- |
| `--match TOKEN`           | Substring filter over project name or path (case-insensitive)       |
| `--format markdown\|json` | Output format (default: markdown)                                   |
| `--json`                  | Alias for `--format json`                                           |
| `--db PATH`               | Database path (default: `~/.claude-memory/conversations.db`)        |

Each row: name, path, key (transcript-directory name), session count, first and last seen. JSON shape: `{"projects": [...], "count", "match"}`. Ordered most recently active first.

## Output contract

### Markdown (default)

Token-efficient session digests:

```
## myproject | 2026-02-01 10:00
Session: abc123

### Conversation

**User:** ...
**Assistant:** ...
```

### JSON (--json or --format json)

Single envelope object:

```json
{
  "sessions": [...],
  "total_sessions": N,
  "total_messages": M,
  "scope": {"projects": ["claudest"], "auto_detected": true},
  "has_more": false,
  "query": "..."   // search_conversations only
}
```

- `scope.auto_detected: true` means the project was inferred from CWD; `false` means it was explicitly passed (or scope is unfiltered).
- `has_more: true` indicates the result set hit the limit; more sessions may exist.
- `retrieval` (search only, present when semantic search is on): `hybrid` when vectors ranked the results, `lexical` with `--lexical`, an empty index, or Ollama down.

## Error contract

Errors and warnings are emitted to **stderr** (not stdout). In JSON mode, structured shape:

```json
{"error": "<snake_case_code>", "message": "...", "hint": "<exact command or null>"}
```

Common error codes:
- `db_not_found` — database file missing; hint suggests checking `~/.claude-memory/`
- `invalid_limit` — `--limit` outside [1, 50]
- `invalid_session` — a `--session` entry is not 8+ hex/dash characters
- `query_failed` — runtime error during DB query

In markdown mode, errors are plain text: `Error: <message>` followed by `Hint: <command>`.

## Exit codes

| Code | Meaning                                       |
| ---- | --------------------------------------------- |
| `0`  | Success (including zero results)              |
| `1`  | Runtime error (DB unreadable, FTS error)      |
| `2`  | Invalid arguments (mutex conflict, bad limit) |

## Project auto-detection

When `--project` and `--all-projects` are both omitted, scope is resolved by:

1. Walk up from CWD (`os.getcwd()` or `--cwd PATH`) looking for a path match in `projects.path`
2. If no path match, try `basename(CWD)` against `projects.name`
3. If both miss, emit a `WARN` to stderr and proceed with no project filter

The `--cwd PATH` flag is useful for testing and for agents running in non-standard working directories.
