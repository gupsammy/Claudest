---
name: get-token-insights
description: >
  This skill should be used when the user says "get token insights", "show my
  token usage", "token analysis", "usage insights", "how am I using tokens",
  "analyze my claude usage", or "show cache efficiency". Not for general
  context-reduction advice or API cost questions.
allowed-tools:
  - Bash(python3:*)
---

# Get Token Insights

Ingest Claude Code session data into the token_snapshots table and surface usage patterns.

## Process

1. Run the ingest script:

```bash
python3 $CLAUDE_PLUGIN_ROOT/scripts/ingest_token_data.py
```

The script reads `~/.claude/usage-data/session-meta/` and `~/.claude/usage-data/facets/`,
upserts all sessions into `~/.claude-memory/conversations.db` (token_snapshots table),
and prints a JSON blob to stdout.

2. Parse the JSON output and present a human-readable summary covering:
   - **Totals**: input, output, cache_read, cache_creation tokens; cache_ratio
   - **Top tools**: ranked by call count across all sessions
   - **Idle gaps**: sessions where user_response_times > 300s (cache likely expired)
   - **Outcomes**: distribution of session outcome types (mostly_achieved, etc.)
   - **Per-session highlights**: flag sessions with 0 cache_read tokens or high tool_errors

3. Highlight actionable findings — e.g. sessions with no cache hits suggest cache expiry
   between turns; high tool_error counts in a session suggest fragile bash sequences worth
   reviewing.

## Dashboard

A pre-built HTML dashboard is available at `$CLAUDE_PLUGIN_ROOT/templates/dashboard.html`.
Copy it to any local path and open in a browser — it expects the JSON blob from step 1
to be embedded or fetched. To open immediately:

```bash
open $CLAUDE_PLUGIN_ROOT/templates/dashboard.html
```
