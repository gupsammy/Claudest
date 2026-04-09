---
name: update-claudemd
description: This skill should be used when the user says "update CLAUDE.md", "refresh CLAUDE.md", "sync CLAUDE.md with the codebase", "reorganize CLAUDE.md", "optimize project instructions", or when CLAUDE.md is stale, verbose, or out of sync.
allowed-tools:
  - Read
  - Glob
  - Grep
  - Edit
  - Write
  - Bash(wc:*)
  - Bash(git:*)
  - Bash(mkdir:*)
  - Bash(cp:*)
  - Task
  - AskUserQuestion
---

# Update and Optimize CLAUDE.md

Reconcile project CLAUDE.md and its topic files against codebase reality and git history since each file's last commit. Enforce progressive disclosure: CLAUDE.md is a top-level index of project identity, universal invariants, and pointers to topic files at `.claude/claudemd-topics/*.md`.

Reconciliation scope includes every file in `.claude/claudemd-topics/`. Topic files are first-class content — they share the same staleness, accuracy, and duplication risks as CLAUDE.md itself and must be audited, reconciled, and (when warranted) edited during every run. They are not appendices.

Scope boundary: project CLAUDE.md (L1) and its topic files only. User-global `~/.claude/CLAUDE.md` (L0) is out of scope unless the user explicitly asks.

## Governing Principle

Content in CLAUDE.md is justified only if it changes how Claude acts in the next session AND is needed across most tasks — not subsystem-specific. Subsystem-scoped content still matters but belongs in a topic file, loaded on demand.

Content in a topic file is justified only if it changes how Claude acts within its subsystem AND is accurate against current codebase reality. Stale topic files are worse than missing ones because Claude trusts them.

Phase 3 operationalizes this principle with a seven-action set that lets content flow both downward (demote from CLAUDE.md to a topic file) and upward (promote from a topic file back to CLAUDE.md when a detail becomes universal). The goal is a living progressive-disclosure hierarchy, not a one-way archive.

## Budget

CLAUDE.md: soft target ~120 lines, soft ceiling 150 lines. Over-budget files are flagged, not forced — hand-maintained CLAUDE.md is respected. Line counts measured with `wc -l`.

Topic files: informational soft ceiling ~300 lines each. Topic files legitimately grow as their subsystem grows; past ~300 lines, flag the overage in Phase 7 and suggest splitting by sub-topic. Never block. The CLAUDE.md budget is the only hard constraint.

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

Record current `CLAUDE.md` line count with `wc -l`. List `.claude/claudemd-topics/*.md` if the directory exists and record each topic file's line count AND last-commit date via `git log -1 --format="%ai" -- <path>`. The per-file dates are required in Phase 2 so Agent B can anchor each file's staleness window independently — using CLAUDE.md's date for every file produces wrong windows when cadences differ.

Run cheap git probes in two steps.

First, resolve the last CLAUDE.md commit date (sequential — downstream probe depends on it):

- `git log -1 --format="%ai" -- CLAUDE.md` → record as `<last_claudemd_date>`. If CLAUDE.md has no history, fall back to `git log --reverse --format="%ai" | head -1`.

Then run the remaining probes as parallel Bash calls:

- `git log --since="<last_claudemd_date>" --oneline --no-merges | wc -l` — churn
- `git ls-files | wc -l` — tracked files
- `git ls-files | awk -F/ 'NF>1{print $1}' | sort -u | wc -l` — top-level directory count

Compute N using the fan-out rule. Decide the shard plan: which top-level directories each Codebase Scan agent will cover.

Exit condition: CLAUDE.md exists, baseline line counts recorded, per-topic-file line counts and last-commit dates recorded, N computed, shard plan decided.

## Phase 2 — Parallel Research

Launch `3 + N` Explore agents in a single message. All use `subagent_type: Explore`.

**Agent A — CLAUDE.md and Topic Files Audit** (`subagent_type: Explore`)

Read the project's CLAUDE.md and every file in `.claude/claudemd-topics/` if present. Apply the same H1/H2/H3 classification pass to CLAUDE.md AND to every topic file — topic files are first-class content, not inventory.

For each H1/H2/H3 section in either source, record: `source` (CLAUDE.md path or topic file path), `name`, `line_count`, and `classification_hint` (project identity, universal invariant, subsystem-scoped detail, conditional guidance, or derivable from file inspection). For topic-file sections, also flag any that have become universal invariants — content that now changes how Claude acts across almost every task in the project, not just within one subsystem — these are promote-back candidates.

