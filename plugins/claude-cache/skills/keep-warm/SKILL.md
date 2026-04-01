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
  - Bash(echo:*)
---

# Cache Keepalive

Activate a recurring cache ping that fires every 4 minutes while the user is away,
keeping the prompt cache warm. Auto-deactivates when the user sends their next message.

## Workflow

1. Determine the session-specific keepalive file path. The current session ID is
   available in the environment or hook context. Construct the path:
   `~/.claude-cache/sessions/<session_id>/keepalive-active`

   To get the session ID, run:
   ```bash
   echo $CLAUDE_SESSION_ID
   ```
   If the env var is empty, check the most recent session directory under
   `~/.claude-cache/sessions/` as a fallback.

   Ensure the session directory exists:
   ```bash
   mkdir -p ~/.claude-cache/sessions/<session_id>
   ```

2. Write the text `pending` to the keepalive file.

3. Create a recurring cron job:
   ```
   CronCreate: cron "*/4 * * * *", prompt below
   ```

   Prompt for the cron job (substitute the actual keepalive file path):
   ```
   Cache keepalive ping. Read the file ~/.claude-cache/sessions/<session_id>/keepalive-active.
   If the file exists and contains a cron job ID, respond with just: ✓
   If the file does NOT exist, call CronDelete with the job ID that was
   stored in the file, then respond with: keepalive deactivated.
   ```

4. After CronCreate returns the job ID, overwrite the keepalive file with the job ID
   string (so the cron prompt can read it for self-destruction).

5. Respond to the user:
   ```
   Cache keepalive active — pinging every 4 minutes.
   It will auto-deactivate when you send your next message.
   ```

## How Deactivation Works

When the user returns and sends a message, the `UserPromptSubmit` hook deletes
the session-specific `keepalive-active` file. On the next cron fire, the prompt
reads the file, finds it missing, and calls `CronDelete` to remove itself. At most
one extra ping fires after the user returns.
