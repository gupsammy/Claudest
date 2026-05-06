---
description: Set up GitHub Actions workflows for this repo
tools:
  - Read
  - Glob
  - Bash
  - Write
  - Edit
  - AskUserQuestion
---

# Setup GitHub Actions

Analyze the project's existing `.github/workflows/` and generate correct, production-ready GitHub Actions workflows using `anthropics/claude-code-action`.

## Principles

These govern every workflow generated. Apply them rather than copying templates.

### Permission Model — Output-Driven

Grant only the permissions Claude needs to write to. Never over-permission.

| What Claude produces | Permission required |
|---|---|
| Pushes commits, modifies files | `contents: write` |
| Posts PR comments, inline reviews, updates PR description | `pull-requests: write` |
| Labels, closes, or comments on issues | `issues: write` |
| Reads CI/test failure logs | `actions: read` |
| OAuth token auth | `id-token: write` (always required) |

The interactive `@claude` workflow (`claude.yml`) primarily needs `contents: write` and `pull-requests: write`. It does NOT need `issues: write` unless it explicitly labels or comments on issues — it mostly runs in the background making code changes and commits.

For issue workflows that should trigger on issues from non-contributors, add `allowed_non_write_users: "*"` and pass `github_token: ${{ secrets.GITHUB_TOKEN }}` alongside the OAuth token.

### Tool Scope — Capability-Driven

Scope `--allowedTools` to exactly the minimum tools needed. Never use broad wildcards unless the workflow legitimately needs broad access.

| Workflow type | Allowed tools |
|---|---|
| Interactive `@claude` (code changes + commits) | `Edit,Write,Read,Glob,Grep,Bash(git:*),Bash(gh pr:*),Bash(gh issue:*)` |
| PR review (read + post comments only) | `Read,Glob,Grep,Bash(gh pr diff:*),Bash(gh pr view:*),Bash(gh pr comment:*),mcp__github_inline_comment__create_inline_comment` |
| Issue triage (label + comment) | `mcp__github__get_issue,mcp__github__search_issues,mcp__github__list_issues,mcp__github__create_issue_comment,mcp__github__update_issue,Bash(gh issue view:*),Bash(gh issue edit:*),Bash(gh label:*)` |
| Issue deduplication | `mcp__github__get_issue,mcp__github__search_issues,mcp__github__list_issues,mcp__github__create_issue_comment,mcp__github__update_issue,mcp__github__get_issue_comments` |
| CI/test failure auto-fix | `Edit,Write,Read,Glob,Grep,Bash(git:*),Bash(gh:*)` |
| Manual code analysis | `Read,Glob,Grep,Bash(gh:*)` |

### Auth

Always use `claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}`. Do not add, change, or generate auth configuration — it is set up separately by `/install-github-app`.

### File Naming — Canonical Names

Use these exact filenames to prevent duplicates across runs:

| Workflow | Filename |
|---|---|
| Interactive assistant | `claude.yml` |
| PR code review | `claude-code-review.yml` |
| Issue triage | `claude-issue-triage.yml` |
| Issue deduplication | `claude-issue-dedup.yml` |
| CI failure auto-fix | `claude-ci-fix.yml` |
| Test failure analysis | `claude-test-analysis.yml` |
| Manual code analysis | `claude-analysis.yml` |

If a file with the canonical name already exists, **edit it in place** — do not create a parallel file with a different name.

### PR Review Prompt — Project-Customized

Never use a generic "review this PR" prompt. Before writing the review workflow YAML, scan the repository to understand its tech stack and conventions, then generate a prompt customized for this specific project.

**Scanning step** (execute before writing the review YAML):

