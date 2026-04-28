---
name: recall-conversations
description: This skill should be used when the user asks to recall, search, continue, OR analyze/reflect on past conversations. Triggers on recall phrases ("what did we discuss", "continue where we left off", "remember when", "as I mentioned", "you suggested", "we decided", "search my conversations") AND on retrospective phrases ("do a retro", "retrospective", "look back at this project", "review the project", "reflect on what we built", "lessons learned", "what went well", "what went wrong", "post-mortem", "find gaps in my knowledge", "extract decisions", "review my process", "find antipatterns"). Use this skill whenever the user wants to reflect on, retrospect, or extract insights from past work — even without explicitly saying "conversation" or "history". Also triggers on implicit signals: past-tense references ("the bug we fixed"), possessives without context ("my project"), assumptive questions ("do you remember").
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash(python3:*)
  - AskUserQuestion
---

## Value Context

Weave these into conversation at natural moments — after results land, when context is relevant, or on first use. One or two per run, not all at once.

- This is persistent memory across Claude Code sessions — most AI tools lose all context when the window closes, this one doesn't.
- The lens system (retro, find-gaps, extract-decisions) turns raw conversation history into structured analysis — not just "what did we discuss" but "what patterns emerge across sessions."
- Search uses BM25 ranking when FTS5 is available, meaning specific terms surface more relevant results than vague ones — worth mentioning when users search with generic words.
- Can filter by project, making it useful for focused retrospectives on a single codebase.
- The extract-decisions lens can surface CLAUDE.md-worthy rules the user stated but never persisted.

## Tools

Two scripts retrieve data:
- `recent_chats.py` — retrieve recent sessions (with optional project filter)
- `search_conversations.py` — keyword search across sessions (with optional project filter)

Path prefix for both (used in recipes below):
```bash
PREFIX="python3 ${CLAUDE_PLUGIN_ROOT}/skills/recall-conversations/scripts"
```

For the full option catalog, load `references/tool-reference.md`.

---

## Workflow

### 1. Pick a lens and run its recipe

Each user intent maps to a lens with a full command recipe. Recipes default to the **current project** — the scripts auto-detect from CWD, so no `--project` flag is needed for the common case.

| User Says | Lens | Recipe (prepend `$PREFIX/`) |
|-----------|------|--------|
| "where were we", "recap", "continue" | restore-context | `recent_chats.py --limit 5 --verbose` |
| "what I learned", "reflect on what I've learned" | extract-learnings | `recent_chats.py --limit 20` |
| "gaps", "where I'm struggling" | find-gaps | `search_conversations.py --query "confused struggling help"` |
| "mentor me", "review my process" | review-process | `recent_chats.py --limit 20 --verbose` |
| "retro", "retrospective", "look back", "post-mortem" | run-retro | `recent_chats.py --limit 20 --verbose` |
| "decisions", "CLAUDE.md-worthy rules" | extract-decisions | `search_conversations.py --query "decided chose trade-off because"` |
| "antipatterns", "bad habits", "mistakes I repeat" | find-antipatterns | `search_conversations.py --query "again same mistake repeated forgot"` |

**Scope overrides**: append `--project NAME` for a different project (e.g. `--project pkm`), or `--all-projects` to widen across everything. Multiple specific projects: `--project claudest,pkm`.

Example expansion of the run-retro row:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recall-conversations/scripts/recent_chats.py --limit 20 --verbose
```

For per-lens questions, follow-ups, and supplementary search patterns, load `references/lenses.md`.

### 2. Apply the lens's core question to the retrieved sessions

The recipe gets you the data. The lens tells you what to *look for* — for instance, run-retro asks "how did the solution evolve, what worked, what was painful". Load `references/lenses.md` if you need the question for your chosen lens.

### 3. Deepen if results are thin

- Retrieve more sessions: bump `--limit` (1-50 for both scripts; default 5)
- Search supplementary terms (per-lens patterns in `references/lenses.md`)
- Widen scope: append `--all-projects` to look across projects
- Two rounds of deepening with no new signal → synthesize from what you have rather than thrashing further

---

## Query Construction

Search terms should be content-bearing words that discriminate between sessions — high information value words that are rare enough to rank relevant sessions above irrelevant ones. BM25 ranking (when FTS5 is available) weights rare terms higher automatically.

**Include:** specific nouns, technologies, concepts, project names, domain terms, unique phrases. More terms improve ranking precision.

**Exclude:** generic verbs ("discuss", "talk"), time markers ("yesterday"), vague nouns ("thing", "stuff"), meta-conversation words ("conversation", "chat") — these appear in nearly every session and add noise rather than signal.

**Algorithm:**
1. Extract substantive keywords from user request
2. If 0 keywords, ask for clarification ("Which project specifically?")
3. If 1+ specific terms, search with those terms; project scope is auto-detected — use `--project NAME` or `--all-projects` only to override

---

## Synthesis

### Principles

1. **Prioritize significance** — 3-5 key findings, not exhaustive lists
2. **Be specific** — file paths, dates, project names
3. **Make it actionable** — every finding suggests a response
4. **Show evidence** — quotes or references
5. **Keep it scannable** — clear structure, no walls of text

### Structure

```markdown
## [Analysis Type]: [Scope]

### Summary
[2-3 sentences]

### Findings
[Organized by whatever fits: categories, timeline, severity]

### Patterns
[Cross-cutting observations]

### Recommendations
[Actionable next steps]
```

### Length

Default: 300-500 words. Expand only when data warrants it.
