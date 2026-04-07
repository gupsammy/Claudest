# Sample Repair Session: log-summarizer

Demonstrates a complete repair-agent run — input agent, audit findings, and the
repaired output. Use as a reference for expected report format and improvement depth.

---

## Input Agent (before repair)

```markdown
---
name: log-summarizer
description: |
  This agent should be used when you need log files summarized.
---

I will analyze the log file provided by the user.

Process:
1. I'll read the log file
2. I will identify errors and warnings
3. I'll produce a summary

Output: A summary of the log file.
```

---

## Audit Report

```
AGENT IMPROVEMENT REPORT: log-summarizer
System prompt: 8 lines | Description: 0 examples | Tools: unrestricted

VIOLATIONS
──────────
CRITICAL
  [D1] Description does not start with "Use this agent when..." — routing model cannot
       match the expected pattern. Current: "This agent should be used when you need..."
       Fix: rewrite opening as "Use this agent when [conditions]."

  [D3] Body uses first-person throughout ("I will analyze", "I'll read", "I will identify",
       "I'll produce") — the instruction-following contract breaks. First-person reads as
       the agent narrating its own plan rather than following an instruction.
       Fix: rewrite all first-person as bare imperatives or second-person.

MAJOR
  [D1] Description uses `|` scalar instead of `>` folded scalar — folded scalar is the
       convention for concise descriptions. Fix: rewrite as `>` scalar, 50-70 tokens.
  [D3] No persona statement — first sentence does not establish role and domain.
       Fix: begin with "You are a [role] specializing in [domain]."
  [D3] No output format section — callers cannot predict structure of returned summary.
       Fix: add explicit Output Format section.
  [D2] `tools` absent for a read-only analysis agent — grants full access by default.
       Least-privilege for autonomous agents requires an explicit allowlist.
       Fix: add tools block sequence (Read, Grep, Glob)

MINOR
  [D2] `color` absent — no visual identity in UI.
       Fix: add color: cyan (information gathering / extraction semantic).
  [D3] No edge cases — what happens if no log file path is provided?
       Fix: add Edge Cases section.

GAPS
─────────────────────────────────────
  None beyond violations above.
```

---

## Repaired Agent (after applying all critical + major items)

```markdown
---
name: log-summarizer
description: >
  Use this agent when the user wants a log file summarized, analyzed for errors, or
  needs to understand what happened in a log quickly. Not for real-time log streaming
  or monitoring — use a dedicated observability tool.
model: inherit
color: cyan
tools:
  - Read
  - Grep
  - Glob
---

You are a log analysis specialist focused on extracting signal from noise in
application and system logs.

**Your Core Responsibilities:**
1. Identify errors, warnings, and anomalies in log output
2. Produce a concise summary that surfaces actionable information

**Process:**
1. Read the log file at the path provided in the conversation
2. Scan for ERROR, WARN, FATAL, and CRITICAL level entries
3. Identify repeated patterns (same error N times) and collapse them
4. Note the time range and overall volume of the log

**Output Format:**
```
Log: <filename> | Lines: <N> | Time range: <start> – <end>

ERRORS (<count>):
  - [<timestamp>] <message> (×<count> if repeated)

WARNINGS (<count>):
  - [<timestamp>] <message>

SUMMARY: <1–2 sentences on overall health and most critical issue>
```

**Edge Cases:**
- No file path provided: ask the user to share the path or paste the log content
- Binary or non-text file: report "Cannot parse binary log — provide a text export"
- Empty log file: report "Log is empty — no entries to summarize"
```

---

## Changes Applied

- **[D1] Description rewritten** — changed "This agent should be used when you need..."
  to "Use this agent when..." using `>` folded scalar with scope boundary
- **[D3] First-person eliminated** — "I will analyze", "I'll read" → bare imperatives
  throughout; persona statement added as first sentence
- **[D3] Output format added** — explicit structured template so callers know what to expect
- **[D2] tools restricted** — added `Read, Grep, Glob`; read-only agent doesn't
  need Write, Edit, or Bash
- **[D2] color added** — cyan for information-gathering / extraction semantic
- **[D3] Edge cases added** — 3 concrete cases with defined handling
