---
name: get-token-insights
description: >
  This skill should be used when the user says "get token insights", "show my
  token usage", "token analysis", "usage insights", "how am I using tokens",
  "analyze my claude usage", or "show cache efficiency". Not for general
  context-reduction advice or API cost questions.
allowed-tools:
  - Bash(python3:*)
  - Bash(open:*)
  - AskUserQuestion
---

# Get Token Insights

Parse JSONL conversation files from `~/.claude/projects/*/` into per-turn analytics tables, then analyze usage patterns and surface actionable cost-saving opportunities.

## Step 1: Ingest

```bash
python3 $CLAUDE_PLUGIN_ROOT/scripts/ingest_token_data.py
```

First run processes all files (~100s for ~2500 files) — warn the user about the wait before running. Incremental runs complete in under 5s. The script populates analytics tables, deploys an interactive dashboard to `~/.claude-memory/dashboard.html` (built from `templates/dashboard.html`), and prints a JSON blob to stdout.

If the script exits non-zero, report the error and stop.

## Step 2: Analyze as a Cost-Optimization Consultant

Capture the JSON stdout from Step 1 as the analysis input. Prioritize actionable savings over descriptive summaries — every insight should answer "what should I change and how much will it save?"

### Top-Line Summary
State the total spend, session count, date range, and average cost per session in one paragraph.

### Priority Insights (top 3 by dollar waste)
For each insight from the `insights` array (sorted by waste_usd):
1. State the finding and its dollar impact
2. Explain the root cause so the user understands *why* this is happening
3. Present the solution with concrete steps — if a CLAUDE.md rule is suggested, show the exact rule text
4. State the estimated savings

### Model Economics
Compare cost across models. If one model dominates spend, call it out and estimate savings from switching routine tasks to a cheaper model.

### Project Cost Ranking
List top 3 projects by dollar spend. For the most expensive project, identify what drives the cost (model choice, session count, cache inefficiency, or antipatterns).

### Remaining Insights
Briefly cover any remaining insights not in the top 3.

Present the full analysis as markdown with the sections above. Ask the user if they want to dive deeper into any specific project or insight.

## Step 3: Open Dashboard

```bash
open ~/.claude-memory/dashboard.html
```

Note the dashboard is available for deeper exploration of the charts.
