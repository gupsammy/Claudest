---
name: create-skill
description: >
  This skill should be used when the user asks to "create a skill" or "make a command".
  Make sure to use this skill whenever the user mentions skill creation, command authoring,
  slash commands, or building Claude extensions — even if they don't explicitly say
  "create-skill". Not for repairing or auditing existing skills — use repair-skill.
argument-hint: "[skill|command] [name] - or leave empty to interview"
---
# Skill & Command Generator

Generate well-structured skills or slash commands. Both are markdown files with YAML frontmatter—they share the same structure but differ in how they're triggered and described.

## Phase 0: Understand Requirements

Parse `$ARGUMENTS` for type hint. If `$ARGUMENTS` is empty or insufficient, use AskUserQuestion to gather requirements — users are often unclear on what type of artifact they need or what the best design is.

Use AskUserQuestion to collect:
1. **Primary objective** — What should this do?
2. **Trigger scenarios** — When should it activate?
3. **Inputs/outputs** — What does it receive and produce?
4. **Complexity** — Simple, standard, or complex?
5. **Execution needs** — Isolated context? Delegated to specialized agent?
Proceed to Phase 1 when at minimum Objective and Trigger Scenarios are established. Remaining dimensions can be resolved during generation.

## Phase 1: Generate

Apply these principles throughout generation: use imperative voice and terse phrasing because every token in a generated skill body costs budget on every invocation, and Claude extrapolates well from precise nudges. Prefer instruction over example — state the rule with its reasoning so it generalizes to every input.

**If creating a new skill directory (not editing an existing file):**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-skill/scripts/init_skill.py <name> --path <dir> [--resources scripts,references,assets] [--examples]
```

Exit 0 = directory created, proceed to Step 1.
Exit 1 = naming collision; ask user whether to overwrite or rename.

### Step 1 — Choose type

- **Skills:** Trigger-rich, third-person description ("This skill should be used when..."); auto-triggered by routing
- **Commands:** Concise, verb-first description, under 60 chars; user-invoked via `/` menu

### Step 2 — Write frontmatter

Read `${CLAUDE_PLUGIN_ROOT}/skills/create-skill/references/frontmatter-options.md` for the full field catalog, description patterns, tool selection framework, and execution modifiers.

**Description density rules:** Keep descriptions under 150 tokens (200 absolute max) — they load every session. Descriptions over 250 characters are truncated in the skill listing. Derive trigger phrases from the user's actual words in Phase 0, not paraphrases. See the token budget, pushy routing pattern, and trigger derivation principles in `frontmatter-options.md`.

**Intensional over extensional — apply to all generated content.** State the rule directly with its reasoning rather than listing examples that imply the rule. An intensional rule ("quoted phrases must be verbatim user speech *because* routing matches on literal tokens") generalizes to every input the skill will encounter. An extensional approach requires the reader to reverse-engineer the rule — two reasoning hops instead of one, covering only the shape of those specific examples. Since this skill generates instructions that will themselves guide further generation, the quality of reasoning propagates.

### Step 3 — Validate description discoverability

Before writing the body, verify the description will route correctly. Mentally generate:

1. **3 should-trigger prompts** — realistic user messages that should activate this skill. Include at least one naive phrasing from a user who has never heard of the skill.
2. **3 should-NOT-trigger prompts** — messages in adjacent domains that are close but should not activate. These test whether the description is too broad.

Evaluate: does the description cover all should-trigger prompts? Would it plausibly reject the should-NOT-trigger prompts? If coverage is weak, revise the description — add missing trigger phrases, tighten language to exclude adjacent domains, or add a negative trigger ("Not for X").

This step catches routing misses before the rest of the skill is built. Proceed when description coverage is adequate.

### Step 4 — Write body

**Construction rules:**
- State objective explicitly in first sentence
- Use imperative voice ("Analyze", "Generate", "Identify") — no first-person ("I will", "I am")
- Context only when necessary for understanding
- XML tags only for complex structured data
- No "When to Use This Skill" section — body loads only after triggering; routing guidance there is never read by the routing decision
- Avoid headers deeper than H3 — deep nesting signals content that belongs in `references/`, not `SKILL.md`
- Use bang-backtick syntax for dynamic context injection when real-time data (git status, file list, env vars) improves the skill without requiring a tool call
- **Preserve variable bindings when collapsing code blocks to prose.** Code blocks serve
  two purposes: illustrating an operation and establishing workflow state. When a code
  block assigns variables (`BASE=...`, `BRANCH=...`) that later steps reference, collapsing
  it to prose without preserving the bindings leaves downstream `$VAR` references unbound.
  Add a "derive working variables" preamble that explicitly binds each variable in prose
  before the steps that use them

Both skills and commands follow the same body pattern:

```markdown
# Name

Brief overview (1-2 sentences).

