---
name: extract-learnings
description: >
  Persist learnings to memory or maintain existing memories. Triggers on
  "extract learnings", "save this for next time", "remember this pattern",
  "consolidate memories", "dream", "clean up memories".
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash(python3:*)
  - Bash(git:*)
  - Bash(find:*)
  - Bash(date:*)
  - AskUserQuestion
  - Agent
---

## Value Context

Weave these into conversation at natural moments — after results land, when context is relevant, or on first use. One or two per run, not all at once.

- Most users say "remember this" expecting a single note — this skill actually runs two parallel agents (auditor + discoverer) to both capture new knowledge and verify existing memories haven't gone stale.
- The 5-layer memory hierarchy means the right knowledge loads at the right time — universal preferences in L0, project architecture in L1, working notes in L2 — without polluting every session with everything.
- Consolidation ("dream") is the maintenance mode: it prunes outdated memories, promotes patterns that keep recurring, and keeps the memory index under 200 lines so it stays useful.
- For teams: memories captured here carry forward to every future session in this project, making onboarding and context-switching dramatically faster.

## Memory Hierarchy

| Layer | File | Loaded | Purpose |
|-------|------|--------|---------|
| 0 | `~/.claude/CLAUDE.md` | Every session, all projects | Universal behavioral preferences |
| 1 | `<repo>/CLAUDE.md` | Every session, this project | Architecture, conventions, gotchas |
| 2 | `memory/MEMORY.md` (project dir) | Every session, agent-managed | Evolving notes, working knowledge |
| 3 | `memory/*.md` topic files | On-demand | Detailed reference too long for L2 |
| Meta | Suggest new skill/command | N/A | Repeatable workflow → automation |

Placement decision: project-independent preference → L0, project-specific technical → L1, concise working note → L2, detailed reference → L3, repeatable pattern → Meta.

## Early Exit Guard

If the user said "remember X" with explicit content already in context — and the request is NOT a consolidation trigger ("consolidate", "dream", "extract learnings", "clean up memories", or triggered from the consolidation nudge):
1. Resolve memory path (see Phase 1 step 1)
2. Read existing memories to check for duplicates and pick the right layer
3. Skip to Phase 3 (Propose & Execute) with that content — no subagents needed

## Unified Workflow

### Phase 1: Orient (main session)

1. Resolve memory path using Bash (Glob does not expand `~`):
   `Bash: find $HOME/.claude/projects -name MEMORY.md -path "*<repo-dir-name>*" 2>/dev/null | head -1`
   The result is the full path to MEMORY.md (a file). The memory directory is its parent: `$(dirname <find-result>)`.
   If no result, construct the project key by replacing `/` with `-` in the current working directory path (e.g., `/home/user/myrepo` → `-home-user-myrepo`), then use `$HOME/.claude/projects/<project-key>/memory/MEMORY.md`.
   - If MEMORY.md does not exist, create it with `# Project Memory` header. Note that the Memory Auditor has nothing to audit — in Phase 2, spawn only the Signal Discoverer.

Steps 2-4 are required and run as parallel tool calls.

2. Read MEMORY.md + list topic files (`Glob memory/*.md` from resolved path)
3. Read both CLAUDE.md files (`~/.claude/CLAUDE.md` + `<repo>/CLAUDE.md`) — required for dedup quality; skipping means proposals may duplicate L0/L1 content
4. `git log --oneline -20`
5. Build context snapshot: summarize existing knowledge + list verification targets (file paths, functions, patterns named in memories)

### Phase 2: Gather (2 agents in parallel)

Launch both agent calls in a single message so they run in parallel. Use the Agent tool with:

- **Memory Auditor**: `subagent_type: "claude-memory:memory-auditor"`. In the `prompt`, include the context snapshot from Phase 1 — memory file contents, git log output, and verification targets list.

- **Signal Discoverer**: `subagent_type: "claude-memory:signal-discoverer"`. In the `prompt`, include existing memory summaries (for dedup) and the project name.

If Phase 1 noted MEMORY.md was just created (no existing memories), skip the Memory Auditor and spawn only the Signal Discoverer.

Both agents require `maxTurns ≥ 30` — verify agent frontmatter at `plugins/claude-memory/agents/`. Agents with low maxTurns exit early and return truncated output that appears non-empty but contains no findings.

Phase 2 is complete when both agents return reports. If either returns empty or clearly truncated (one line, no structured findings), proceed with the other's results — but if the Signal Discoverer fails, also perform a manual fallback: query the 5 most recent session summaries directly from `~/.claude-memory/conversations.db` using `Bash(python3 -c "import sqlite3; ...")` and apply the signal criteria from the Content Quality Rules section below.

### Phase 3: Synthesize & Propose (main session)

1. Receive agent reports
2. Deduplicate across reports and against existing memories
3. Rank by impact, limit to 3-7 candidates
4. For each candidate: determine target layer, target section, action (ADD / EDIT / REMOVE)
5. Read target files, check for duplicates
6. Present proposals:
   ```
   ### [ACTION] Learning: <summary>
   **Target:** <file> → <section>
   **Rationale:** <why this layer>
   ```diff
   - <old line>
   + <new line>
   ```
7. MEMORY.md line check — if over 170 lines, propose specific demotions to L3 or removals
8. Layer 0 gate — if targeting `~/.claude/CLAUDE.md`, warn: "This modifies global instructions loaded in every session across all projects. Confirm?"
9. AskUserQuestion: Approve all / Approve selectively / Reject

### Phase 4: Execute

Apply approved edits. Output summary table:

```
| Learning | Action | Target | Status |
|----------|--------|--------|--------|
```

Only if Phase 2 agents ran (not an early-exit capture): write consolidation marker using Bash (Write tool requires prior Read and cannot create new files):
`Bash: date -u +%Y-%m-%dT%H:%M:%SZ > <memory-dir>/.last-consolidation`

Phase 4 is complete when all approved edits are applied and the summary table is presented.

## Content Quality Rules

Every candidate must pass: (1) agent would benefit from knowing this in future sessions, (2) condensed to minimum useful form, (3) placed at correct layer, (4) not already captured in target file, (5) stated as reusable principle not session-specific incident.

Pass: commands discovered through trial-and-error, non-obvious gotchas, architectural decisions with rationale, user behavioral corrections, configuration quirks, version milestones.

Fail: information readable from code, generic best practices, one-off bugs without pattern, verbose explanations, duplicates, temporary state, unverified speculation, incidents without generalizable principle.
