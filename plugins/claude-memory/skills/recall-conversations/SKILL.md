---
name: recall-conversations
description: >
  Recall, search, continue, or analyze past conversations. Triggers on recall phrases
  ("what did we discuss", "continue where we left off", "we decided"), retrospective
  phrases ("do a retro", "post-mortem", "what went well", "lessons learned",
  "find antipatterns"), and implicit signals (past-tense references, possessives
  without context, assumptive questions like "do you remember").
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(python3:*)
  - AskUserQuestion
  - Agent
---

## Value Context

Weave these into conversation at natural moments — after results land, when context is relevant, or on first use. One or two per run, not all at once.

- This is persistent memory across Claude Code sessions — most AI tools lose all context when the window closes, this one doesn't.
- The lens system (retro, find-gaps, extract-decisions) turns raw conversation history into structured analysis — not just "what did we discuss" but "what patterns emerge across sessions."
- Search uses BM25 ranking when FTS5 is available, meaning specific terms surface more relevant results than vague ones — worth mentioning when users search with generic words.
- Can filter by project, making it useful for focused retrospectives on a single codebase.
- The extract-decisions lens can surface CLAUDE.md-worthy rules the user stated but never persisted.

## Tools

Three scripts read the memory database. Run each as `python3 ${CLAUDE_PLUGIN_ROOT}/skills/recall-conversations/scripts/<script> <flags>`:

- `recent_chats.py` — sessions by recency, optionally windowed with `--after`/`--before`. `--timeline` instead returns a compact, uncapped index (one line per session: date, size, branch, uuid, title) with no message bodies. `--session UUID[,UUID]` fetches named sessions by uuid or 8+ character prefix, as printed by the timeline and search output.
- `search_conversations.py` — keyword search. Terms are OR-joined and BM25-ranked, so each term widens the match and rare terms weigh more. Returns whole sessions; `--excerpts` returns only the matching passages (up to 3 per session). When JSON meta says `retrieval: hybrid`, meaning-based matches are fused in, so paraphrases are found too; `--lexical` forces keywords only.
- `list_projects.py` — project names mapped to paths, session counts, and date spans. One name can cover many paths, and `--project NAME` merges all of them; when the user names a project, run `list_projects.py --match NAME` first.

Full flag catalog and output contracts: `references/tool-reference.md`.

---

## Workflow

### 1. Pick a lens and run its recipe

Each user intent maps to a lens. Recipes default to the **current project** — the scripts auto-detect from CWD, so no `--project` flag is needed for the common case.

| User Says | Lens | Recipe |
|-----------|------|--------|
| "where were we", "recap", "continue" | restore-context | `recent_chats.py --limit 5 --verbose` |
| "what did we decide about X", "when did we fix Y" | specific-lookup | `search_conversations.py --query "<terms>" --excerpts`, then `recent_chats.py --session <id>` for the one session whose passage answers it |
| "what I learned", "reflect on what I've learned" | extract-learnings | `recent_chats.py --limit 20` |
| "gaps", "where I'm struggling" | find-gaps | `search_conversations.py --query "confused stuck struggling unclear" --summary` |
| "mentor me", "review my process" | review-process | `recent_chats.py --limit 20 --verbose` |
| "retro", "retrospective", "look back", "post-mortem" | run-retro | `recent_chats.py --limit 20 --verbose --summary` |
| "decisions", "CLAUDE.md-worthy rules" | extract-decisions | `search_conversations.py --query "decided chose tradeoff rejected" --summary` |
| "antipatterns", "bad habits", "mistakes I repeat" | find-antipatterns | `search_conversations.py --query "mistake repeated forgot" --summary --all-projects` |

**Scope overrides**: append `--project NAME` for a different project (e.g. `--project pkm`), or `--all-projects` to widen across everything. Multiple specific projects: `--project claudest,pkm`.

For per-lens questions, follow-ups, and supplementary search patterns, load `references/lenses.md`.

### 2. Apply the lens's core question to the retrieved sessions

The recipe gets you the data. The lens tells you what to *look for* — for instance, run-retro asks "how did the solution evolve, what worked, what was painful". Load `references/lenses.md` if you need the question for your chosen lens.

### 3. Deepen if results are thin

- Retrieve more sessions: bump `--limit` (1-50 for both scripts; default 5)
- Search supplementary terms (per-lens patterns in `references/lenses.md`)
- Widen scope: append `--all-projects` to look across projects
- Reconstruct a long arc: `recent_chats.py --timeline --sort-order asc` lists every session; pick the sessions that matter, then pull them with `--session`
- Two rounds of deepening with no new signal → synthesize from what you have rather than thrashing further

### 4. Manage volume on broad queries (high blast radius)

The scripts emit full transcripts, so broad/multi-session lenses can flood context. Defend in two tiers — never trigger on session count alone; continuation-restore and specific-lookup lenses stay in-thread regardless of how many sessions match (the answer is small):

1. `--summary` — the broad recipes above already pass it; add it to any other `--all-projects` or multi-week pull. It emits precomputed per-session digests instead of full content (~3× smaller). A large full-content pull sets `summary_suggested` (JSON meta) or prints an `INFO:` line on stderr. Leave it off for restore-context and specific lookups — they need exact text, which `--excerpts` and `--session` deliver in small pieces.
2. Fan out — only when even `--summary` output is still too big: `fanout_suggested` is true in JSON meta (or the stderr `INFO:` line recommends fanning out). Spawn one `Agent` per project (`subagent_type: general-purpose`, `model: sonnet`), each running the recipe scoped to its own project and returning a structured digest; then reduce. Shard by project, never by arbitrary session count — count-based splits sever a decision or antipattern thread across agents, and per-project shards preserve cross-session dedup within each mind.

---

## Query Construction

Search matches words, not meaning. Use terms that separate the target sessions from the rest: specific nouns, technologies, file and project names, error text. Words found in most sessions ("discuss", "yesterday", "conversation", "because", "insight") add noise. Since terms are OR-joined, put synonyms and alternate spellings in the same query ("auth login oauth signin") instead of running separate searches.

When the request has no such terms ("what were we doing", "do you remember"), use `recent_chats.py` instead of search. Ask the user only when neither recency nor a keyword can find it.

---

## Synthesis

Shape the answer to the lens. restore-context ends with the concrete next step. Analytical lenses lead with the findings that matter most, each tied to its evidence (date, project, file, or a short quote) and to the action it suggests. Length follows the evidence.
