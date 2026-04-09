---
name: update-claudemd
description: >
  This skill should be used when the user says "update CLAUDE.md", "refresh
  the docs", "sync CLAUDE.md with the codebase", "optimize project
  instructions", "clean up CLAUDE.md", "improve CLAUDE.md", "fix CLAUDE.md",
  "reorganize CLAUDE.md", "CLAUDE.md is too long", "extract topics from
  CLAUDE.md", "CLAUDE.md progressive disclosure", or when CLAUDE.md is stale,
  verbose, or out of sync.
allowed-tools:
  - Read
  - Glob
  - Grep
  - Edit
  - Write
  - Bash(wc:*)
  - Bash(git:*)
  - Bash(ls:*)
  - Bash(mkdir:*)
  - Bash(cp:*)
  - Task
  - AskUserQuestion
---

# Update and Optimize CLAUDE.md

Reconcile project CLAUDE.md against codebase reality and git history since its last commit. Enforce progressive disclosure: CLAUDE.md is a top-level index of project identity, universal invariants, and pointers to topic files at `.claude/claudemd-topics/*.md`. Scope: project CLAUDE.md (L1) only. User-global `~/.claude/CLAUDE.md` (L0) is out of scope unless the user explicitly asks.

## Governing Principle

Content in CLAUDE.md is justified only if it changes how Claude acts in the next session AND is needed across most tasks — not subsystem-specific. Every line resolves to one of four actions:

- **Keep in CLAUDE.md** — project identity, top 3-5 universal invariants, pointers to topic files
- **Demote to `.claude/claudemd-topics/<topic>.md`** — subsystem-scoped, conditional, or detailed content that still matters
- **Delete** — anything derivable from reading `package.json`, `ls`, or one source file
- **Add** — new content surfaced by research that belongs in L1 or a topic file

## Budget

Soft target ~120 lines, soft ceiling 150 lines. Over-budget files are flagged, not forced — hand-maintained CLAUDE.md is respected. Line counts measured with `wc -l`.

## Fan-out Rule

Codebase research scales with project size and churn. Compute once in Phase 1, apply in Phase 2.

Signals:
- `churn` — commits since last CLAUDE.md touch: `git log --since="<date>" --oneline --no-merges | wc -l`
- `files` — tracked files: `git ls-files | wc -l`

N (codebase-scan shards):

| churn      | files         | N |
|------------|---------------|---|
| < 20       | < 100         | 1 |
| 20 – 59    | 100 – 499     | 2 |
| 60 – 149   | 500 – 1999    | 3 |
| ≥ 150      | ≥ 2000        | 4 |

Compute N for each column independently. Take the max. Cap at 4. When N > 1, partition by top-level directory clusters (e.g. `plugins/` → shard 1; `scripts/ + tests/` → shard 2).

## Phase 1 — Orient and Compute Fan-out

Glob for `CLAUDE.md` at the project root. If missing, inform the user that creation is out of scope for this skill and stop.

Record current `CLAUDE.md` line count with `wc -l`. List `.claude/claudemd-topics/*.md` if the directory exists and record each topic file's line count.

Run cheap git probes in two steps.

First, resolve the last CLAUDE.md commit date (sequential — downstream probe depends on it):

- `git log -1 --format="%ai" -- CLAUDE.md` → record as `<last_claudemd_date>`. If CLAUDE.md has no history, fall back to `git log --reverse --format="%ai" | head -1`.

Then run the remaining probes as parallel Bash calls:

- `git log --since="<last_claudemd_date>" --oneline --no-merges | wc -l` — churn
- `git ls-files | wc -l` — tracked files
- `git ls-files | awk -F/ 'NF>1{print $1}' | sort -u | wc -l` — top-level directory count

Compute N using the fan-out rule. Decide the shard plan: which top-level directories each Codebase Scan agent will cover.

Exit condition: CLAUDE.md exists, baseline line counts recorded, N computed, shard plan decided.

## Phase 2 — Parallel Research

Launch `3 + N` Explore agents in a single message. All use `subagent_type: Explore`.

**Agent A — CLAUDE.md and Topic Files Audit** (`subagent_type: Explore`)

Read the project's CLAUDE.md and every file in `.claude/claudemd-topics/` if present. For each H1/H2/H3 section, record: name, line count, and classification hint (project identity, universal invariant, subsystem-scoped detail, conditional guidance, or derivable from file inspection). Flag stale content (version numbers, paths, commands, features that look wrong). Flag duplication across CLAUDE.md and topic files. Return: `sections[{name, line_count, classification_hint}]`, `stale_items`, `duplications`, `existing_topic_files[{name, line_count, subject}]`.

