# claude-coding ![v0.2.18](https://img.shields.io/badge/v0.2.18-blue?style=flat-square)

Coding workflow skills for Claude Code. Eight skills and one command covering the commit loop, project maintenance, documentation, and CI setup: stage and commit with conventional format, push and open a PR with smart branch handling, safely prune merged or stale branches, keep your CLAUDE.md accurate and concise, generate professional READMEs through a structured interview, create or update a changelog from git history, refresh an existing README against current codebase state, and configure production-ready Claude Code GitHub Actions workflows.

## Why

Coding sessions involve repetitive workflow decisions: which files belong in the same commit, whether to split concerns, whether you're on the right branch, what base branch to target, whether your project docs still reflect reality. These skills encode the right defaults so Claude handles those decisions consistently — and asks when it genuinely can't.

## Installation

```
/plugin marketplace add gupsammy/claudest
/plugin install claude-coding@claudest
```

Requires `git` and `gh` CLI (for push-pr). Both must be authenticated.

## Skills

### commit

Analyze and commit changes with intelligent file grouping and conventional commits. Reads `git status` and `git diff`, groups files by purpose rather than directory, validates if a linter is configured, and writes a conventional commit message (`feat`, `fix`, `docs`, etc.). Handles temporary file exclusion, multi-concern splits, and optional push in one flow.

Triggers on: "commit my changes", "commit this", "git commit", "save my work", "stage and commit", "create a commit", "commit what I've done".

### push-pr

Push commits and create or update pull requests with automatic branch management and scope-aware multi-PR splitting. Detects if you're on `main` with unpushed commits and cuts a feature branch before pushing without destroying local state. Uses origin-based comparisons to determine divergence safely. Analyzes the changeset for size and diversity: if the diff exceeds ~400 lines or spans 3+ distinct commit scopes, it proposes stacked PRs (each targeting the previous cluster's branch) and asks before splitting. Creates new PRs or comments on existing ones. Calls the `commit` skill first if there are uncommitted changes.

Triggers on: "push this", "push my changes", "create a PR", "open a pull request", "make a PR", "submit for review", "send this up", "open PR", "pr please".

### clean-branches

Safely remove merged and stale git branches with confirmation. Finds branches already merged into main and branches with no commits in 30+ days, shows them categorized, and asks before deleting anything. Never touches protected branches. Remote deletion requires explicit confirmation. Pass an optional branch pattern (e.g. `feature/*`) to limit candidates to matching branches only.

Triggers on: "clean up branches", "delete merged branches", "prune stale branches", "git branch cleanup", "remove old branches".

### update-claudemd

Audit and optimize your project's CLAUDE.md file. Reads the current file, explores the codebase to verify accuracy, cuts anything that doesn't change how Claude acts in the next session, and rewrites for scannability. Creates a `.bak` backup before writing. Targets 150-250 lines of actionable content.

Triggers on: "update CLAUDE.md", "refresh the docs", "sync claude config", "optimize project instructions", "clean up CLAUDE.md", "improve CLAUDE.md", "fix CLAUDE.md".

### make-readme

Generate a professional `README.md` through a structured interview. Detects the project type from manifest files, then asks about depth (minimal, standard, or comprehensive), header style, sections, and badges. Minimal produces a 50-line focused doc; standard adds structured sections and shields.io badges; comprehensive adds a full Table of Contents, API reference, FAQ, and back-to-top links throughout. Writes the complete file in one pass.

Triggers on: "create a README", "generate a README", "make a readme", "write a README for my project", "add a README", "document my project", "readme with badges".

### make-changelog

Create or update `CHANGELOG.md` from git history using Keep-a-Changelog format. Detects existing changelog state and determines scope (fresh, fill, or unreleased-only). Launches one Haiku subagent per version range in parallel for token-efficient processing. Categorizes commits by user-observable impact rather than commit prefix, with present-tense imperative entries.

Triggers on: "create a changelog", "generate a changelog", "update my changelog", "fill in the changelog", "changelog from git history", "write changelog", "release notes", "my project needs a CHANGELOG".

### update-readme

Refresh an existing `README.md` against current codebase state, git history, and changelog content. Runs `make-changelog` first so changelog context is available when revising README sections. Then launches three parallel agents — one to audit stale content and thin sections, one to scan the codebase for the current version and structure, and one to categorize git commits since the README was last touched. Applies updates in priority order: version numbers and badge URLs first, then stale paths and commands, then placeholder cleanup, then new features from git history, then missing standard sections. Falls back to `make-readme` if no substantial README is found. Uses `Edit` for targeted changes and `Write` only when more than 60% of the file changes.

Triggers on: "update my README", "refresh the README", "README is outdated", "sync README with the codebase", "improve my README", "my README is stale", "update readme from git history", "readme is out of date".

## Commands

### /setup-github-actions

Analyze existing `.github/workflows/` and generate production-ready Claude Code GitHub Actions workflows. Detects broken default skeletons installed by `/install-github-app` and surfaces them first. Presents a multi-select menu of workflow types not yet configured — interactive assistant (`@claude` mentions), automatic PR code review, filtered-path PR review, issue triage, issue deduplication, CI failure auto-fix, test failure analysis, and manual on-demand analysis. Applies a permission-minimal model (grants only what Claude needs to write to) and scopes `--allowedTools` to the minimum capabilities per workflow type. Generates language-agnostic review prompts using stack discovery rather than hardcoded framework assumptions. Writes each selected workflow to `.github/workflows/<name>.yml` and summarizes what was created.

Requires `/install-github-app` to have been run first — if no `.yml` files exist under `.github/workflows/`, the command stops and prompts you to do that setup first.

## License

MIT
