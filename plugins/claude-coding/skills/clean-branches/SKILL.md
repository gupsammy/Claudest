---
name: clean-branches
description: >
  This skill should be used when the user says "clean up branches", "delete merged
  branches", or "prune stale branches". Use whenever the user mentions branch cleanup,
  pruning, or stale branch deletion — even if they don't say "clean-branches" explicitly.
argument-hint: "[branch-pattern]"
allowed-tools:
  - Bash
  - AskUserQuestion
---

# Clean Git Branches

Safely remove merged and stale git branches with confirmation.

## Process

If `$ARGUMENTS` is provided, treat it as a glob pattern to filter branch candidates (e.g., `feature/*`) and pass it to the candidate script in Step 1.

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
The script outputs two labeled sections (`=== MERGED ===` and `=== STALE ===`), one branch per line. Branches carry two optional annotations:
- `[worktree:/path/to/wt]` — branch is checked out in a worktree
- `[squash-merged]` — branch was merged via squash or rebase PR; git does not recognize it as merged locally (requires force-delete in Step 5)

Parse each section into its own list, preserving both annotations.

After parsing, apply these worktree rules before building the candidate lists. Process the MERGED list first, then the STALE list — a branch that appears in both (merged AND older than 30 days) is governed by the MERGED rule only; skip it when processing STALE.

- **Stale branch + active worktree** → move immediately to the **blocked** list, regardless of worktree state. A stale branch with a live worktree may still have in-progress work — never offer it for cleanup. Skip branches already classified as merged.
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
- For merged branches: one option per branch. If the branch has a worktree: label = "branch-name + worktree", description = "Removes branch and worktree at /path". If the branch is squash/rebase-merged: append "(squash/rebase PR — force delete)" to the description so the user knows `-D` will be used. If no worktree and not squash-merged: label = branch name, description = "Removes local branch". Include a "Keep all merged branches" fallback. If there are multiple candidates with no worktrees, a "Delete all N" batch option is acceptable.
- For stale branches: use multiSelect:true. Each option: label = branch name, description = age. (No stale branch with a worktree will appear here — they were moved to blocked in Step 2.)
- Always include a "Skip — keep all" option

The user selecting a branch-with-worktree option is explicit authorization to remove both. Never remove a worktree that was not explicitly included in a confirmed selection.

**5. Execute deletion**

Delete only what the user confirmed. For each confirmed branch:

1. If the branch has a `[worktree:/path]` annotation, remove the worktree first:
   ```bash
   git worktree remove /path/to/wt
   ```
   If the command fails (the worktree acquired changes in the window between Step 2 and now), report the error and skip that branch — do not force-remove.

2. Then delete the branch:
   - Regular merged (no `[squash-merged]` annotation): `git branch -d <branch-name>`
   - Squash/rebase-merged (`[squash-merged]` annotation): `git branch -D <branch-name>`

   `-D` is required for squash/rebase-merged branches because git does not recognise their commits as merged into base — using `-d` will fail. The `[squash-merged]` annotation on a confirmed selection is explicit user authorisation to force-delete.

3. After local deletions are complete, offer remote cleanup:

   Use AskUserQuestion with multiSelect:true listing every branch that was successfully deleted locally and has a known remote (`git ls-remote --heads origin <branch-name>` to verify). Let the user select which remotes to also delete. If none have a remote, skip this step.

   For each selected remote:
   ```bash
   git push origin --delete <branch-name>
   ```

## Output

Summary of actions taken:
- Branches deleted (local)
- Branches deleted (remote, if requested)
- Branches kept
- Any errors encountered