**Agent B — Git History Since Last CLAUDE.md Touch** (`subagent_type: Explore`)

Run `git log -1 --format="%ai" -- CLAUDE.md` to find the last CLAUDE.md commit date. If no history, use the initial commit date from `git log --reverse --format="%ai" | head -1`. Then run `git log --since="<that date>" --format="%s%n%b" --no-merges`. Categorize each commit: `new_subsystems` (net-new code areas), `architecture_changes` (decisions or refactors affecting how code should be written), `convention_changes` (new patterns), `removed_features` (gone from the codebase), `other`. Skip CI, formatting, version bumps. Return: `claudemd_last_updated` (ISO date), `changes_since` with the five lists.

**Agents C1..CN — Codebase Scan Shards** (`subagent_type: Explore`)

One agent per shard. Each shard covers the assigned top-level directories from the shard plan. Extract: tech stack and frameworks, established patterns (testing style, error handling, module organization), non-obvious conventions a new contributor would trip on, key commands from config files or scripts, gotchas from comments or subdirectory READMEs, and clusters of content subsystem-scoped enough to deserve their own topic file. Return: `tech_stack`, `patterns`, `conventions`, `commands`, `gotchas`, `topic_file_candidates[{filename, subject}]`.

Exit condition: all `3 + N` agents return structured notes. If any agent returns empty or truncated, proceed with available results and note the gap in the Phase 5 proposal.

## Phase 3 — Classify

Assign exactly one action (`keep`, `demote → <topic-file>`, `delete`, `add`) to every existing section from Agent A and every new finding from Agents B and C1..CN.

Cluster demotion candidates and topic-file candidates from codebase-scan shards by subject. Each cluster becomes one topic file with a proposed filename.

The top 3-5 universal invariants are never demoted — they are the content that changes how Claude acts in almost every task in this project.

Exit condition: every section and every finding has exactly one action, and demotion candidates are grouped into named topic-file clusters.

## Phase 4 — Budget Check

Estimate the post-reconciliation CLAUDE.md line count: `current + added − demoted − deleted`.

If the estimate is ≤ 150, stop demoting. Having topic files does not justify over-demotion.

If the estimate is > 150, demote additional lowest-priority `keep` items by cluster until under 150. Top invariants are off-limits. If the file still exceeds 150 after reasonable demotion — typically because it is hand-maintained with intentional density — flag the overage in Phase 5 and let the user decide whether to accept it.

Exit condition: final action list exists with pre/post line count estimate and a budget status: `under`, `over (user override)`.

## Phase 5 — Propose

Present the plan in a structured summary:

- Line count: `<current> → <estimated>` (budget: `<status>`)
- Keep: count of sections
- Delete: list with one-line rationale each (e.g. "derivable from `package.json`")
- Demote: grouped by target topic file, showing which sections go to each
- Add: list of new content with source (which research agent found it)
- Topic files to create: list with filenames and line estimates
- Topic files to update: list
- Topic files to leave alone: list

Use AskUserQuestion with three options: approve all, approve selectively, reject.

Exit condition: user has explicitly approved an action set.

## Phase 6 — Write

Execute in this order so topic-file pointers resolve:

1. Run `cp CLAUDE.md CLAUDE.md.bak` via Bash to create a backup before any writes
2. Create `.claude/claudemd-topics/` with `mkdir -p` if missing
3. Write or edit topic files
4. Write CLAUDE.md with a regenerated `## Topic Files` section at the end

The `## Topic Files` section is regenerated from actual disk state after step 2, not maintained manually. Each entry is a load trigger, not a descriptive link:

```
## Topic Files

Read on demand — do not load preemptively.

- `.claude/claudemd-topics/testing.md` — before writing or modifying tests
- `.claude/claudemd-topics/hooks.md` — before editing anything in plugins/*/hooks/
```

The load-trigger phrase is generated from the topic file's subject, not copied from a heading.

Use `Edit` for targeted changes when most of CLAUDE.md is staying. Use `Write` only if more than 60% of the file is changing — at that point the document is being regenerated rather than updated.

Exit condition: all files written, `## Topic Files` section reflects actual disk state.

## Phase 7 — Report

Output a diff summary:

- CLAUDE.md: `<before> → <after>` lines (`<budget status>`)
- Topic files created: list with line counts
- Topic files updated: list
- Deletions: list with rationale
- Additions: list with source
- Invariants preserved: top 3-5 quoted verbatim so the user can verify they survived
- Backup: `CLAUDE.md.bak` was created in Phase 6 step 1 — diff with `diff CLAUDE.md.bak CLAUDE.md` then remove

Exit condition: report delivered, advise user to remove `CLAUDE.md.bak` after review.
