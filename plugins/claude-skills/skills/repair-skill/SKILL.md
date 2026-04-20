---
name: repair-skill
description: >
  This skill should be used when the user asks to "fix my skill" or "audit this skill".
  Make sure to use this skill whenever the user mentions skill quality, structural issues,
  broken skills, or skill diagnostics — even if they don't explicitly say "repair-skill".
  Not for adding features or improving effectiveness — use improve-skill. Not for agents
  — use repair-agent.
argument-hint: <path-to-skill-directory-or-SKILL.md>
---

# Skill Repair

Audit and improve an existing skill against a gold standard. Unlike create-skill (which
generates from scratch), this skill diagnoses violations *and* identifies gaps — what is
broken, what is missing, and what would raise quality. The output is a structured
improvement plan covering all dimensions.

## Phase 1: Load the Skill

Read `$ARGUMENTS` as the path to a skill directory or SKILL.md file.

- If a directory: read `SKILL.md`, then list and note which of `references/`, `examples/`,
  `scripts/`, `assets/` exist and which are referenced from SKILL.md
- If a file: read it directly, then discover sibling resource directories

If the path is missing or ambiguous, use AskUserQuestion to resolve before proceeding.

**Load all three reference files before Phase 2:**

1. `${CLAUDE_PLUGIN_ROOT}/skills/repair-skill/references/skill-anatomy.md` — gold standard for
   correct anatomy, three-level loading model, directory type definitions, degrees of
   freedom, naming conventions, body conventions. Required for Dimensions 5, 6, and 7.
2. `${CLAUDE_PLUGIN_ROOT}/skills/repair-skill/references/frontmatter-options.md` — complete
   frontmatter field catalog, valid values, tool list, tool selection framework.
   Required for Dimensions 1 and 2.
3. `${CLAUDE_PLUGIN_ROOT}/skills/repair-skill/references/audit-calibration.md` — known
   false-positive patterns that look like violations but are not. Prevents over-flagging
   on D2 (allowed-tools absent), D4 (Task/Skill prose), and D5 (orientation vs routing).

Proceed to Phase 2 when: SKILL.md is read, sibling directories are cataloged, and all
three reference files are loaded.

## Phase 2: Audit

Run each dimension independently. For each finding record: the dimension code, what is
wrong or missing, which principle it violates or which gold standard it falls short of,
and the specific change required. Proceed to Phase 3 when all 7 dimensions are evaluated.

**Finding types:**
- **Violation** — something present that contradicts a rule
- **Gap** — something absent that would improve the skill against the gold standard
- **Improvement** — something that works but could be meaningfully tightened

**Severity:**
- **critical** — breaks triggering or wastes significant context on every invocation
- **major** — degrades generalization, reliability, or workflow correctness
- **minor** — polish; the skill works but isn't as good as it could be

---

### Dimension 1 — Frontmatter Quality

The description is the only part of a skill that is always in context. Every token here
costs budget across every session. Audit for violations *and* gaps:

