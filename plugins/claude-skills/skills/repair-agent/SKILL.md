---
name: repair-agent
description: >
  This skill should be used when the user asks to "repair an agent", "audit an agent",
  "fix my agent", "review agent quality", "check if my agent is well-written", "diagnose
  agent problems", "what's wrong with this agent", "improve this agent", or "what's wrong
  with this agent file". Not for skills — use repair-skill.
argument-hint: "<path-to-agent-file>"
---

# Agent Repair

Audit and improve an existing Claude Code agent against a gold standard. Unlike
create-agent (which generates from scratch), this skill diagnoses violations *and*
identifies gaps — what is broken, what is missing, and what would raise quality. The
output is a structured improvement plan covering all dimensions of agent design.

## Phase 1: Load the Agent

The agent file at `$ARGUMENTS` is loaded inline:

@$ARGUMENTS

Note the directory from `$ARGUMENTS` to verify it lives in `agents/` (not `skills/`).
Identify any `skills:` preloads listed in the frontmatter above.

If the file is a `SKILL.md` or lives in a `skills/` directory, decline and tell the user
to use `repair-skill` instead. If the path is missing or ambiguous, use AskUserQuestion
to resolve before proceeding.

**Load the following reference files before Phase 2:**

1. `${CLAUDE_PLUGIN_ROOT}/skills/repair-agent/references/agent-anatomy.md` — gold standard
   for system prompt structure, voice conventions, size invariants, naming, `skills:` preload
   pattern, and the gap analysis checklist. Required for Dimensions 3, 5, 6, and 7.
2. `${CLAUDE_PLUGIN_ROOT}/skills/create-agent/references/agent-frontmatter.md` — complete
   frontmatter field catalog, valid values, tool selection framework, color semantics, and
   execution modifiers. Required for Dimensions 1 and 2.

Proceed to Phase 2 when: agent file is confirmed in scope and reference files are loaded.

## Phase 2: Audit

Run each dimension independently. For each finding record: the dimension code, what is
wrong or missing, which principle it violates or which gold standard it falls short of,
and the specific change required. Proceed to Phase 3 when all 7 dimensions are evaluated.

**Finding types:**
- **Violation** — something present that contradicts a rule
- **Gap** — something absent that would improve the agent against the gold standard
- **Improvement** — something that works but could be meaningfully tightened

**Severity:**
- **critical** — breaks triggering or causes the agent to malfunction on every invocation
- **major** — degrades trigger accuracy, system prompt reliability, or autonomy safety
- **minor** — polish; the agent works but isn't as good as it could be

---

### Dimension 1 — Description Quality

The description is read by the routing model to decide when to spawn this agent. It is
the primary trigger mechanism and is always in context. Audit for violations *and* gaps.

**Violations:**
- **Framing:** Does it start with "Use this agent when..."? *Critical if wrong.*
- **Bloated description:** Description should be a concise `>` folded scalar, 50-70 tokens.
  No `<example>` blocks — they waste context without improving routing. *Major if examples
  present or description exceeds ~80 tokens.*
- **Scalar type:** Should use `>` (folded). *Minor if using `|` without XML blocks.*
- **Proactive hint missing:** For agents that fire after events, does the description include
  "Recommended PROACTIVELY after..."? *Minor if proactive intent without the hint.*

**Gaps:**
- **Negative trigger:** If adjacent agents exist with overlapping scope, does the description
  state when NOT to trigger? *Minor if adjacent agent exists without disambiguation.*

---

### Dimension 2 — Frontmatter Modifiers

Refer to `agent-frontmatter.md` for the complete field catalog, tool selection framework,
and color semantics. Omitting a field is not an error when the default applies — audit for
*mismatches* (violations) and *missing configuration that would improve the agent* (gaps).

**Violations:**
- Does `tools` include unscoped `Bash` for an agent that doesn't need full shell access?
  Agents run autonomously with no human in the loop — unrestricted Bash is the highest
  blast-radius grant. *Major.*
- Is `model: opus` set for a task sonnet handles? Cost scales directly per spawn. *Major.*
- Is `isolation: worktree` set without the agent performing git-state modifications?
  Unnecessary isolation adds overhead. *Minor.*
- Does `disallowedTools` block a tool the system prompt requires? *Critical.*

**Gaps:**
- Is `color` absent? Visual identity in the UI helps users track which agent is active
  in multi-agent workflows. *Minor.*
- Is `tools` absent for a read-only analysis agent? Least-privilege requires an explicit
  allowlist for autonomous agents — omitting it grants full access when restricted access
  would suffice. *Major for analysis-only agents.*
- Is `maxTurns` absent for a task with a predictable completion horizon? Unbounded agents
  can loop on ambiguous input. *Minor for bounded tasks.*
- Is `skills` absent for an agent with domain-specific knowledge needs? Embedding domain
  reference directly in the system prompt inflates every spawn; `skills:` defers it.
  *Major if system prompt exceeds 300 lines of embedded reference data.*
