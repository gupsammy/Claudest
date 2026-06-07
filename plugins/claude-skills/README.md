# claude-skills ![v0.5.28](https://img.shields.io/badge/v0.5.28-blue?style=flat-square)

Skill and agent authoring tools for Claude Code. Six complementary skills: one generates skills from scratch, one generates agents, one audits skills for structural correctness, one audits agents for structural correctness, one improves skills for effectiveness, and one designs CLI interfaces for the scripts those skills produce. A bundled `skill-lint` agent runs structural linting automatically after skill creation or improvement.

## Why

Writing a good Claude Code skill is harder than it looks. The description has to be precise enough to route reliably while being dense enough to not waste tokens on every session it doesn't trigger. The body has to be tight enough for consistent outcomes but not so prescriptive that it suppresses the model's ability to generalize. Workflow steps that should be deterministic scripts get left as inline prose that the model re-generates differently on each run. And most skills that "work" are missing infrastructure — reference files for domain-specific data, scripts for fragile operations, examples for outputs users will adapt — that would make them substantially better.

These skills exist to close that gap: `create-skill` generates skills with these properties built in, `create-agent` generates Claude Code agents with proper description format and system prompt structure, `repair-skill` diagnoses structural violations in skills, `repair-agent` diagnoses structural violations in agents, `improve-skill` tests effectiveness against user goals, and `create-cli` designs the CLI interfaces for the scripts that deterministic workflow steps get extracted into.

## Installation

```
/plugin marketplace add gupsammy/claudest
/plugin install claude-skills@claudest
```

No dependencies. All skills work with whatever Claude model is running.

## Skills

### create-skill

Generate a new skill or slash command from requirements. Triggers on phrases like "create a skill", "make a command", "write a slash command", "build a Claude extension", or "add a skill to a plugin".

The skill interviews you about requirements via AskUserQuestion (or reads them from `$ARGUMENTS`) and works through a structured generation process:

Phase 0 gathers requirements: primary objective, trigger scenarios, inputs and outputs, complexity, and execution needs. Phase 1 generates the skill — it loads `frontmatter-options.md` as the authoritative source for field catalog and description patterns, applies description density rules (100-token budget, 150 max), and derives trigger phrases from the user's actual words rather than paraphrases. Step 3 (discovery validation) tests the description against should-trigger and shouldn't-trigger prompts before proceeding to body writing, catching routing failures early. The body follows progressive disclosure with imperative voice, and a script opportunity scan applies five signal patterns to identify steps that should be proper CLI tools rather than inline code blocks. When a step has a clear enough interface to write `--help` text for, it delegates to the `create-cli` skill. Phase 2 delivers the skill to the correct path, optionally packages it via `package_skill.py`, and explains every design decision. Heavyweight reference material (Degrees of Freedom table, Quality Standards, Validation Checklist) is extracted to `generation-standards.md` and loaded only at the evaluate phase, keeping the main SKILL.md lean.

### repair-skill

Audit and improve an existing skill against a gold standard. Triggers on phrases like "repair a skill", "audit a skill", "fix my skill", "improve an existing skill", "review skill quality", "check if my skill is well-written", or "what's wrong with this skill". Accepts a path to a skill directory or SKILL.md file as an argument.

The skill loads three reference files before auditing — the skill anatomy gold standard, the frontmatter options catalog, and the audit calibration guide (known false-positive patterns to avoid) — then runs seven audit dimensions:

**D1 — Frontmatter Quality.** Description person and framing, scalar type, trigger phrase authenticity and coverage, token density. Also flags missing `argument-hint` when the skill reads positional arguments. Three additional gap checks: trigger accuracy (do trigger phrases match actual user language?), token budget compliance (100-token target, 150 max), and negative triggers for adjacent skill domains.

**D2 — Execution Modifiers.** Model selection, context isolation, tool scope. Checks for both over-configuration (unrestricted `Bash`, dead tool entries, `opus` for tasks `sonnet` handles) and under-configuration (missing `AskUserQuestion` for interactive workflows, missing `context: fork` for heavy-output skills, `@$1` injection opportunities that a `Read` tool call is currently blocking).

