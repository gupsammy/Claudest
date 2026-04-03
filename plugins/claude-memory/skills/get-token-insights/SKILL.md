---
name: get-token-insights
description: >
  Ingest Claude Code usage data and surface token cost, cache, and workflow patterns.
allowed-tools:
  - Bash(python3:*)
  - Bash(open:*)
  - Agent
  - AskUserQuestion
---

# Get Token Insights

Parse JSONL conversation files from `~/.claude/projects/*/` into per-turn analytics tables, then analyze both cost-optimization opportunities and Claude Code workflow patterns (skills, agents, hooks).

## Step 1: Ingest

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/get-token-insights/scripts/ingest_token_data.py
```

First run processes all files (~100s for ~2500 files) — warn the user about the wait before running. Incremental runs complete in under 5s. The script populates analytics tables, deploys an interactive dashboard to `~/.claude-memory/dashboard.html` (built from `templates/dashboard.html`), and prints a slim JSON blob to stdout (full data goes to dashboard only).

If the script exits non-zero, report the error and stop.

## Step 1.5: Claude Code Feature Enrichment

Take the top 3 insights from the JSON output and spawn a `claude-code-guide` agent asking it to suggest Claude Code features, settings, or workflow changes that address those patterns. Use `subagent_type: "claude-code-guide"`. Weave its suggestions into the analysis in Step 2.

## Step 2: Analyze

Capture the JSON stdout from Step 1 as the analysis input. Structure the analysis in two parts:

### Part A: Cost-Optimization Consultant

#### Top-Line Summary
State the total spend, session count, date range, and average cost per session in one paragraph.

#### Priority Insights (top 3 by dollar waste)
For each insight from the `insights` array (sorted by waste_usd):
1. State the finding and its dollar impact
2. Explain the root cause so the user understands *why* this is happening
3. Present the solution with concrete steps — if a CLAUDE.md rule is suggested, show the exact rule text
4. State the estimated savings
5. Include any relevant Claude Code feature suggestions from Step 1.5

#### Model Economics
Compare cost across models. If one model dominates spend, call it out and estimate savings from switching routine tasks to a cheaper model.

#### Project Cost Ranking
List top 3 projects by dollar spend. For the most expensive project, identify what drives the cost.

### Part B: Workflow Analytics

#### Skill Usage
Summarize which skills are invoked most, error rates per skill, and any skills that appear underused relative to the user's workflow.

#### Agent Delegation Patterns
Show which subagent types are spawned, how often, and whether model overrides are being used. Flag if `subagent_type` is frequently omitted (defaults to general-purpose when Explore would suffice).

#### Hook Performance
Identify the slowest hooks by total runtime and average latency. Flag any hooks with high error rates.

Present the full analysis as markdown with the sections above. Ask the user if they want to dive deeper into any specific project, skill, or insight.

## Step 3: Open Dashboard

```bash
open ~/.claude-memory/dashboard.html
```

Note the dashboard is available for deeper exploration — Section 6 (Claude Code Ecosystem) has the new skill, agent, and hook charts.