Flag stale content (version numbers, paths, commands, features that look wrong). Flag cross-file duplication — topic-file content that duplicates CLAUDE.md, or topic files that overlap each other.

Also scan for index drift between CLAUDE.md and the topic files directory:
- `dangling_references` — topic files pointed at from CLAUDE.md's `## Topic Files` section but missing from disk
- `unreferenced_topic_files` — topic files on disk with no pointer from CLAUDE.md

Return: `sections[{source, name, line_count, classification_hint, promote_back_candidate}]`, `stale_items[{source, item, reason}]`, `duplications[{items, locations}]`, `existing_topic_files[{path, line_count, last_touched, subject, section_count}]`, `dangling_references[]`, `unreferenced_topic_files[]`.

**Agent B — Git History Per File** (`subagent_type: Explore`)

Walk git history once per instruction file, anchored to each file's own last-touch date. Using the dates captured in Phase 1:

1. For CLAUDE.md: run `git log --since="<claudemd_last_touched>" --format="%s%n%b" --no-merges` across the whole repo.
2. For each topic file: run `git log --since="<topic_file_last_touched>" --format="%s%n%b" --no-merges -- <directories the topic file covers>`. The covered directories are inferred from the topic file's subject and load trigger (e.g. a topic file about hooks covers `plugins/*/hooks/`). If the subject spans the whole repo, use the repo root.

Fallback for files with no history: use the initial commit date from `git log --reverse --format="%ai" | head -1`.

Categorize each commit per file: `new_subsystems` (net-new code areas), `architecture_changes` (decisions or refactors affecting how code should be written), `convention_changes` (new patterns), `removed_features` (gone from the codebase), `other`. Skip CI, formatting, version bumps.

Return: `per_file[{path, last_touched, changes_since{new_subsystems, architecture_changes, convention_changes, removed_features, other}}]`. One entry per instruction file.

**Agents C1..CN — Codebase Scan Shards** (`subagent_type: Explore`)

One agent per shard. Each shard covers the assigned top-level directories from the shard plan. Extract: tech stack and frameworks, established patterns (testing style, error handling, module organization), non-obvious conventions a new contributor would trip on, key commands from config files or scripts, gotchas from comments or subdirectory READMEs, and clusters of content subsystem-scoped enough to deserve their own topic file. Return: `tech_stack`, `patterns`, `conventions`, `commands`, `gotchas`, `topic_file_candidates[{filename, subject}]`.

Exit condition: all `3 + N` agents return structured notes. If any agent returns empty or truncated, proceed with available results and note the gap in the Phase 5 proposal.

## Phase 3 — Classify

Assign exactly one action to every section from Agent A (CLAUDE.md sections AND topic-file sections) and every new finding from Agents B and C1..CN. The action set is bidirectional — content flows both downward (demote) and upward (promote):

- `keep` — content stays where it is, unchanged
- `update` — content stays where it is, rewritten for accuracy (stale facts, changed paths, new conventions)
- `delete` — derivable from reading `package.json`, `ls`, or one source file; or the feature no longer exists
- `demote → <topic-file>` — CLAUDE.md content that is subsystem-scoped moves to a topic file
- `promote → CLAUDE.md` — topic-file content that has become a universal invariant moves back to CLAUDE.md. Reserved for sections Agent A flagged as `promote_back_candidate` — content whose load trigger now applies to almost every task, not just one subsystem. This is the reverse of `demote` and must be used sparingly; the CLAUDE.md budget is tight
- `move → <other-topic-file>` — topic-file content whose subject clustering changed and now belongs in a different topic file
- `add` — new content surfaced by research that belongs in CLAUDE.md or a topic file

Address dangling-reference items from Agent A as reconciliation actions:
- `dangling_references` — either create the missing topic file (if the referenced subject is still valid) or remove the dead pointer from CLAUDE.md
- `unreferenced_topic_files` — either add a pointer from CLAUDE.md's `## Topic Files` section or delete the orphan (the user decides in Phase 5)

Cluster demotion candidates and topic-file candidates from codebase-scan shards by subject. Before creating a new topic file, check if an existing topic file already covers that subject — if so, add to the existing file instead.

The top 3-5 universal invariants in CLAUDE.md are never demoted. Conversely, a topic-file section is only promoted back when it clearly meets the "changes how Claude acts in almost every task" bar.

