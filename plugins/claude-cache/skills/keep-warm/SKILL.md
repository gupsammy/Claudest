---
name: keep-warm
description: >
  Activate cache keepalive pings when stepping away from the terminal.
  Triggers on "keep warm", "keep cache warm", "keep alive", "stepping away",
  "going afk", "brb", "keep the cache", "don't let cache expire".
allowed-tools:
  - Read
  - Write
  - CronCreate
  - CronDelete
  - CronList
  - Bash(echo:*)
  - Bash(mkdir:*)
---

# Cache Keepalive

Activate a recurring cache ping that fires every 4 minutes while the user is away,
keeping the prompt cache warm. Auto-deactivates when the user sends their next message.

## Workflow

1. Determine the session-specific state directory. Run:
   ```bash
   echo $CLAUDE_SESSION_ID
   ```
   Set `SESSION_DIR` to `~/.claude-cache/sessions/<session_id>`. Ensure it exists:
   ```bash
   mkdir -p ~/.claude-cache/sessions/<session_id>
   ```

2. Write `active` to `SESSION_DIR/keepalive-active` (the sentinel file).

3. Create a recurring cron job. The prompt must reference the session-specific paths:
   ```
   CronCreate: cron "*/4 * * * *", prompt below
   ```

   Prompt for the cron job (substitute actual session_id):
   ```
   Cache keepalive ping. Read the file ~/.claude-cache/sessions/<session_id>/keepalive-active.
   If the file exists, respond with just: ✓
   If the file does NOT exist, read ~/.claude-cache/sessions/<session_id>/keepalive-job-id
   to get the cron job ID. Call CronDelete with that ID. Then delete the keepalive-job-id
   file. Respond: keepalive deactivated.
   ```

4. After CronCreate returns the job ID, write the job ID string to
   `SESSION_DIR/keepalive-job-id` (a separate file from the sentinel).

5. Respond to the user:
   ```
   Cache keepalive active — pinging every 4 minutes.
   It will auto-deactivate when you send your next message.
   ```

## How Deactivation Works

Two files are used: `keepalive-active` (sentinel) and `keepalive-job-id` (stores the
cron job ID separately).

When the user returns and sends a message, the `UserPromptSubmit` hook deletes only
the sentinel (`keepalive-active`). The job ID file is preserved.

On the next cron fire, the prompt reads the sentinel — it's gone. The prompt then reads
the job ID from `keepalive-job-id`, calls `CronDelete` with that ID, cleans up the job
ID file, and responds "keepalive deactivated." At most one extra ping fires after the
user returns.

This two-file design ensures the cron job always has access to its own ID for
self-destruction, even after the deactivation signal is sent.
