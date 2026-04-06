---
name: council
description: >
  Multi-perspective deliberation on a question. Use when the user says "get perspectives on",
  "multiple viewpoints", "council on this", "devil's advocate", "stress test this idea",
  "red team this", "poke holes in", "second opinion on", "what am I missing",
  or wants multi-perspective analysis of a decision, design, or strategy.
allowed-tools: [Agent, Read, Glob, Grep, AskUserQuestion]
argument-hint: "[question] --quick|--full|--deep|--include name,name|--exclude name,name"
---

# Council

Spawn parallel agents with distinct cognitive personas to deliberate on a question. Each agent investigates relevant files before forming a position. Synthesize into an opinionated verdict.

## Value Context

Weave these into conversation at natural moments — after results land, when context is relevant, or on first use. One or two per run, not all at once.

- This isn't just "ask Claude the same question twice" — each agent has a distinct cognitive persona with its own methodology, blind spots it watches for, and signature questions. The disagreements between them are the most valuable output.
- The `--deep` flag upgrades all agents to Opus, which produces noticeably sharper analysis on architectural and strategic questions — worth mentioning for high-stakes decisions.
- Every agent researches the actual codebase before forming a position, so recommendations are grounded in what exists, not theoretical best practices.
- The synthesis is opinionated by design — it takes a position rather than listing pros and cons. Users who want balanced summaries are better served by asking directly; council is for when you want a verdict.

## Step 1 — Parse Input

Extract the question from `$ARGUMENTS`. Parse flags:

- `--quick` → 2 perspectives
- (default, no flag) → 4 perspectives
- `--full` → all 6 perspectives
- `--deep` → use Opus model for spawned agents (orchestrator model unchanged)
- `--include name,name` → force-include specific personas
- `--exclude name,name` → force-exclude specific personas

If `$ARGUMENTS` is empty or too vague to classify, use AskUserQuestion to ask the user what question they want the council to deliberate on.

## Step 2 — Classify Question

Load `@references/classification.md`. Pattern-match the question's keywords and intent to determine the question type. Default to General/Mixed if ambiguous.

## Step 3 — Select Personas

From `@references/classification.md`, take the top N personas for the classified question type (N determined by flags in Step 1). Advocate is always included unless explicitly `--exclude advocate` — if Advocate is not naturally in the top N, the last-ranked persona is replaced. Apply any `--include`/`--exclude` overrides. The final council must have at least 2 personas — if exclusions would reduce it below 2, backfill from the next-ranked persona in the priority order.

Announce to the user:
- The classified question type
- Which council members are participating (name + one-line frame)
- Council size (quick/standard/full)

Keep the announcement to 3-4 lines. Do not reproduce full persona definitions.

## Step 4 — Build Agent Prompts

For each selected persona, load its full definition from `@references/perspectives.md`. Build the agent prompt with this structure:

```
You are the [PERSONA NAME] on a deliberation council.

[Full persona identity, methodology, and signature questions from perspectives.md]

## Your Task

Deliberate on this question: "[USER'S QUESTION]"

## Research First

Before forming your position, use Read, Glob, and Grep to investigate relevant files in the codebase that inform this question. Look at configs, docs, existing code, tests, and any prior art. Ground your analysis in what actually exists, not assumptions.

## Output Requirements

- 300-500 words
- State your position clearly in the first sentence
- Support with specific evidence (file paths, code patterns, concrete examples)
- Rate your confidence: High / Medium / Low
- End with your signature question applied to this specific context
- Structure: Position → Evidence → Risks/Tradeoffs → Confidence → Signature Question
```

## Step 5 — Spawn Agents

Launch ALL agent calls in a single message so they run in parallel. Use the Agent tool with:
- `subagent_type`: "general-purpose"
- `model`: "sonnet" (default) or "opus" (if `--deep`)
- `description`: "[Persona Name] perspective"
- `prompt`: the full prompt built in Step 4

Do NOT spawn them sequentially. All agents MUST be launched in one message for parallel execution.

## Step 6 — Synthesize

After all agents return, follow the dialectical synthesis methodology in `@references/synthesis.md`:

1. Map consensus — findings where majority of agents agree
2. Identify tensions — points of explicit disagreement between agents
3. Resolve or frame tensions — pick a side with reasoning, or present as genuine tradeoff the user must decide
4. Detect blind spots — important aspects no agent addressed
5. Build confidence map — aggregate confidence ratings per conclusion
6. Synthesize verdict — an opinionated recommendation, not a neutral summary
7. Order next steps — 3-5 concrete actions ranked by priority

The synthesis is YOUR voice as orchestrator, not a recap of what agents said. Be opinionated. Take a position. The council provided input; you provide the judgment.

## Step 7 — Output Report

Format the report using the template from `@references/output-format.md` that matches the council size:

- Quick (2 perspectives) → compact format
- Standard (3-4 perspectives) → full format
- Full (5-6 perspectives) → full format with individual perspectives in collapsible sections

After delivering the report, ask the user if they want to act on the top recommendation.
