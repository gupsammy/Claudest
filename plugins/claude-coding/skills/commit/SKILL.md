---
name: commit
description: >
  This skill should be used when the user says "commit my changes", "commit this",
  "create a commit", "git commit", "save my work", or mentions committing code.
argument-hint: "[push]"
allowed-tools:
  - Bash(git:*)
  - Bash(python3:*)
  - AskUserQuestion
---

# Commit

Analyze uncommitted changes and create well-organized commits using conventional commit format.

## Workflow

### 1. Discover Changes

Current repo state (injected at invocation — no tool calls needed):

- Status: !`git status --porcelain`
- Diff stats: !`git diff --stat`

Abort before staging if any apply:
- Not a git repository.
- No changes ("Nothing to commit").
- Mid-merge / rebase / cherry-pick / revert — `git add -A` would stage conflict markers as content. Check `$(git rev-parse --git-dir)` for `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, `rebase-merge/`, or `rebase-apply/`. If any present, report the in-progress operation and stop.
- Detached HEAD — `git symbolic-ref -q HEAD` is empty. Commit would land on an unreferenced commit and be lost on branch switch. Report and stop.

### 2. Stage Files

Run `git add -A` to stage all changes.

**Exclude generated or ephemeral files** that should never be version-controlled: `scratch.*`, `temp.*`, `debug.*`, `playground.*`, `*.log`, `dist/`, `build/`, `target/`, `node_modules/`, `__pycache__/`.

If such files detected:
1. Unstage with `git reset HEAD <file>`
2. Ask user if they want to add to .gitignore

Proceed when all intended files are staged and ephemeral files are excluded.

### 3. Analyze Commit Boundaries

For each changed file, write a one-line PURPOSE description (not file location).

**Group by PURPOSE, not directory:**
- Same goal = one commit
- Different goals = separate commits

Each commit should represent one logical change because atomic commits enable `git bisect` and `git revert` without side-effects.

**Signs of separate concerns:**
- "Added X" AND "Fixed Y" (feature + bugfix)
- Changes that could be reverted independently
- Different conventional-commit types on related work — a fix and its tests go in separate `fix:` and `test:` commits so the fix can be reverted without losing the tests

If multiple concerns: use `git reset HEAD` then `git add <specific-files>` for each group. Commit foundational changes first.

**Handle renames (R status):** When splitting, add BOTH old and new paths. Git detects renames by similarity scoring across the old/new pair — staging only the new path causes git to log a delete + add, losing rename history.

Proceed when every changed file is assigned to exactly one commit group.

### 4. Validate

Run the validation script after staging:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/commit/scripts/validate.py . --output json
```

Interpret the result:
- Exit 0 → validation passed; proceed to Step 5
- Exit 1 → validation failed; parse the `output` field, report the error to the user, and stop
- Exit 2 → validator skipped (no marker file, no staged files match the validator's extensions, or the tool isn't installed); proceed to Step 5. The `output` field names the reason.

### 5. Create Commit

Check recent commit style:
```bash
git log --no-merges --oneline -10
```

Use conventional commit format:
```
<type>(<scope>): <description>
```

**Types:** feat, fix, docs, refactor, test, chore, perf

- Lowercase subject, no period, imperative mood
- Max 72 chars for subject
- Omit Co-authored-by trailers and AI attribution — these pollute `git log` and break downstream tooling that greps commit metadata
- No emojis

**If `git commit` fails due to a pre-commit hook:** the commit did NOT land. Check `git status` — the hook may have auto-fixed files (e.g. `auto-version.py` syncing `plugin.json`) and left them modified. Re-stage (`git add -A`) and retry the same `git commit` command with the same message. Do NOT use `--amend` (amends the PREVIOUS commit, not the failed one). Do NOT use `--no-verify` (skips the hook entirely, defeating its guard).

Proceed when the commit message is drafted and matches the repo's existing style.

### 6. Push (only if requested)

If user mentions "push" or arguments contain "push", run `git push`. If push fails, report the error and stop — do not retry or force-push.

## Output

One line per commit (hash + message). If temporary files were excluded, list them as bullets below.

