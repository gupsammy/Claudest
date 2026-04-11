---
name: clean-branches
description: >
  This skill should be used when the user says "clean up branches", "delete merged
  branches", "prune stale branches", "git branch cleanup", "remove old branches",
  or wants to tidy up or purge old branches.
argument-hint: "[branch-pattern]"
allowed-tools:
  - Bash(git:*)
  - Bash(bash:*)
  - AskUserQuestion
---

# Clean Git Branches

Safely remove merged and stale git branches with confirmation.

## Process

**0. Parse arguments**
If `$ARGUMENTS` provided, treat it as a glob pattern to filter branch candidates (e.g., `feature/*` shows only feature branches). Pass it to the candidate script in Step 2.

**1. Fetch latest state**
```bash
git fetch --all --prune
```
If fetch fails (no remotes configured), note remote data is unavailable and continue with local analysis only.

**2. Identify candidates**

Run the candidate detection script, passing the optional pattern filter:
```bash
bash ${CLAUDE_PLUGIN_ROOT}/skills/clean-branches/scripts/find-candidates.sh "$PATTERN"
```
The script outputs two labeled sections (`=== MERGED ===` and `=== STALE ===`), one branch per line. Branches checked out in a worktree are annotated: `branch-name [worktree:/path/to/wt]`. Parse each section into its own list, preserving the worktree annotation.

After parsing, apply these worktree rules before building the candidate lists:

- **Stale branch + active worktree** → move immediately to the **blocked** list, regardless of worktree state. A stale branch with a live worktree may still have in-progress work — never offer it for cleanup.
- **Merged branch + active worktree** → check whether the worktree is clean:
  ```bash
  git -C /path/to/wt status --porcelain
  ```
  If the command returns any output (uncommitted changes), move to the **blocked** list. If clean, keep in the merged candidate list with the worktree annotation.

**3. Present results**

Display branches in four groups:
- **Merged** (safe to delete) — branches fully merged into base; those with a clean worktree show "(+ worktree at /path)"
- **Stale** (no recent commits, no active worktree) — only branches without a worktree appear here
- **Protected** (never touch) — main, master, develop, release/*
- **Blocked** — branches skipped because they have an active worktree with uncommitted work, or are stale with any active worktree; list each with its worktree path so the user knows what to resolve manually

Do NOT say "will be removed" for worktrees — removal is gated on confirmation in Step 4. If both the merged and stale candidate lists are empty, report "No branches to clean" and stop.

**4. Confirm before deletion**

Use AskUserQuestion. For merged branches that carry a worktree annotation, the confirmation option must name both the branch and its worktree path — the user is authorizing removal of both in one selection.

Structure:
- Header: "Branch cleanup"
- For merged branches: one option per branch. If the branch has a worktree: label = "branch-name + worktree", description = "Removes branch and worktree at /path". If no worktree: label = branch name, description = "Removes local branch". Include a "Keep all merged branches" fallback. If there are multiple candidates with no worktrees, a "Delete all N" batch option is acceptable.
- For stale branches: use multiSelect:true. Each option: label = branch name, description = age. (No stale branch with a worktree will appear here — they were moved to blocked in Step 2.)
- Always include a "Skip — keep all" option

The user selecting a branch-with-worktree option is explicit authorization to remove both. Never remove a worktree that was not explicitly included in a confirmed selection.

**5. Execute deletion**

Delete only what the user confirmed. For each confirmed branch:

1. If the branch has a `[worktree:/path]` annotation, remove the worktree first:
   ```bash
   git worktree remove /path/to/wt
   ```

2. Then delete the branch:
   ```bash
   git branch -d <branch-name>
   ```
   Use `-d` (not `-D`) — git refuses to delete branches with unmerged commits.

3. If the user explicitly requests remote cleanup:
   ```bash
   git push origin --delete <branch-name>
   ```
   Remote deletion requires explicit user request — never delete remotes unless the user says so directly.

## Output

Summary of actions taken:
- Branches deleted (local)
- Branches deleted (remote, if requested)
- Branches kept
- Any errors encountered
