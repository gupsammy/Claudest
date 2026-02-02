---
name: past-conversations
description: Fetch past conversations from memory database. Triggers on "what did we discuss", "continue where we left off", "remember when", "as I mentioned", "you suggested", "we decided". Also triggers on implicit signals like past-tense references ("the bug we fixed"), possessives without context ("my project"), or assumptive questions ("do you remember").
---

# past-conversations

## Tools

### recent_chats

Retrieve recent conversation sessions with all messages.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/past-conversations/scripts/recent_chats.py --n 3
```

| Option | Effect |
|--------|--------|
| `--n N` | Number of sessions (1-20, default 3) |
| `--sort-order` | 'desc' (newest first, default) or 'asc' |
| `--before DATE` | Sessions before this datetime (ISO) |
| `--after DATE` | Sessions after this datetime (ISO) |
| `--project NAME` | Filter by project name(s), comma-separated |
| `--verbose` | Include files_modified and commits |
| `--format` | 'markdown' (default) or 'json' |

**Usage guidance:**
- Use `--n 20` to maximize information gathering
- For comprehensive review, use `--n 20`
- For quick context, use `--n 3-5`
- Use `--verbose` for lenses that need file/commit context

### search_conversations

Search for sessions containing keywords using FTS5 full-text search.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/past-conversations/scripts/search_conversations.py --query "keyword"
```

| Option | Effect |
|--------|--------|
| `--query` | Required - substantive keywords |
| `--max-results N` | Limit results (1-10, default 5) |
| `--project NAME` | Filter by project name(s), comma-separated |
| `--verbose` | Include files_modified and commits |
| `--format` | 'markdown' (default) or 'json' |

**Output**: Default markdown format (token-efficient):
```
## myproject | 2026-02-01 10:00
Session: abc123

### Conversation

**User:** ...
**Assistant:** ...
```

Use `--format json` when structured data is needed.

---

## Workflow

1. **Identify the lens** from user intent (see Routing table below)

2. **Gather context** using lens-appropriate tools:
   - For recent context: `recent_chats.py --n N`
   - For keyword search: `search_conversations.py --query "keywords"`
   Use the Parameters table to select tool and options.

3. **Apply lens questions** to analyze the retrieved conversations

4. **Deepen the search** if initial results are insufficient:
   - Retrieve more sessions: `--n 20`
   - Search for specific terms that surfaced
   - Filter by project: `--project projectname`

---

## Query Construction

When building search queries from user requests, extract substantive keywords.

**Include (high-confidence):**
- Specific nouns: technologies, concepts, project names, proper nouns
- Domain-specific terms: "OAuth", "SQL queries", "derivative"
- Unique identifiers or unusual phrases

**Exclude (low-confidence):**
- Generic verbs: "discuss", "talk", "mention", "say"
- Time markers: "yesterday", "last week", "recently"
- Vague nouns: "thing", "stuff", "issue", "problem"
- Meta-conversation: "conversation", "chat", "question"

**Algorithm:**
1. Extract substantive keywords from user request
2. If 0 keywords → ask for clarification ("Which project specifically?")
3. If 1+ specific terms → search with those terms
4. If initial search returns nothing → try broader terms or more sessions

---

## Lenses

### Routing

| User Says | Lens |
|-----------|------|
| "where were we", "recap" | restore-context |
| "what I learned", "reflect" | extract-learnings |
| "gaps", "struggling" | find-gaps |
| "mentor", "review process" | review-process |
| "retro", "project review" | run-retro |
| "decisions", "CLAUDE.md" | extract-decisions |
| "bad habits", "antipatterns" | find-antipatterns |

### Parameters

| Lens | Tool | Options | Also Gather |
|------|------|---------|-------------|
| restore-context | recent_chats | `--n 5 --verbose` | `git status`, `git log -10` |
| extract-learnings | recent_chats | `--n 20` | — |
| find-gaps | search_conversations | `--query "confused struggling"` | — |
| review-process | recent_chats | `--n 20 --verbose` | recent git log |
| run-retro | recent_chats | `--n 20 --project NAME --verbose` | full git history |
| extract-decisions | search_conversations | `--query "decided chose trade-off"` | — |
| find-antipatterns | search_conversations | `--query "again same mistake repeated"` | — |

Use `--verbose` for lenses that benefit from seeing files modified and commits (restore-context, review-process, run-retro).

### Core Questions

| Lens | Ask |
|------|-----|
| restore-context | What's unfinished? What were the next steps? |
| extract-learnings | Where did understanding shift? What mistakes became lessons? |
| find-gaps | What topics recur? Where is guidance needed repeatedly? |
| review-process | Is there planning before coding? Is debugging systematic? |
| run-retro | How did the solution evolve? What worked? What was painful? |
| extract-decisions | What trade-offs were discussed? What was rejected and why? |
| find-antipatterns | What mistakes repeat? What confusions persist? |

**Follow-ups**: find-gaps → suggest `learn-anything`. extract-decisions → suggest `/updateclaudemd`.

### Supplementary Search Patterns

When recent retrieval doesn't surface enough, use targeted searches:

| Lens | Query |
|------|-------|
| extract-learnings | `--query "learned realized understand clicked"` |
| find-gaps | `--query "confused struggling help don't understand"` |
| extract-decisions | `--query "decided chose instead trade-off because"` |
| find-antipatterns | `--query "again same mistake repeated forgot"` |

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
