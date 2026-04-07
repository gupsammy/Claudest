# Agent Anatomy Reference

Gold standard for what a well-formed Claude Code agent looks like. Load before running
any audit dimension — it is the rubric for Dimensions 3, 5, 6, and 7.

---

## Agent vs Skill — Key Structural Distinction

| Feature | Agent | Skill |
|---------|-------|-------|
| File location | `agents/<name>.md` | `skills/<name>/SKILL.md` |
| Body voice | Second-person ("You are...") | Imperative ("Analyze...") |
| Description format | "Use this agent when..." — concise `>` scalar, no `<example>` blocks | "This skill should be used when..." + trigger phrases |
| Triggering | Spawned via Agent tool; description triggers delegation | Auto-triggered by description routing |
| Context | Isolated context window | Injects into current conversation |
| Domain preloading | `skills:` frontmatter field | `references/` directory loaded on demand |

---

## Gold Standard System Prompt Structure

A complete agent system prompt has these sections in order:

```
You are [role] specializing in [domain].

**Your Core Responsibilities:**
1. [Primary responsibility — verb-led]
2. [Secondary responsibility]

**Process:**
1. [Step — imperative action + what completion looks like]
2. [Next step]

**Quality Standards:**
- [Standard with brief reasoning]

**Output Format:**
[Exact structure and content of what to return to the caller]

**Edge Cases:**
- [Situation]: [How to handle it]
```

### Persona statement

The first sentence establishes expert identity. It shapes all downstream decisions —
an agent without a persona behaves like a generic assistant rather than a specialist.

Format: "You are a [specific role] specializing in [concrete domain]."

Avoid: "You are an AI that...", "You are a helpful agent...", "You will help the user..."

### Process steps

Numbered, sequential, and imperative. Each step specifies what action to take, what
signals completion, and what to do with the result.

An agent without explicit process steps must invent its own procedure on each invocation,
producing variable behavior. Numbered steps are the agent equivalent of a skill's phases.

### Output format

Every agent that returns structured data must define the output format explicitly.
Callers — whether human or an orchestrating skill — need predictable structure to consume
results. Implicit output format produces variable results that cannot be reliably parsed.

### Edge cases

Predefined handling prevents mid-task failures that cost retries. Common edge cases:
no input provided, ambiguous input, target file missing, empty result set. Each entry
states the situation and the handling action.

---

## Voice Conventions

The system prompt is an address to the agent. Every sentence must use second-person.

| Correct (second-person) | Wrong |
|-------------------------|-------|
| "You are a security analyst..." | "I will analyze security..." |
| "Read the file the user provides." | "This agent reads the file." |
| "Identify all SQL injection risks." | "We will look for SQL injection." |
| "Your output must include..." | "The output should include..." |

**Critical — first-person:** "I will", "I'll", "I am" in a system prompt reads as the
agent narrating its own plan rather than following an instruction. The instruction-following
contract breaks.

**Major — bare imperative without "you":** "Analyze the code" reads as a skill instruction
addressed to Claude following a skill body. In an agent system prompt, use "You will
analyze the code and report findings to the caller."

---

## Size Invariants

| System prompt length | Interpretation |
|----------------------|----------------|
| Under 100 lines | Minimal agent — appropriate for focused single-step tasks |
| 100–300 lines | Standard — verify embedded content is process, not reference data |
| 300–400 lines | Review for `skills:` preload opportunities |
| Over 400 lines | Requires `skills:` deferral; embedded domain data is inflating per-spawn cost |

---

## Naming Conventions

| Rule | Detail |
|------|--------|
| Character set | Lowercase letters, digits, hyphens only |
| Length | 3–50 characters |
| Boundaries | Must start and end with alphanumeric; no consecutive hyphens |
| Avoid | Generic terms: `helper`, `assistant`, `agent` |
| Plugin scope | Plugin agents auto-namespaced as `plugin-name:agent-name` |

Good names: `code-reviewer`, `test-generator`, `sql-validator`, `api-docs-writer`
Bad names: `helper`, `assistant`, `my_agent`, `-start`, `ag`

---

## `skills:` Preload Pattern

The `skills:` frontmatter field injects full skill content into the agent's context at
spawn time. Use it to equip the agent with domain knowledge without embedding it in the
system prompt.

Use `skills:` when:
- Domain reference catalogs (option tables, field definitions) are needed during the process
- Shared conventions used by multiple agents can be centralized
- Reference data would exceed ~100 lines in the system prompt

Do not use `skills:` when:
- Domain context is under ~30 lines — embed directly; the preload overhead is not worth it
- Static one-time facts — just state them inline

Reference format in frontmatter:
```yaml
skills: agent-conventions, code-style-guide
```

---

## Gap Analysis Checklist

**Would `skills:` preloading help?**
- [ ] Does the system prompt exceed 300 lines, with significant reference tables?
- [ ] Is there domain-specific data (option catalogs, field definitions) only needed
      for specific steps, not every step?
- [ ] Do multiple agents in the same plugin share the same reference data?

**Would companion scripts help?**
- [ ] Is there a deterministic operation the agent repeats across invocations?
- [ ] Is there a step that must produce consistent (not variable) output?
- [ ] Would a user benefit from running one of the agent's steps independently from
      the terminal?

**Would `isolation: worktree` help?**
- [ ] Does the agent write files to the working tree during its process?
- [ ] Would in-progress modifications be confusing or disruptive if visible during the
      agent run?

**Would `memory` help?**
- [ ] Does the agent review code, audit quality, or accumulate project knowledge?
- [ ] Would its effectiveness increase over time with accumulated context?
- [ ] Does the system prompt include memory maintenance instructions if `memory` is set?

**Would `effort` help?**
- [ ] Is this a fast classification/extraction task that could use `effort: low`?
- [ ] Does this require deep multi-step reasoning that warrants `effort: high` or `max`?

**Would `initialPrompt` help?**
- [ ] Is this agent designed to run as a session agent via `--agent`?
- [ ] Should it self-start with a command or prompt rather than waiting for user input?

**Would `maxTurns` help?**
- [ ] Does the task have a predictable number of steps?
- [ ] Is there a risk of the agent looping on ambiguous or open-ended input?