1. Read config files: `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `pom.xml`, `*.csproj`, or equivalent
2. Read `CLAUDE.md`, `CONTRIBUTING.md`, and linter/formatter configs (`.eslintrc*`, `.prettierrc*`, `rustfmt.toml`, `.golangci.yml`, etc.)
3. Identify the major artifact types in the codebase (e.g., "React components + Express handlers", "Python hooks + SKILL.md files", "Go handlers + protobuf definitions")
4. Note project-specific conventions, security requirements, or architectural constraints

**Prompt structure** — generate a 4-phase review prompt using what you discovered:

**Phase 1 — Context Discovery**: Instruct the reviewer to read config files to confirm the tech stack, read CLAUDE.md/CONTRIBUTING.md for project conventions, then run `gh pr diff` to get the full diff and categorize changed files by type (source, tests, config, docs, CI).

**Phase 2 — Change Comprehension**: Instruct the reviewer to summarize the PR's intent in one sentence, read surrounding code (not just the diff) to understand context, and identify existing patterns the codebase uses. Comprehension before critique is the single biggest lever for reducing false positives.

**Phase 3 — Deep Analysis**: Evaluate the diff across dimensions relevant to the detected stack. Select from this table — include dimensions that apply, skip those that don't:

| Dimension | When to include | Stack-specific examples |
|---|---|---|
| Correctness & logic | Always | Off-by-one errors, null handling, race conditions |
| Security | Always — tailor to stack | SQL injection (DB), XSS (web), memory safety (C/Rust), path traversal (file I/O) |
| Performance | Performance-sensitive code present | N+1 queries, unnecessary re-renders, hot-path allocations |
| Error handling | Always | Uncaught exceptions, missing error propagation, silent failures |
| API & interface design | Changes touch public APIs/types | Breaking changes, naming consistency, backward compatibility |
| Testing | Project has tests | Coverage of new code paths, edge cases, test quality |
| Documentation | Project has docs | README/API doc accuracy for changed features |
| Maintainability | Always | Over-engineering, dead code, naming clarity, DRY violations |

Use the language and framework conventions discovered in Phase 1. If a linter or formatter is configured, explicitly instruct the reviewer to skip formatting and style nits — those are automated.

**Phase 4 — Output**: Instruct the reviewer to:

- Post inline comments on specific lines, classified by severity:
  - 🔴 **Must fix** — bugs, security vulnerabilities, data loss risks
  - 🟡 **Should fix** — error handling gaps, performance issues, API design concerns
  - 🟢 **Suggestion** — alternative approaches, minor optimizations, readability wins
  - ❓ **Question** — clarification needed, intent unclear, design choice to discuss
- Post a single top-level summary comment with: one-sentence PR summary, overall assessment, key findings grouped by severity, and positive callouts for well-written code
- If no issues are found, say so — don't manufacture feedback

Show the generated prompt to the user and ask for confirmation before writing the YAML.

### Progress Tracking

Set `track_progress: true` for all review and analysis workflows.

## Workflow

### Step 1 — Analyze

Scan existing workflows:

```bash
ls .github/workflows/ 2>/dev/null
cat .github/workflows/*.yml 2>/dev/null
```

**Pre-check:** If `.github/workflows/` does not exist or contains no `.yml` files, stop. Tell the user:

> No GitHub Actions workflows were found. `/setup-github-actions` configures Claude Code workflows, but the GitHub App and initial workflow skeleton need to exist first. Run `/install-github-app` to complete that setup, then re-run `/setup-github-actions`.

Do not proceed past this point until the user has run `/install-github-app`.

For each `.yml` found, classify:
- **Broken default**: Uses `anthropics/claude-code-action@v1`, all permissions are `read`, no `prompt` or `claude_args` configured — this is the vanilla skeleton installed by `/install-github-app`. Flag it prominently.
- **Broken**: Uses `anthropics/claude-code-action@v1` but either (a) has no `Install Claude Code` step before the action, or (b) has the step but the `run` line is `npm install -g @anthropic-ai/claude-code` without `mkdir -p ~/.local/bin &&` as a prefix. Either condition means the workflow silently fails on every run — the action hardcodes `~/.local/bin/claude` as the binary location, and that directory is absent on fresh runners. Flag as critical and require a fix.
- **Upgradeable**: An existing Claude Code workflow that works but uses outdated patterns (checkout@v4, generic prompt, missing `track_progress`, permissions too broad or too narrow, no `--model` in `claude_args`). Note what needs updating. Missing `--model` means the action uses its own default, which may change across releases — add `--model sonnet` to pin the family (resolves to latest Sonnet).
- **Existing Claude Code workflow**: Already correctly configured for a specific purpose. Note what it covers and its filename.
- **Other CI**: Present but unrelated to Claude Code. Note as present, no action needed.

### Step 2 — Present Adaptive Menu

Tell the user:
1. What was found (existing workflows, broken defaults, broken workflows, upgradeable workflows)
2. What Claude Code workflow types are missing

If a broken default or broken workflow is detected, surface it first — these are the highest priority fixes and must be resolved before the workflow can run.
If upgradeable workflows are detected, surface them second with a summary of what would change.

Use AskUserQuestion (multiSelect) to let the user pick which workflows to set up. Offer only what is NOT already correctly configured:

| Workflow | Trigger | What Claude does |
|---|---|---|
| **Interactive assistant** (`claude.yml`) | `@claude` mentions in issues/PRs/comments | Makes code changes, commits, responds to requests |
| **PR code review** (auto) | Every PR opened/updated | Reviews all PRs automatically on open/sync/reopen |
| **Issue triage** | Issue opened | Auto-labels and categorizes new issues |
| **Issue deduplication** | Issue opened | Finds and flags duplicate issues |
| **CI failure auto-fix** | CI workflow fails | Creates a fix branch when CI breaks |
| **Test failure analysis** | Test run fails | Detects flaky vs real failures, auto-retries flaky tests |
| **Manual code analysis** | `workflow_dispatch` | On-demand analysis triggered manually from the Actions tab |

If the user selects **PR code review**, ask a follow-up:
- **Trigger scope**: All PRs / Only external contributors / Only PRs touching specific paths?
- **Review depth**: Comprehensive (4-phase structured review) / Lightweight (security + correctness only)?

### Step 3 — Generate and Write

**For broken default upgrade**: Show the diff — what permissions change, what `claude_args` gets added. Ask for confirmation before overwriting.

**For upgradeable workflows**: Show what would change (checkout version, prompt, permissions). Ask for confirmation, then use Edit to update in place.

**For PR review workflows**: Execute the scanning step from the "PR Review Prompt — Project-Customized" principle. Use discovered context to generate a 4-phase prompt. Show the generated prompt to the user for confirmation before writing.

**For each selected workflow**: Generate YAML applying the principles above. If a file with the canonical name already exists, use Edit to update it. Otherwise, use Write to create it.

Structural template — adapt permissions, tools, prompt, trigger, and job name per workflow:

```yaml
name: <Descriptive Name>

on:
  <trigger>:
    types: [<events>]

jobs:
  <job-name>:
    runs-on: ubuntu-latest
    timeout-minutes: <15 for review/triage, 30 for CI fix, omit for interactive>
    permissions:
      contents: <read|write>        # write only if Claude pushes commits
      pull-requests: <read|write>   # write only if Claude posts PR comments/reviews
      issues: <read|write>          # write only if Claude labels or comments on issues
      id-token: write               # always required for OAuth token auth
      actions: read                 # only if Claude reads CI/test logs

    steps:
      - name: Checkout repository
        uses: actions/checkout@v6
        with:
          fetch-depth: <1 for read-only workflows, 0 for workflows that create branches or need full history>

      - name: Install Claude Code
        run: mkdir -p ~/.local/bin && npm install -g @anthropic-ai/claude-code

      - name: <Action Name>
        uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          track_progress: true
          prompt: |
            <prompt — use project-customized 4-phase structure for review workflows>
          claude_args: |
            --model sonnet
            --allowedTools "<minimum capability-scoped tools>"
```

#### Workflow-Specific Patterns

**CI failure auto-fix** — Add a step before Claude that extracts failure logs:

```yaml
    steps:
      - name: Checkout code
        uses: actions/checkout@v6
        with:
          ref: ${{ github.event.workflow_run.head_branch }}
          fetch-depth: 0
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Setup git identity
        run: |
          git config --global user.email "claude[bot]@users.noreply.github.com"
          git config --global user.name "claude[bot]"

      - name: Get CI failure details
        id: failure_details
        uses: actions/github-script@v7
        with:
          script: |
            const jobs = await github.rest.actions.listJobsForWorkflowRun({
              owner: context.repo.owner,
              repo: context.repo.repo,
              run_id: context.payload.workflow_run.id
            });
            const failedJobs = jobs.data.jobs.filter(j => j.conclusion === 'failure');
            let logs = [];
            for (const job of failedJobs) {
              const log = await github.rest.actions.downloadJobLogsForWorkflowRun({
                owner: context.repo.owner, repo: context.repo.repo, job_id: job.id
              });
              logs.push({ jobName: job.name, logs: log.data.slice(-50000) });
            }
            return { failedJobs: failedJobs.map(j => j.name), logs };

      - name: Install Claude Code
        run: mkdir -p ~/.local/bin && npm install -g @anthropic-ai/claude-code

      - name: Fix CI failures with Claude
        uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          track_progress: true
          prompt: |
            CI failed on branch ${{ github.event.workflow_run.head_branch }}.
            Failed jobs: ${{ join(fromJSON(steps.failure_details.outputs.result).failedJobs, ', ') }}
            Error logs: ${{ toJSON(fromJSON(steps.failure_details.outputs.result).logs) }}
            <...project-specific fix instructions...>
          claude_args: |
            --model sonnet
            --allowedTools "<minimum capability-scoped tools>"
```

**Test failure analysis** — Instruct Claude to output a classification keyword on its own line for simple conditional branching:

```yaml
          prompt: |
            Analyze the test failure. Determine if it is flaky (intermittent, timing-dependent,
            environment-sensitive) or a real failure (deterministic bug in code).
            On the LAST line of your response, output exactly one of: FLAKY or REAL_FAILURE
```

Then add a conditional retry step that greps the output:

```yaml
      - name: Retry if flaky
        if: steps.analysis.outputs.result && contains(steps.analysis.outputs.result, 'FLAKY')
        run: <re-run the failed test suite>
```

**Issue triage** — Add `allowed_non_write_users` so issues from non-contributors still trigger:

```yaml
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          allowed_non_write_users: "*"
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

**Manual code analysis** — Use `workflow_dispatch` with typed inputs:

```yaml
on:
  workflow_dispatch:
    inputs:
      analysis_type:
        description: "Type of analysis to perform"
        required: true
        type: choice
        options:
          - security-review
          - summarize-recent-changes
          - architecture-overview
```

### Step 4 — Summarize

After writing all files, list:
- What was created or edited (filename, workflow name, whether new or updated)
- What trigger activates each workflow
- What Claude is allowed to do in each (based on permissions + tools granted)