**D3 — Intensional vs Extensional Instruction.** Identifies instruction blocks that teach by example when a stated rule would generalize better. An example requires two reasoning hops: infer the rule, then apply it. A stated rule is one hop, and it covers edge cases the examples didn't anticipate.

**D4 — Agentic vs Deterministic Split.** Applies the five script signal patterns (repeated generation, unclear tool choice, rigid contract, dual-use potential, consistency-critical operations) to every workflow step. Also flags judgment steps with no outcome criteria, and scripts that exist but aren't actionably referenced.

**D5 — Verbosity and Context Efficiency.** Redundant prose, hedging language, code blocks that collapse to one intensional rule, routing guidance in the body (dead tokens on every invocation since the body only loads post-trigger), headers deeper than H3, body exceeding 500 lines, extraneous documentation files.

**D6 — Workflow Clarity.** Phase structure, entry and exit conditions, half-thought steps, missing delivery format.

**D7 — Anatomy Completeness.** Compares the skill's directory structure against the correct tier (simple/standard/complex) and identifies absent infrastructure: missing `scripts/` for deterministic operations, missing `references/` when SKILL.md is too large, missing `examples/` for skills that produce user-adaptable output, unreferenced resource files that Claude won't load.

The output separates violations (something wrong) from gaps (something absent), each with the specific fix or addition needed. Confirmed repairs are applied in severity order with explicit reasoning for each change.

### improve-skill

Increase the effectiveness of an existing skill. Where `repair-skill` checks structural correctness against fixed rules, `improve-skill` asks whether the skill accomplishes what users actually need. Triggers on phrases like "improve a skill", "make this skill better", "add features to a skill", "what's missing from this skill", "the skill doesn't do X", or "improve how the skill works step by step".

