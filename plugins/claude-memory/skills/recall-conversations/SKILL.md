---
name: recall-conversations
description: >
  This skill should be used when the user asks to recall, search, or continue
  past conversations. Triggers on "what did we discuss", "continue where we
  left off", "remember when", "as I mentioned", "you suggested", "we decided",
  "search my conversations", "find the conversation where", "what did we work on".
  Also triggers on implicit signals like past-tense references ("the bug we fixed"),
  possessives without context ("my project"), or assumptive questions ("do you remember").
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

Two scripts retrieve data. For full option catalogs, load `references/tool-reference.md`.

**recent_chats.py** — retrieve recent sessions:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recall-conversations/scripts/recent_chats.py --n 3
```

**search_conversations.py** — keyword search across all sessions:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recall-conversations/scripts/search_conversations.py --query "keyword"
```

---

## Workflow

1. **Identify the lens** from user intent:

| User Says | Lens |
|-----------|------|
| "where were we", "recap" | restore-context |
| "what I learned", "reflect" | extract-learnings |
| "gaps", "struggling" | find-gaps |
| "mentor", "review process" | review-process |
| "retro", "project review" | run-retro |
| "decisions", "CLAUDE.md" | extract-decisions |
| "bad habits", "antipatterns" | find-antipatterns |

   Load `references/lenses.md` for per-lens parameters, core questions, and supplementary search patterns.

2. **Gather context** using lens-appropriate tools:
   - For recent context: `recent_chats.py --n N`
   - For keyword search: `search_conversations.py --query "keywords"`

3. **Apply lens questions** to analyze the retrieved conversations.

4. **Deepen the search** if initial results are insufficient:
   - Retrieve more sessions: `--n 20`
   - Search for specific terms that surfaced
   - Filter by project: `--project projectname`
   - If 2 rounds of deepening yield no new relevant sessions, synthesize from available data.

---

## Query Construction

Search terms should be content-bearing words that discriminate between sessions — high information value words that are rare enough to rank relevant sessions above irrelevant ones. BM25 ranking (when FTS5 is available) weights rare terms higher automatically.

**Include:** specific nouns, technologies, concepts, project names, domain terms, unique phrases. More terms improve ranking precision.

**Exclude:** generic verbs ("discuss", "talk"), time markers ("yesterday"), vague nouns ("thing", "stuff"), meta-conversation words ("conversation", "chat") — these appear in nearly every session and add noise rather than signal.

**Algorithm:**
1. Extract substantive keywords from user request
2. If 0 keywords, ask for clarification ("Which project specifically?")
3. If 1+ specific terms, search with those terms; use `--project` to narrow scope

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
