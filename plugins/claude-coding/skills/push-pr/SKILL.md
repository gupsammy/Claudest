---
name: push-pr
description: This skill should be used when the user wants to create a pull request, or submit code for review. Triggers on "push a PR", "create a PR", "open a pull request", "make a PR", "submit for review".
argument-hint: "[status: 1=open|2=draft|3=ready] [base-branch]"
allowed-tools:
  - Bash(git:*)
  - Bash(gh:*)
  - Bash(python3:*)
  - Skill
  - AskUserQuestion
---
# Push & PR

Push commits and create/update pull requests with automatic branch management and
scope-aware multi-PR splitting.

## Arguments

Parse flexibly from `$ARGUMENTS`:
- **status**: `1`=opened, `2`=draft, `3`=ready (default: new PR=opened, update=draft)
- **base-branch**: Target branch (default: `main`)

## Pre-Flight Context

Injected at invocation — analyze before taking any action:

- Working tree status: `!git status --porcelain`
- Current branch: `!git rev-parse --abbrev-ref HEAD`
- Unpushed commits: `!git rev-list @{u}..HEAD --count 2>/dev/null || echo "no upstream"`
- Recent commits: `!git log origin/main..HEAD --oneline 2>/dev/null` (assumes main base; see Step 3 if base differs)
- Diff stat: `!git diff origin/main...HEAD --stat 2>/dev/null` (captured before fetch; re-run if stale)

## Workflow

### 1. Pre-Flight

Run `git fetch origin` to sync remote state.

If the working tree status above shows uncommitted changes, invoke `Skill: commit` to
commit first.

Complete when: remote is fetched and working tree is clean.

### 2. Branch Management

If on main/master with unpushed commits, cut a feature branch before proceeding.

Branch naming: prefix from the primary commit's conventional type (`feat/`, `fix/`,
`docs/`, `chore/`, `refactor/`); slug from the commit scope or subject, lowercase hyphens
only, max 45 chars total (keeps branch names readable in GitHub's UI and avoids truncation
in terminal prompts). Use the scope if present (`feat(auth)` → `feat/auth`); otherwise
condense the subject to 2–4 words (`fix login redirect timeout` → `fix/login-redirect`).

```bash
git checkout -b <derived-branch-name>
git branch -f main origin/main
```

`git branch -f` moves main's pointer back to origin/main without checkout or `--hard` —
non-destructive and never triggers permission denials.

If already on a feature branch: skip to step 3.

Complete when: HEAD is on a feature branch (not main/master).

### 3. Context Gathering

Derive working variables from pre-flight context and arguments:
- `BASE` = base-branch argument, or `main` if not provided
- `BRANCH` = current branch name (from pre-flight injection)

Use the pre-flight context injected above. If the base branch differs from `main`,
re-gather against `origin/$BASE`:

```bash
git log origin/$BASE..HEAD --oneline --reverse
git diff origin/$BASE...HEAD --stat
```

Always compare against `origin/$BASE`, not local `$BASE` — the PR targets the remote
branch, so comparisons must match what GitHub will see.

Record: commit count, conventional-commit types and scopes present, total diff lines
(approximate from `--stat` output).

Complete when: commit count, scope/type inventory, and approximate diff size are known.

### 4. Scope Analysis

Evaluate whether the changeset warrants multiple PRs. A split is warranted when either:
- **Size**: total diff exceeds ~400 lines (code lines; ignore lock files and generated
  files) — beyond this threshold, reviewer fatigue degrades review quality and catch rate
- **Diversity**: commits span 3+ distinct conventional-commit scopes or types (e.g.,
  `feat(auth)`, `fix(ui)`, `chore(deps)`) — multiple scopes mean the changeset lacks a
  single narrative, making review harder and revert riskier

If neither condition is met: proceed to step 5 as a single PR.

If either condition is met — propose stacked PRs:

Cluster commits by scope/type in the order they were made. Each cluster becomes one PR
targeting the previous cluster's branch (the first targets `$BASE`). Present the plan:

```
Proposed stacked PRs (each PR targets the previous branch):
  PR 1  [base: main]         feat/auth          — commits: abc1234, def5678
  PR 2  [base: feat/auth]    fix/ui-redirect    — commits: ghi9012
  PR 3  [base: fix/ui-...]   chore/cleanup      — commits: jkl3456
```

Use AskUserQuestion: "Split into N stacked PRs as shown above, or push as a single PR?"

If user declines split: proceed to step 5 as a single PR.

If user confirms split — stacked PR execution:

For each cluster in order:
1. Create a branch from the previous cluster's branch (`$BASE` for cluster 1):
   ```bash
   git checkout -b <cluster-branch> <previous-branch>
   git cherry-pick <sha1> <sha2> ...
   ```
2. Push: `git push -u origin <cluster-branch>`
3. Generate PR body using the format script with `--base <previous-branch>`:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/push-pr/scripts/format-pr-body.py --base "<previous-branch>"
   ```
4. Create PR targeting the correct base branch.
5. Repeat for next cluster.

After all PRs are created, check out the last cluster's branch and report the full
stack (see Output). Exit — skip steps 5–7.

Complete when: user has chosen single-PR or stacked, and stacked flow is finished if chosen.

### 5. PR Status

Check for an existing PR on this branch: `gh pr list --head "$BRANCH" --json number,state`

Use the provided status argument, or default: new PR=opened, update=draft.

Complete when: existing PR state is known and target status is determined.

### 6. Push

Always push with `git push -u origin "$BRANCH"` — the `-u` flag sets tracking on new branches
and is a no-op when the upstream is already correctly set, so it is always safe to use.

If push fails because the remote branch has diverged, run `git pull --rebase origin $BRANCH`
and retry the push once. If the rebase itself has conflicts, stop and report.

Complete when: branch is pushed and tracking the remote.

### 7. PR Creation/Update

Generate the PR body using the format script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/push-pr/scripts/format-pr-body.py --base "origin/$BASE"
```

Exit 1 means no changes found relative to base; report to user. On success, use stdout
as the PR body directly.

**New PR:**
```bash
gh pr create --title "<title>" --body "<format-pr-body output>" --base "$BASE"
# If status=ready: gh pr ready
```

**Existing PR:** Add a comment listing new commits since last push; update PR status if
the status argument changed.

```bash
PR_NUM=$(gh pr list --head "$BRANCH" --json number -q '.[0].number')
gh pr comment $PR_NUM --body "New commits: ..."
```

Complete when: PR URL is obtained and status matches the target.

## Constraints

Produce clean, unattributed PRs that match the project's existing commit and PR style:
- No Co-authored-by or AI signatures — PRs should look like human-authored work
- No "Generated with Claude Code" — same reason; attribution is the user's choice
- No emojis in PR title or description — most project conventions use plain text
- Use existing git user config only — never modify `user.name` or `user.email`

## Edge Cases

- No remote → suggest `git remote add origin <url>` and stop
- No `gh` CLI → report requirement and stop
- Branch behind remote → pull/rebase before pushing
- No commits to push → report and stop
- Cherry-pick conflict during stacked flow → stop, report the cluster name, failing commit
  SHA, and conflicting file(s). Suggest `git cherry-pick --abort` followed by manual
  resolution, then re-running

## Output

Single PR: branch name, PR URL, PR status (opened/draft/ready).

Stacked PRs: ordered list showing each PR URL and the branch it targets, plus the name
of the final branch now checked out. 