Exit condition: every section from every source and every new finding has exactly one action, demotion candidates are grouped into named topic-file clusters, dangling references and orphan topic files have resolution plans.

## Phase 4 — Budget Check

Estimate the post-reconciliation CLAUDE.md line count: `current + added + promoted_in − demoted − deleted`. The `promoted_in` term accounts for topic-file sections moving back to CLAUDE.md; a large promote-back set can push CLAUDE.md over budget even when other actions are net-neutral.

If the estimate is ≤ 150, stop demoting. Having topic files does not justify over-demotion.

If the estimate is > 150, first pressure-test the promotions — a promote-back action must clear a high bar. Drop any promote-back whose universality is marginal. Then demote additional lowest-priority `keep` items by cluster until under 150. Top invariants are off-limits. If the file still exceeds 150 after reasonable demotion and pruned promotions — typically because it is hand-maintained with intentional density — flag the overage in Phase 5 and let the user decide whether to accept it.

Also check each touched topic file against the ~300-line informational ceiling. Flag any overage for Phase 5 presentation, but do not force demotion or splitting — topic files may legitimately be dense. The CLAUDE.md budget is the only hard constraint.

Exit condition: final action list exists with pre/post line count estimate, a CLAUDE.md budget status (`under`, `over (user override)`), and a list of topic files flagged as over the informational ceiling.

## Phase 5 — Propose

Present the plan in a structured summary. Every instruction file — CLAUDE.md and each topic file being touched — is shown with the same level of detail, not just as a filename.

**CLAUDE.md**
- Line count: `<current> → <estimated>` (budget: `<status>`)
- Keep: count of sections
- Update: list with one-line rationale each (what is stale, what is being corrected)
- Delete: list with one-line rationale each (e.g. "derivable from `package.json`")
- Demote: grouped by target topic file, showing which sections go to each
- Promoted in: list of sections moved back from topic files because they are now universal invariants
- Add: list of new content with source (which research agent found it)

**Topic files**

For each topic file being created:
- Filename, estimated line count, which sections seeded it (from demotion or research findings)

For each topic file being updated:
- Path, line count: `<current> → <estimated>`
- Keep / update / delete / promoted out / moved in — same structured breakdown as CLAUDE.md
- Budget flag if over ~300 lines

For each topic file being left alone: path and one-line reason (no drift detected).

**Index reconciliation**
- Dangling references resolved (list)
- Orphan topic files resolved (list)

Use AskUserQuestion with three options: approve all, approve selectively, reject.

Exit condition: user has explicitly approved an action set.

## Phase 6 — Write

Execute in this order so topic-file pointers resolve:

1. Run `cp CLAUDE.md CLAUDE.md.bak` via Bash to create a backup before any writes
2. Create `.claude/claudemd-topics/` with `mkdir -p` if missing
3. Apply topic file actions in this sub-order: delete orphaned topic files the user approved for removal, then write new topic files, then edit existing topic files (applying keep/update/delete/promoted-out/moved-in actions). Promoted-out sections are removed from the topic file in this step; they reappear in CLAUDE.md in step 4
4. Write CLAUDE.md with a regenerated `## Topic Files` section at the end, excluding pointers to deleted topic files. Promoted-in sections from step 3 are written as new H2 sections in the CLAUDE.md body (not inside the `## Topic Files` index block)

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

Output a diff summary with per-file deltas for every instruction file touched:

**CLAUDE.md**
- `<before> → <after>` lines (`<budget status>`)
- Deletions: list with rationale
- Additions: list with source
- Promoted in: list of topic-file sections that moved up to CLAUDE.md (if any)
- Invariants preserved: top 3-5 quoted verbatim so the user can verify they survived

**Topic files created** — for each: path, line count, seeded-from source

**Topic files updated** — for each: path, `<before> → <after>` lines, one-line change summary (e.g. "2 stale paths corrected, 1 section promoted to CLAUDE.md, 1 new section added from Agent C2 findings"). Flag any topic file over the ~300-line informational ceiling.

**Topic files unchanged** — bare list (no drift detected).

**Index reconciliation** — dangling references resolved, orphan topic files resolved or linked.

**Backup**: `CLAUDE.md.bak` was created in Phase 6 step 1. Tell the user they can diff with `diff CLAUDE.md.bak CLAUDE.md` and remove it themselves once satisfied — the skill does not delete the backup.

Exit condition: report delivered, advise user to remove `CLAUDE.md.bak` after review.