**Violations:**
- **Person and framing:** Is the description third-person ("This skill should be used
  when...")? First-person or imperative framing reads as an instruction to execute, not a
  triggering condition to evaluate. *Critical if wrong.*
- **Scalar type:** Does the description use `>` (folded scalar)? The `|` literal scalar
  preserves newlines and can produce unexpected whitespace when parsed. *Minor.*
- **Trigger phrase authenticity:** Are quoted phrases verbatim user speech — the exact
  tokens a user would type? Paraphrases ("hook creation tasks") have lower routing match
  rates than natural language ("create a hook"). *Major if paraphrased.*
- **Token density:** Does the description restate the skill name, explain what skills are,
  or include meta-commentary? Every such token is budget waste. *Minor per instance,
  major if systemic.*

**Gaps:**
- **Trigger phrase coverage:** Are there 3–5+ varied phrases? Single-phrase descriptions
  miss synonym space. Does coverage include the naive phrasing a user would use who has
  never heard of this skill? *Major if sparse.*
- **Missing `argument-hint`:** Does the skill read `$ARGUMENTS` or `$1`/$`$2` without an
  `argument-hint` field? The hint is shown in autocomplete — its absence means users don't
  know what to pass. *Minor.*
- **Name validity:** Is the skill name lowercase, hyphens only, max 64 chars? Verb-led for
  commands? Namespaced when it aids routing clarity? These constraints ensure filesystem
  compatibility, command-line ergonomics, and unambiguous routing. *Minor if wrong.*
- **Trigger accuracy:** Mentally generate 3 prompts that should trigger this skill and 3
  that should NOT (from adjacent domains). Does the description cover the should-triggers
  and exclude the shouldn't-triggers? Sparse coverage or broad false-trigger surface is a
  routing quality gap. *Major if coverage is sparse.*
- **Token budget:** Is the description over 100 tokens? Per-session cost scales with
  description length across all installed skills. Over 150 tokens is a violation (major);
  100–150 is a gap (minor) — tighten by prioritizing trigger phrases over prose.
- **Negative triggers absent:** For skills in crowded domains (multiple skills with
  overlapping concerns), does the description include explicit "Not for X" exclusions?
  Negative triggers sharpen the routing decision boundary. *Minor.*

---

### Dimension 2 — Execution Modifiers

Modifiers left at their defaults are not errors — omitting them is correct when defaults
apply. Audit for *mismatches* (violations) and *missing configuration* (gaps).

Refer to `frontmatter-options.md` for the complete field catalog, model selection table,
and tool selection framework.

**Violations:**
- Does the skill have unrestricted `Bash` when a scoped pattern (`Bash(git:*)`) would work?
- Does the skill have tools in `allowed-tools` it never uses? Dead entries add noise.

**Gaps:**
- Does the skill invoke other skills or spawn agents without `Skill` or `Task` in `allowed-tools`?
- Does the skill require user decisions mid-workflow but lacks `AskUserQuestion` in `allowed-tools`?
- Does the skill read a file path from `$1` but uses a `Read` tool call instead of `@$1`
  inline injection? A tool round-trip is being wasted. *Minor.*
- Could real-time data (git status, env vars, file tree) be injected using dynamic content
  syntax (bang + backtick-wrapped command) instead of a tool call? *Major when the skill's
  workflow begins with infallible probes (git branch, file tree, env vars) that never need
  error handling; Minor for commands that may fail or need exit-code branching.*

  **Before (wastes tool round-trips):**
  ```
  1. Run `git log --oneline -5` using Bash
  2. Run `git diff --name-only` using Bash
  3. Analyze the results...
  ```
  **After (injected at invocation, zero tool calls).** Replace those Bash calls with:

  - Recent commits: !\`git log --oneline -5\`
  - Changed files: !\`git diff --name-only\`

  Then continue with prose like "Analyze the results..." (The backslashes escape
  the backticks so this documentation doesn't execute — in a real skill, write
  !\`cmd\` without the backslashes.)

---

### Dimension 3 — Intensional vs Extensional Instruction

A rule stated with its reasoning generalizes to every input. An example that implies a
rule requires the reading model to reverse-engineer the rule — two reasoning hops instead
of one, covering only the shape of that example.

**Violations:**
- Does it show a good/bad contrast and leave the principle implicit? The principle should
  be stated first; the contrast confirms it, not carries it.
- Is a "Common Mistakes" or "Bad/Good examples" section doing the work that a single
  principle sentence could do more efficiently? *Major.*
- Would removing the examples leave the rule intact and still actionable? If yes, the
  examples are redundant. If no, the rule hasn't been stated yet — state it.

**Gaps:**
- Are there instruction blocks that tell Claude *what* to do but not *why*? Adding the
  reasoning makes the instruction generalize to edge cases not covered by the current
  examples. *Major per uncovered block.*

---

### Dimension 4 — Agentic vs Deterministic Split

**Load `${CLAUDE_PLUGIN_ROOT}/skills/create-skill/references/script-patterns.md` before auditing this dimension.** It contains
the five signal patterns for recognizing a script candidate, CLI design conventions,
common archetypes (init, validate, transform, package, query), and the delegation
pattern for using `create-cli` to design the interface.

Skills mix LLM-guided reasoning (agentic) and script execution (deterministic). The split
should be deliberate — see the Degrees of Freedom table in `skill-anatomy.md`.

**Violations:**
- **Code blocks that are repeated or identical across invocations** — these are
  deterministic operations being re-generated each time. They belong in `scripts/`.
  Inlining costs context tokens on every run; scripts execute without being loaded.
- **Prose that describes a deterministic sequence** — if the steps are always the same
  regardless of input, a script is more reliable than asking the model to reproduce them.
- **Scripts that exist but aren't referenced in SKILL.md** — Claude won't use them.
  A script without a reference in SKILL.md specifying when and how to invoke it is
  invisible to the skill workflow. *Major.*
- **Vague script references** — "run the validation script if needed" is not actionable.
  References must state the trigger condition, the exact invocation, and how to interpret
  the output. *Minor.*

**Gaps — apply the five signal patterns from `script-patterns.md` to each workflow step:**
- **Signal 1 (Repeated Generation):** Does any step produce the same structure with
  different parameters across invocations? → Parameterized script candidate. *Major.*
- **Signal 2 (Unclear Tool Choice):** Does any step require combining multiple standard
  tools in a fragile sequence to accomplish something naturally expressible as a single
  function? → Script the procedure. *Major.*
- **Signal 3 (Rigid Contract):** Does any step have an input/output shape clear enough
  to write `--help` text for right now? → CLI candidate; delegate design to `create-cli`.
- **Signal 4 (Dual-Use Potential):** Would any step be useful to run independently from
  the terminal, outside the skill workflow? → Design as proper CLI from the start.
- **Signal 5 (Consistency Critical):** Does any step need to produce identical output
  for identical inputs — not "similar" but reproducible? → Script, not LLM generation.
- **Judgment steps with no criteria** — "analyze the situation" is agentic but unanchored.
  Agentic steps need explicit criteria for what to consider and what constitutes a good
  outcome. *Major per uncovered step.*

---

### Dimension 5 — Verbosity and Context Efficiency

Every token in SKILL.md is loaded into context when the skill triggers. Audit for tokens
that consume budget without improving outcomes, *and* for content that belongs in
`references/` instead.

Refer to the size invariants table in `skill-anatomy.md` to calibrate severity.

**Violations:**
- **Prose that restates the section header** — "## Validation" followed by "In this
  section we will validate..." is pure redundancy. *Minor per instance.*
- **Hedging language** — "you might want to consider", "it could be useful to",
  "generally speaking". Replace with direct imperatives or remove. *Minor per instance.*
- **Code blocks illustrating a principle stateable in one sentence** — a good/bad YAML
  contrast often collapses to one intensional rule. *Major if pattern is frequent.*
- **Code blocks collapsed to prose that lose variable bindings** — a code block that
  assigns workflow variables (`BASE=...`, `BRANCH=...`) used by later steps serves two
  purposes: illustrating the operation AND establishing state. Collapsing it to prose
  without preserving the bindings leaves downstream `$VAR` references unbound. When
  collapsing, add a "derive working variables" preamble that explicitly binds each
  variable in prose. *Major per lost binding.*
- **Repeated guidance across sections** — the same rule in a "Best Practices" section and
  a "Common Mistakes" section. Consolidate to one location. *Minor.*
- **"When to Use This Skill" section in the body** — body loads only after triggering;
  routing guidance here is never read by the routing decision. Dead tokens every invocation.
  *Major.*
- **Headers deeper than H3** — signals content that belongs in `references/`. *Minor.*
- **SKILL.md over ~500 lines** — requires `references/` deferral. *Major.*
- **Extraneous documentation files** (`README.md`, `CHANGELOG.md`, `INSTALLATION.md`) in
  the skill directory — never loaded into context, add noise to the package. *Minor per file.*

**Gaps:**
- **Would a `references/` file reduce SKILL.md size?** Identify sections only needed for
  specific sub-tasks and flag them as deferral candidates. *Major if SKILL.md > 300 lines.*
- **Would a `references/` file for domain-specific data help?** Lookup tables, option
  catalogs, field definitions — these are reference data, not instructions. *Major.*

---

### Dimension 6 — Workflow Clarity

A skill's process should be sequential, complete, and have explicit exit conditions at
each phase. Audit for broken workflow *and* for missing structure that would help.

**Violations:**
- Is the process structured as numbered phases with clear names? Without explicit phases
  the model can't track progress or know which step it's in. *Major if unstructured.*
- Does each phase have an explicit exit condition? Without one, the model doesn't know
  when to stop iterating on a phase and may loop or skip prematurely. *Major if missing.*
- Are there half-thought steps — phases that describe intent without specifying what to
  do or how to evaluate the result? *Major per uncovered phase.*
- Does the skill handle missing, ambiguous, or malformed input?

**Gaps:**
- **Variable continuity:** Does every `$VAR` referenced in a step have an explicit binding
  in an earlier step or a pre-flight/preamble section? Scan all `$VARNAME` tokens in the
  skill body and trace each back to its origin. An unbound variable is a workflow break —
  the agent either halts on an invalid command or silently substitutes an empty string.
  *Major per unbound variable.*
- Is there a delivery phase that tells Claude what to produce and in what format? Many
  skills describe the process clearly but leave the output format implicit. *Major if absent.*
- Would a validation checklist at the end of the workflow catch errors that prose
  instructions miss? *Minor.*
- Would an `examples/` directory help users understand what the expected output looks
  like? *Minor.*

---

### Dimension 7 — Anatomy Completeness

Refer to `skill-anatomy.md` for the gold standard directory anatomy and the Gap Analysis
Checklist. This dimension asks: does the skill's structure match its complexity tier, and
what is absent that would raise it?

**Use the Gap Analysis Checklist from `skill-anatomy.md` directly.** For each "yes"
answer, record a gap at the appropriate severity.

**Violations:**
- Does the skill have a `scripts/` directory with scripts not referenced in SKILL.md?
  *Major — referenced or delete.*
- Does the skill have a `references/` directory with files not pointed to from SKILL.md?
  *Major — referenced or delete.*
- Does the naming violate conventions (uppercase, underscores, over 64 chars)? *Minor.*

**Gaps — ask for each absent directory:**
- **Missing `scripts/`:** Is there a deterministic operation that would be more reliable
  scripted? Does the same code block appear or would it appear in multiple invocations?
- **Missing `references/`:** Does SKILL.md exceed 300 lines? Are there sections only
  needed for specific sub-tasks? Is there domain-specific reference data?
- **Missing `examples/`:** Does the skill produce output users adapt? Are there ambiguous
  instructions a working example would clarify better than prose?
- **Missing resource pointers in SKILL.md:** Are there directories present but not
  referenced — invisible to Claude unless it guesses to look?

---

## Phase 3: Improvement Report

Present findings as a structured report. Split violations from gaps — a violation is
something wrong, a gap is something missing that would improve the skill.

```
SKILL IMPROVEMENT REPORT: <skill-name>
Current tier: [simple / standard / complex] — [lines] lines, [directories present]

VIOLATIONS
──────────
CRITICAL
  [D1] Description uses first-person — routing model reads as instruction, not trigger.
       Fix: rewrite as "This skill should be used when the user asks to..."

MAJOR
  [D3] Body teaches frontmatter quality by bad/good contrast; principle never stated.
       Fix: state the rule ("quoted phrases must be verbatim user speech because routing
       matches on literal tokens") then keep the contrast as confirmation.
  [D5] "When to Use This Skill" section in body — dead tokens every invocation.
       Fix: move routing guidance to frontmatter description, delete body section.

MINOR
  [D1] Description uses | scalar instead of >.
       Fix: change to >.

GAPS (what would improve this skill)
─────────────────────────────────────
MAJOR
  [D7] SKILL.md is 420 lines with no references/ directory. Three sections (option catalog,
       field definitions, examples table) are only needed for specific sub-tasks.
       Improvement: extract to references/; add load pointer in SKILL.md for each.
  [D4] File-path validation logic is inlined but must produce consistent output.
       Improvement: move to scripts/validate-input.py; reference from Phase 2.

MINOR
  [D2] Skill reads $1 as a file path but uses Read tool — @$1 injection would save a
       tool round-trip.
       Improvement: replace Read call with @$1 inline injection.
  [D7] No examples/ directory; skill produces config output users adapt.
       Improvement: add examples/ with one representative output file.
```

Group violations by severity, then gaps by severity. For each: dimension code, what is
wrong or missing, the principle or gold standard it falls short of, the exact fix.

Ask: "Apply all critical and major items? Or select specific ones?"

---

## Phase 4: Apply Improvements

Apply confirmed items in order: critical violations → major violations → major gaps →
minor violations → minor gaps.

For each item:
- State what is being changed or added and why (principle reference, not just "you asked")
- Make the edit or create the file
- Confirm the change is consistent with surrounding content

### Explain Your Choices

After applying improvements, briefly explain:
- **What was changed and why** — reference the principle: "Rewrote description as
  third-person because first-person framing is parsed as an instruction to execute, not a
  triggering condition to evaluate"
- **What was added and why** — "Created references/options.md and deferred the option
  catalog because SKILL.md was 420 lines and the catalog is only needed for the
  configuration sub-task"
- **What was left unchanged and why** — "`hooks` left unset — no lifecycle validation needed"
- **What remains for the user to address** — "The examples/ gap requires domain knowledge
  to fill; a placeholder directory was created"

Phase 4 is complete when all confirmed items are applied, the explanation is delivered,
and the validation checklist passes.

---

## Validation

After applying all improvements, load `${CLAUDE_PLUGIN_ROOT}/skills/repair-skill/references/quality-checklist.md`
and run the quality standards check followed by the item-by-item validation checklist.
Report any failing items before delivering final results.
