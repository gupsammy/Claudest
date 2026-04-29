# Tool Reference

Both scripts share a common flag taxonomy. Default scope: **current project, auto-detected from CWD**. Use `--project NAME[,NAME]` to override, or `--all-projects` to widen scope.

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
| `--format markdown\|json` | Output format (default: markdown)                                   |
| `--json`                  | Alias for `--format json`                                           |
| `--include-notifications` | Include task notification messages                                  |
| `--db PATH`               | Database path (default: `~/.claude-memory/conversations.db`)        |
| `--cwd PATH`              | Override CWD for project auto-detect                                |
| `--version`               | Print version                                                       |
| `-h, --help`              | Show help with examples                                             |

Use `--verbose` for lenses that need file/commit context (restore-context, review-process, run-retro).

## search_conversations.py

Search for sessions containing keywords using full-text search (FTS5/FTS4/LIKE cascade).

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
| `--format markdown\|json` | Output format (default: markdown)                                   |
| `--json`                  | Alias for `--format json`                                           |
| `--include-notifications` | Include task notification messages                                  |
| `--db PATH`               | Database path (default: `~/.claude-memory/conversations.db`)        |
| `--cwd PATH`              | Override CWD for project auto-detect                                |
| `--version`               | Print version                                                       |
| `-h, --help`              | Show help with examples                                             |

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

## Error contract

Errors and warnings are emitted to **stderr** (not stdout). In JSON mode, structured shape:

```json
{"error": "<snake_case_code>", "message": "...", "hint": "<exact command or null>"}
```

Common error codes:
- `db_not_found` — database file missing; hint suggests checking `~/.claude-memory/`
- `invalid_limit` — `--limit` outside [1, 50]
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