## Process
1. Step one (imperative voice)
2. Step two
3. Step three
```

**Dynamic Content:**

| Syntax | Purpose |
|--------|---------|
| `$ARGUMENTS` | All arguments as string |
| `$1`, `$2`, `$3` | Positional arguments (shell-style quoting for multi-word values) |
| `@path/file` | Load file contents |
| `@$1` | Load file from argument |
| Exclamation + backticks | Execute bash command, include output |
| `${CLAUDE_SKILL_DIR}` | Path to the skill's own directory (use for referencing bundled scripts/files) |
| `${CLAUDE_SESSION_ID}` | Current session ID (useful for logging or session-specific output files) |

**Example — injecting live context.** In a skill body, write lines like:

- Current branch: !\`git branch --show-current\`
- Recent commits: !\`git log --oneline -5\`
- Changed files: !\`git diff --name-only\`

Then add directives like "Summarize this pull request..."

These commands run when the skill is invoked. The model sees only the output — no tool calls needed. Use this for infallible probes (git status, env vars, file trees, process output) where failure is rare and the output is informational. Do not use for commands that may fail or need exit-code branching — those require Bash tool calls so the model can handle errors.

> **Note:** The backslashes above escape the backticks so this documentation
> doesn't execute — in a real skill, write !\`cmd\` without the backslashes.

### Step 5 — Script opportunity scan

Read `${CLAUDE_PLUGIN_ROOT}/skills/create-skill/references/script-patterns.md` and apply the five signal patterns to every workflow step in the skill being generated:

| Signal | Question | If yes → |
|--------|----------|----------|
| **Repeated Generation** | Does any step produce the same structure with different params across invocations? | Parameterized script in `scripts/` |
| **Unclear Tool Choice** | Does any step combine multiple tools in a fragile sequence naturally expressible as one function? | Script the procedure |
| **Rigid Contract** | Can you write `--help` text for this step right now without ambiguity? | CLI candidate — delegate design to `create-cli` |
| **Dual-Use Potential** | Would a user want to run this step from the terminal, outside the skill workflow? | Design as proper CLI from the start |
| **Consistency Critical** | Must this step produce bit-for-bit identical output for identical inputs? | Script — never LLM generation |

For each identified script candidate:
1. Choose the archetype from `references/script-patterns.md` (init/validate/transform/package/query)
2. If the interface is non-trivial, delegate to `claude-skills:create-cli` skill to design it
3. Scaffold the script in `scripts/` using the Python template from `references/script-patterns.md`
4. Wire it into SKILL.md with: trigger condition, exact invocation, output interpretation

**Wiring rule:** A script reference must state *when* to invoke (trigger condition), *how* to invoke (exact command with flags), and *what to do* with the result (exit code handling, which output fields matter).

### Step 6 — Check delegation

Scan for existing resources before finalizing:

```
Review available: skills, commands, agents, MCPs
For each workflow step, ask: "Do we already have this?"
```

**Common delegation patterns:**
- Git commits → `Skill: claude-coding:commit`

**Always use fully qualified names:**
- `Skill: plugin-dev:hook-development` (not just "hook-development")
- `SlashCommand: /plugin-dev:create-plugin` (not just "create-plugin")
- `Task: subagent_type=plugin-dev:agent-creator`

### Step 7 — Validate

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-skill/scripts/validate_skill.py <skill-directory> --output json
```

Exit 0 = proceed to Phase 2. Exit 1 = parse the `errors` array; each entry has `field`, `message`, `severity`. Resolve all `critical` and `major` items before writing to disk.

## Phase 2: Deliver

### Output Paths

| Type | Location |
|------|----------|
| User skill | `~/.claude/skills/<name>/SKILL.md` |
| User command | `~/.claude/commands/<name>.md` |
| Project skill | `.claude/skills/<name>/SKILL.md` |
| Project command | `.claude/commands/<name>.md` |

### Write and Confirm

Before writing:
```
Writing to: [path]
This will [create new / overwrite existing] file.
Proceed?
```

### Explain Your Choices

When presenting the generated skill/command to the user, briefly explain:
- **What you set and why** — "Added `allowed-tools` to scope Bash to git commands only, since the skill only needs git for commits"
- **What you excluded and why** — "`hooks` omitted (no validation needed), `disable-model-invocation` left unset (auto-triggering is appropriate)"
- **Add more trigger phrases if routing misses expected inputs**

### Package for Distribution

Only when user explicitly requests a distributable file, run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/create-skill/scripts/package_skill.py <skill-directory> [output-dir]
```

Exit 0 = `.skill` file created at output path. Exit 1 = validation failed; read stdout for details.

### After Creation

Summarize what was created:
- Name and type
- Path
- How to invoke/trigger
- Suggested test scenario

## Phase 3: Structural Lint

After writing the skill to disk, invoke the skill-lint agent to run a structural audit:

```
Use Task tool with subagent_type=claude-skills:skill-lint:
"Lint the skill at <path-to-skill-directory>. Auto-apply critical and major fixes, report
minor findings for user decision."
```

Wait for the agent to complete. If it auto-applied fixes, note them in the Phase 4 summary.
If it reports minor findings, include them in the evaluation output for the user to decide.

Proceed to Phase 4 when the lint agent returns.

## Phase 4: Evaluate

Score the generated skill/command:

| Dimension | Criteria |
|-----------|----------|
| **Clarity (0-10)** | Instructions unambiguous, objective clear |
| **Precision (0-10)** | Appropriate specificity without over-constraint |
| **Efficiency (0-10)** | Token economy—maximum value per token |
| **Completeness (0-10)** | Covers requirements without gaps or excess |
| **Usability (0-10)** | Practical, actionable, appropriate for target use |

**Target: 9.0/10.0.** If below, refine once addressing the weakest dimension, then deliver.

Before finalizing, load `${CLAUDE_PLUGIN_ROOT}/skills/create-skill/references/generation-standards.md` and verify the validation checklist passes.

---

Execute phases sequentially.