The skill starts by understanding user intent via AskUserQuestion — establishing the specific complaint or running a full effectiveness audit if the user is unsure. It then runs five sub-analyses: mental simulation (walk through the skill as Claude with a real request, documenting stuck points, divergence points, dead ends, and friction), live doc validation (verify every factual claim — frontmatter field names, tool behavior, API parameters — against current documentation), feature adjacency scan (identify capabilities that are absent but adjacent, complementary, or needed to close end-to-end gaps), UX flow review (check whether user interaction is placed at optimal points and consequential decisions are surfaced rather than made silently), and edge case stress test (run adversarial inputs — missing files, contradictory requirements, boundary conditions — to find failure modes the skill doesn't handle).

Findings are presented grouped by outcome type — new features, UX improvements, accuracy fixes, efficiency gains — and the user selects which to apply before any edits are made.

### repair-agent

Audit and improve an existing Claude Code agent against a gold standard. Triggers on phrases like "repair an agent", "audit an agent", "fix my agent", "review agent quality", "check if my agent is well-written", "diagnose agent problems", or "what's wrong with this agent". Accepts a path to an agent file as an argument.

Where `repair-skill` audits skills, `repair-agent` handles the distinct structural contract of agent files — second-person voice, `<example>` routing blocks, `skills:` preloads, and autonomous execution safety. The skill loads two reference files before auditing: `agent-anatomy.md` (gold standard for system prompt structure, voice conventions, size invariants, naming, and the gap analysis checklist) and `agent-frontmatter.md` (the complete agent frontmatter field catalog).

The audit runs seven dimensions: D1 (Description Quality) checks "Use this agent when..." framing, `|` scalar type for XML preservation, `<example>` block count and completeness, commentary quality, and proactive trigger patterns. D2 (Frontmatter Modifiers) audits tool scope against least-privilege for autonomous execution, model cost, isolation needs, and `skills:` preload opportunities. D3 (System Prompt Voice) enforces second-person address, persona statements, numbered process steps, and output format sections. D4 (Agentic vs Deterministic Split) applies the five script signal patterns to agent process steps. D5 (System Prompt Efficiency) flags hedging language, routing guidance in the body, embedded domain reference over 100 lines, and system prompts over 400 lines. D6 (Process Completeness) checks for numbered steps, exit conditions, half-thought steps, and input handling. D7 (Anatomy Completeness) evaluates `skills:` usage, companion scripts, naming conventions, and color configuration.

The output is a structured improvement report separating violations from gaps at each severity level. After user confirmation, fixes are applied in severity order with a validation pass using `validate_agent.py` and a quality checklist.

### create-cli

Design a CLI's surface area — syntax, flags, subcommands, output contracts, error codes, and configuration — before writing implementation code. Triggers on phrases like "design a CLI", "help me design command-line flags", "what flags should my tool have", "create a CLI spec", "refactor my CLI interface", or "design a CLI my agent can call".

Based on a design by [steipete](https://github.com/steipete); this is a modified version adapted for agent-aware CLI workflows.

The skill is built around a key distinction: a CLI consumed by an agent has different requirements than one designed only for human terminal use. Agents are always non-TTY, cannot tolerate ambiguous exit codes, parse stderr as structured data, and need compound output that reduces follow-up tool calls. The skill applies that lens throughout.

Phase 1 loads `cli-guidelines.md` as the default rubric — which now includes the full agent ergonomics section (agent-aware conventions are no longer a separate file). For new CLI designs it also loads `language-selection.md`; for audits that load is skipped since the language is already chosen. Phase 2 branches on trigger intent: for new designs it asks about command name, primary consumer (agent, human, scripted automation, or mixed), input sources, output contract, interactivity needs, config model, and implementation language; for audits it collects the CLI name and source location, then uses Glob, Grep, and Bash to explore the codebase and capture actual `--help` output. Phase 3 applies agent-first conventions: explicit output modes (`--json` for structured output rather than implicit TTY sniffing — no surprises for agent callers), NDJSON for list commands to enable streaming, structured error objects on stderr with an `error` code and an executable `hint` field, consistent flag naming across subcommands so agent callers can learn patterns once, and compound output on mutating commands to avoid follow-up calls. Phase 4 delivers either a full CLI spec (new designs) or a gap report (audits of existing CLIs), including a command tree, args/flags table, output rules, error and exit code map, safety rules, config/env precedence, and worked examples. Phase 5 verifies completeness: for new specs it confirms coverage of all applicable skeleton sections and required example types; for audits it confirms the gap report addresses every audit subsection with at least one example invocation demonstrating the fix for each major finding.

`create-cli` is also called internally by `create-skill` and `repair-skill` when they identify a workflow step with a rigid enough interface to warrant a proper CLI tool rather than an inline code block.

### create-agent

Generate a well-structured Claude Code agent from requirements. Triggers on phrases like "create an agent", "make an agent", "write an agent", "build a subagent", "add an agent to a plugin", "design an autonomous agent", "generate an agent file", "write a system prompt for an agent", "what frontmatter does an agent need", or "create a specialized agent".

Agents and skills are distinct constructs with different authoring requirements. An agent runs in an isolated context window, is written in second-person ("You are..."), uses `<example>` XML blocks in its description for routing, and is spawned via the Task tool. A skill injects inline into the current conversation, uses imperative instructions for Claude to follow, and routes via trigger phrase matching. `create-agent` enforces this distinction throughout.

The skill loads `agent-frontmatter.md` as the authoritative source for agent field catalog and works through a structured generation process. Phase 0 gathers requirements: domain, expert persona, trigger conditions, proactive vs reactive firing behavior, tool access, and context isolation needs — using AskUserQuestion if `$ARGUMENTS` is empty. Phase 1 generates the agent — it applies naming validation rules (3–50 characters, lowercase alphanumeric with hyphens, no generic names like `helper` or `assistant`), writes frontmatter with minimum necessary tools on the least-privilege principle, constructs a description with 2–4 `<example>` blocks covering synonym trigger coverage, and writes the system prompt body in second person with persona, process steps, output format, and edge cases. A script opportunity scan applies the five signal patterns (loaded from a local copy of `script-patterns.md`) to every step in the system prompt. Phase 2 delivers the agent to the correct path (`~/.claude/agents/`, `.claude/agents/`, or `<plugin-root>/agents/`), explains every design decision, and scores the result across five quality dimensions (Clarity, Trigger Precision, Efficiency, Completeness, Safety) targeting 9.0/10.0.

Two helper scripts are included: `validate_agent.py` checks naming rules and required frontmatter fields, returning a structured JSON error list with severity levels; `init_agent.py` scaffolds a new agent file with placeholders at the target path.

## Agents

### skill-lint

A bundled agent that runs structural linting after skill creation or improvement. Fires proactively when `create-skill` or `improve-skill` finishes generating output, and also on explicit user requests like "lint this skill".

`skill-lint` loads three repair-skill reference files — `skill-anatomy.md`, `frontmatter-options.md`, and `audit-calibration.md` (which lists known false-positive patterns to avoid) — then runs all seven structural dimensions (D1–D7) against the skill. D1 includes expanded sub-checks for trigger accuracy, token budget compliance (100-token target, 150 max), and negative trigger coverage for adjacent domains. The agent auto-applies all critical and major fixes without asking, then presents minor findings for user decision. This separates structural correctness (skill-lint's domain) from effectiveness analysis (improve-skill's domain) — the two concerns that most often get conflated in manual skill review.

The agent is scoped to `Read`, `Glob`, `Grep`, `Edit`, `Write`, and `AskUserQuestion` only, with a 15-turn limit. It explicitly declines to lint agent files (AGENT.md), which have a different structural contract.

## Reference Library

The skills share a `references/` library that they load during their workflows.

`skill-anatomy.md` defines the gold standard at each complexity tier, the three-level loading model (metadata always loaded, SKILL.md on trigger, resources on demand), directory type definitions with when-to-use criteria, the Degrees of Freedom table mapping task fragility to instruction specificity, and a Gap Analysis Checklist for identifying what a skill would benefit from adding.

`frontmatter-options.md` is the complete catalog of valid frontmatter fields, all valid values per field, the full tool list with blast-radius notes, and a tool selection framework with tier table and rationale. Includes description density rules (100-token budget, 150 max), trigger derivation principles, and negative trigger guidance. Loaded before any frontmatter or execution modifier audit. A separate copy exists in `create-skill/references/` with generation-specific additions.

`script-patterns.md` covers the five signal patterns for recognizing script and CLI candidates, CLI design conventions for skill context (argument structure, output format, exit codes, help text), five script archetypes (init/validate/transform/package/query) with canonical argument patterns, the Python script template, wiring rules for referencing scripts from SKILL.md, and the delegation pattern to `create-cli` for non-trivial interface design. A local copy exists in `create-agent/references/` to avoid cross-skill dependency.

`generation-standards.md` (in `create-skill/references/`) contains the Degrees of Freedom table, Quality Standards, Validation Checklist, and Error Handling rules extracted from the main create-skill body. Deferred-loaded at the evaluate phase to keep the primary SKILL.md lean.

`audit-calibration.md` (in `repair-skill/references/`) lists known false-positive patterns that the seven audit dimensions should avoid flagging. Loaded by `skill-lint` and `repair-skill` before running any dimension.

`agent-frontmatter.md` (in `create-agent/references/`) is the complete catalog of valid agent frontmatter fields — tools, disallowedTools, model, color, permissionMode, isolation, background, maxTurns, skills, memory — with color semantics and tool selection guidance specific to agents. Loaded by `create-agent` and `repair-agent` before generating or auditing frontmatter.

`agent-anatomy.md` (in `repair-agent/references/`) defines the gold standard for agent system prompt structure, voice conventions, size invariants, naming rules, `skills:` preload patterns, and the gap analysis checklist. Loaded by `repair-agent` for Dimensions 3, 5, 6, and 7.

`quality-checklist.md` exists in both `repair-skill/references/` (for skill validation) and `repair-agent/references/` (for agent validation), each tailored to their respective structural contracts. Loaded after fixes are applied for final validation.

`language-selection.md` (in `create-cli/references/`) covers implementation language selection for CLI tools: Go, Python, Node.js, Rust, and shell, with recommendations based on distribution model, runtime dependency tolerance, parsing library options, and agent-use suitability. Loaded by `create-cli` during Phase 2 for new designs only; skipped for audits.

`cli-guidelines.md` (in `create-cli/references/`) is the primary CLI rubric, now incorporating the agent ergonomics section that was previously a separate `agent-aware-design.md` file. Loaded during Phase 1 for both new designs and audits.

## Architecture Note

The skills in this plugin are regular directories shipped with the plugin. No symlinks or external sync needed — updates are delivered through the plugin version.

## License

MIT
