---
name: get-pr-comments
description: >
  This skill should be used when the user says "get PR comments", "show PR feedback",
  "what comments on my PR", "PR review comments", "show me the review", "what did
  reviewers say", or asks about feedback on a pull request. Not for creating PRs or
  responding to comments.
allowed-tools:
  - Bash(gh:*)
  - Bash(python3:*)
  - Read
  - Grep
  - AskUserQuestion
argument-hint: "[PR number or URL]"
---

# Get PR Comments

Fetch, organize, and present all comments on a GitHub pull request — issue-level
comments, review bodies, and inline review comments — grouped by human vs bot,
with actionable items (must-fix, optional) extracted from structured reviews
and inline comments.

## Pre-Flight Context

- Current branch: `!git rev-parse --abbrev-ref HEAD`
- Repo: `!gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || echo "unknown"`
- Current branch PR: `!gh pr view --json number,title --jq '"\(.number) — \(.title)"' 2>/dev/null || echo "none"`

## Workflow

### 1. Identify the PR

Parse `$ARGUMENTS` for a PR number or URL. If present, use it directly.

If no arguments provided, check the pre-flight "Current branch PR" value. If it
contains a PR number (not "none"), use the detected PR.

If no PR detected, list open PRs:

```bash
gh pr list --state open --limit 10 --json number,title,headRefName --jq '.[] | "\(.number)\t\(.title)\t(\(.headRefName))"'
```

If the list is empty, report "No open PRs found for this repository" and stop.
If only one open PR exists, use it directly. Otherwise present options via
AskUserQuestion.

### 2. Fetch comments

Run the fetch script with the resolved PR number (default text output is
pre-formatted and token-efficient; use `--output json` only for programmatic
consumers):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/get-pr-comments/scripts/fetch_pr_comments.py <PR_NUMBER>
```

Exit 0 = proceed. Exit 2 = `gh` auth or network error — report to user.

### 3. Present results

The script output is already formatted for presentation. If the output starts
with "0 human, 0 bot", report "No comments on this PR yet" and skip to Step 4.

Otherwise, relay the script output directly. The output is structured as:
actionable items (must-fix, optional) first, then human comments, then bot
comments (truncated). Do not reformat or reparse — present as-is.

### 4. Suggest next steps

After presenting comments, offer context-appropriate actions:

- If must-fix items exist: "Want me to address these must-fix items?"
- If inline comments reference specific files: "Want me to read the referenced
  files and check if these issues are already resolved?"
- If the PR is the user's: "Want me to respond to any of these comments?"