- Is `isolation: worktree` absent for an agent that modifies files in the working tree?
  Without isolation, modifications are immediate and irreversible during the run. *Major.*
- Is `memory` absent for an agent that would benefit from cross-session learning? Agents
  that review code, audit patterns, or accumulate project knowledge should use persistent
  memory (`project` recommended default). If `memory` is set, does the system prompt include
  memory maintenance instructions? Without them, the agent won't proactively update its
  knowledge base. *Major if the agent's domain involves pattern accumulation.*
- Is `effort` absent for a cost-sensitive or complexity-varying agent? Fast classification
  tasks benefit from `effort: low`; deep reasoning tasks from `effort: high`. *Minor.*
- Is `initialPrompt` absent for a session-mode agent designed for `--agent` use? Without
  it, the agent waits for user input instead of self-starting. *Minor if applicable.*

---

### Dimension 3 — System Prompt Voice

The markdown body of an agent file is its system prompt. Voice, persona, and structural
conventions determine whether the agent behaves as a specialist or a generic assistant.
Refer to `agent-anatomy.md` for voice conventions and the gold standard structure.

**Violations:**
- **First-person language:** Does the body contain "I will", "I'll", "I am", or any
  first-person construction? *Critical* — the system prompt is an address to the agent;
  first-person reads as the agent narrating its own plan rather than following an
  instruction. Both second-person ("You will analyze...") and bare imperatives in process
  steps ("Analyze...") are correct conventions — only first-person breaks the contract.
- **Third-person self-description:** Does the body refer to the agent in third person
  ("This agent will analyze...", "The agent should...")? *Major* — the body must address
  the agent directly, not describe it from the outside.
- **No persona statement:** Does the first sentence establish role and domain? Without one,
  the agent has no expert identity to shape downstream decisions. *Major.*
- **No numbered process steps:** Unstructured prose instructions produce variable behavior
  across invocations. *Major if unstructured.*
- **No output format:** Is there an **Output Format** section? Callers — human or
  orchestrating skill — need predictable structure to consume results. *Major if absent.*

**Gaps:**
- **No edge cases:** Predefined handling prevents mid-task failures that cost retries.
  Common cases: no input provided, ambiguous input, target missing, empty result. *Minor.*
- **Judgment steps without criteria:** Steps like "analyze the situation" or "assess quality"
  without explicit criteria for what to consider and what constitutes a good outcome.
  *Major per uncovered judgment step.*

---

### Dimension 4 — Agentic vs Deterministic Split

**Load `${CLAUDE_PLUGIN_ROOT}/skills/create-skill/references/script-patterns.md` before
auditing this dimension.** The same five signal patterns apply to agents as to skills.

Agents mix LLM-guided reasoning (agentic) and deterministic operations. The split should
be deliberate — see the Degrees of Freedom table in `agent-anatomy.md`.

**Violations:**
- **Inlined deterministic code:** Code blocks that would be re-generated identically across
  invocations belong in `scripts/`, not in the system prompt. *Major.*
- **Vague script references:** "Run the validation script if needed" — no path, no trigger
  condition, no output interpretation. *Minor.*

**Gaps — apply the five signal patterns to each process step:**
- **Signal 1 (Repeated Generation):** Same structure, different parameters → script. *Major.*
- **Signal 2 (Unclear Tool Choice):** Fragile multi-tool sequence → script the procedure. *Major.*
- **Signal 3 (Rigid Contract):** Can write `--help` for this step → CLI candidate. *Minor.*
- **Signal 4 (Dual-Use):** Useful outside the agent → design as proper CLI. *Minor.*
- **Signal 5 (Consistency Critical):** Must produce identical output → script, not LLM. *Major.*

---

### Dimension 5 — System Prompt Efficiency

Every line in the agent body is loaded into context every time the agent is spawned. Domain
reference data and lookup tables belong in `skills:` preloads, not embedded inline. Refer
to size invariants in `agent-anatomy.md` to calibrate severity.

**Violations:**
- **Hedging language:** "You might want to consider", "generally speaking", "you could try".
  Replace with direct imperatives. *Minor per instance.*
- **Routing guidance in body:** Any section explaining when to trigger the agent belongs in
  the `description` field. The body loads only after triggering — routing guidance there
  never informs the triggering decision and burns context on every spawn. *Major.*
- **Embedded domain reference > 100 lines:** Lookup tables, option catalogs, field
  definitions only needed for specific steps inflate every invocation. Use `skills:` preload
  instead. *Major.*
- **System prompt over ~400 lines:** Signals embedded content that belongs in `skills:`
  preloads. *Major.*

**Gaps:**
- **Could `skills:` reduce system prompt size?** Identify sections only needed for specific
  sub-tasks. *Major if system prompt > 300 lines with extractable content.*

---

### Dimension 6 — Process Completeness

A complete agent process is sequential, has explicit steps, and defines what "done" looks
like at each step. Audit for broken workflow *and* for missing structure that would help.

**Violations:**
- **No numbered steps:** Prose description of process without step numbers — the agent
  cannot track progress or know which step it's in. *Major.*
- **Steps without exit conditions:** Multi-step processes need explicit completion criteria
  per step. Without them, the agent may loop or skip prematurely. *Major if missing.*
- **Half-thought steps:** Phases that describe intent without specifying action or evaluation
  criteria. *Major per uncovered step.*
- **No input handling:** What does the agent do if input is missing, ambiguous, or malformed?
  *Minor if unaddressed.*

**Gaps:**
- **No output format section:** Callers cannot reliably consume implicit output structure.
  *Major if agent returns structured data.*
- **No validation checklist:** A self-check at the end of the process catches errors that
  prose instructions miss. *Minor.*

---

### Dimension 7 — Anatomy Completeness

Agents are typically single files, but their ecosystem includes `skills:` preloads and
optional companion scripts. This dimension asks whether declared structure matches needs.

Refer to the Gap Analysis Checklist in `agent-anatomy.md` for each absent element.

**Violations:**
- **`skills:` listed in frontmatter but not referenced in process:** Dead preloads inflate
  context on every spawn without being used. *Minor.*
- **Naming violates conventions:** Generic terms (helper, assistant, agent), underscores,
  over 50 characters, leading/trailing hyphens. *Minor.*

**Gaps:**
- **No `skills:` preload for domain-heavy agents:** System prompt embeds a large reference
  catalog that could be externalized. *Major if body > 300 lines.*
- **No companion scripts for consistency-critical steps:** Process describes steps that
  must produce identical output for identical inputs. *Major.*
- **`color` absent:** No visual identity in multi-agent UI contexts. *Minor.*
- **No `memory` for pattern-accumulating agents:** Agent reviews code, audits quality, or
  accumulates project knowledge but has no persistent memory configured. *Major if the
  agent's value increases with accumulated context.*

---

## Phase 3: Improvement Report

Present findings as a structured report. Split violations from gaps. See
`${CLAUDE_PLUGIN_ROOT}/skills/repair-agent/examples/sample-repair.md` for a complete
example of the report format and a before/after repair session.

```
AGENT IMPROVEMENT REPORT: <agent-name>
System prompt: [N] lines | Description: [N] tokens | Tools: [listed / unrestricted]

VIOLATIONS
──────────
CRITICAL
  [D1] Description does not start with "Use this agent when..." — routing model cannot
       match. Fix: rewrite opening as "Use this agent when [trigger conditions]."

MAJOR
  [D3] Body uses first-person throughout ("I will analyze...") — system prompt must be
       second-person because it is an address to the agent, not a narration of intent.
       Fix: rewrite as "Analyze the input and identify..." throughout.
  [D2] `tools` omitted for a read-only analysis agent — omission grants full tool access;
       least-privilege for autonomous agents requires an explicit allowlist.
       Fix: add tools block sequence (Read, Grep, Glob)

MINOR
  [D2] `color` not set — no visual identity in multi-agent UI.
       Fix: add color: blue (analysis/review semantic).

GAPS (what would improve this agent)
─────────────────────────────────────
MAJOR
  [D7] System prompt is 380 lines of embedded domain reference. Extract to a skill file
       and preload via `skills:` frontmatter to reduce per-spawn context cost.

MINOR
  [D6] No edge cases section — what happens when the agent receives no input?
       Improvement: add "Edge Cases: No input provided — ask the user to share the target."
```

Group violations by severity, then gaps by severity. For each: dimension code, what is
wrong or missing, the principle it falls short of, the exact fix.

Ask: "Apply all critical and major items? Or select specific ones?"

Proceed to Phase 4 when the user has indicated which items to apply.

---

## Phase 4: Apply Improvements

Apply confirmed items in order: critical violations → major violations → major gaps →
minor violations → minor gaps.

For each item:
- State what is being changed and why (principle reference, not just "you asked")
- Make the edit
- Confirm the change is consistent with surrounding content

### Explain Your Choices

After applying:
- **What was changed and why** — reference the principle: "Rewrote body as second-person
  because the system prompt is an address to the agent; first-person breaks the instruction-
  following contract"
- **What was added and why** — "Added `tools` block sequence (Read, Grep, Glob) because this
  is a read-only agent and least-privilege for autonomous execution requires an explicit allowlist"
- **What was left unchanged and why** — "Left `maxTurns` unset — task horizon is open-ended"
- **What remains for the user** — items requiring domain knowledge to fill

Phase 4 is complete when all confirmed items are applied, explanation delivered, and the
validation checklist passes.

---

## Validation

After applying all improvements:

1. Run the structural validator:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-agent/scripts/validate_agent.py \
     <agent-file> --output json
   ```
   Exit 0 = structure clean. Exit 1 = parse the `errors` array; report each entry's
   `field`, `message`, and `severity` before delivering final results.

2. Load `${CLAUDE_PLUGIN_ROOT}/skills/repair-agent/references/quality-checklist.md`
   and run the quality standards check followed by the item-by-item checklist. Report
   any failing items before delivering final results.
