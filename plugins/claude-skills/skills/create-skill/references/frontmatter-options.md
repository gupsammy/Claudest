# Frontmatter Options & Patterns Reference

**Authoritative source for skill/command frontmatter.** Keep current with Claude Code
releases — this file is the single source of truth used by create-skill. No live
documentation fetch is performed; accuracy depends on this file being maintained.

Load before writing frontmatter in Phase 1, Step 2. Contains the full field catalog,
description patterns, execution modifiers, tool selection framework, and progressive
disclosure patterns.

---

## Common Frontmatter Options

```yaml
---
name: identifier                    # Required for skills
description: >                      # How it's described/triggered
  [See description patterns below]

# Tool access
allowed-tools:                      # Restrict available tools
  - Read
  - Grep
  - Bash(git:*)

# Lifecycle hooks (optional, scoped to this skill's lifetime)
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "scripts/validate-input.sh"
          timeout: 10
          statusMessage: "Validating..."
  PostToolUse:
    - hooks:
        - type: command
          command: "scripts/cleanup.sh"
  Stop:
    - hooks:
        - type: command
          command: "scripts/on-complete.sh"
          once: true                # Skills only: run once, then auto-remove

# Execution context
context: fork                       # Run in a subagent (isolates from conversation)
agent: Explore                      # Subagent type when context: fork (default: general-purpose)
effort: high                        # Override session effort: low | medium | high | max (Opus only)
paths: "*.py,src/**/*.ts"           # Glob patterns limiting auto-activation to matching files
shell: bash                         # Shell for !`cmd` blocks: bash (default) or powershell

# Behavior modifiers
user-invocable: true                # Show in /command menu (default true)
disable-model-invocation: true      # Prevent programmatic invocation
argument-hint: "[arg1] [arg2]"      # Document expected arguments; quote if value contains [...]
---

**`argument-hint` quoting rule:** Values containing `[...]` must be quoted (`"[arg]"`), because YAML treats unquoted `[` as the start of a flow sequence. Values using only `<...>` do not need quoting.
```

**Hooks structure:** Each hook event (PreToolUse, PostToolUse, Stop, SessionStart, etc.)
accepts an array of entries. Each entry can have a `matcher` (filter by tool name) and a
`hooks` array with handlers. Handler fields: `type` (command, http, prompt, agent),
`command`, `timeout` (seconds), `statusMessage` (custom spinner text), `once` (skills
only — run once then auto-remove). Hooks are scoped to the skill's lifetime and cleaned
up when it finishes.

---

## Description Patterns

### For Skills (auto-triggered) — principles

- **Third-person framing is a routing signal, not a stylistic choice.** The routing model evaluates the description as a triggering condition. First-person ("Use this skill when...") reads as an instruction to execute. Third-person ("This skill should be used when...") reads as a condition to test. The framing changes how the model interprets the field.
- **Quoted phrases must be verbatim user speech.** Routing matches on literal token patterns. Write the exact words a user would type, not paraphrases: `"create a hook"` triggers correctly; `"hook creation workflows"` may not.
- **The description is always in context, even when the skill isn't active.** Every session pays the token cost of every skill's description. Density matters: cover more trigger patterns in fewer words. Avoid restating the skill name or explaining what skills are.
- **Cover the naive phrasing.** A user who doesn't know this skill exists won't search for it by name — they'll describe their problem in plain language. Include the phrasing someone would use who has never heard of this skill.
- **Include negative triggers for adjacent domains.** Routing is a classification problem — explicit exclusions sharpen the decision boundary. Add "Not for X" or "Don't use for Y" when the skill could plausibly false-trigger on a related but distinct domain.
- **3–5 trigger phrases minimum.** Single-phrase descriptions have high miss rates. Varied phrases improve routing coverage across synonym space.
- **Derive trigger phrases from user language.** Pull phrases from how the user actually described their need during requirements gathering, not from formalized or paraphrased versions. If the user said "fix my skill," use "fix my skill" — not "skill remediation." When no user phrasing is available, imagine the most natural way someone would describe this need without knowing the skill exists.
- **Err toward overtriggering, not undertriggering.** Claude tends to undertrigger skills — to not invoke them when they'd be useful. After the core verbatim phrases, append a routing directive using intent categories: "Make sure to use this skill whenever the user mentions [X, Y, Z] — even if they don't explicitly say '[skill name]'." X/Y/Z should be intent categories and concept words (broad, generalizable), not verbatim query phrases (which overfit and bloat context). The core description uses verbatim phrases (optimized for recall); the routing suffix uses category words (broad, anti-overfit). These two layers are not interchangeable.
- **Keep descriptions under 150 tokens (200 absolute max).** Anthropic's hard limit is 1024 characters (~250 tokens). Descriptions longer than 250 characters are truncated in the skill listing. The routing suffix from the overtriggering rule adds ~20-30 tokens — the raised budget accommodates it. Prioritize trigger phrases over explanatory prose — the description's job is routing, not documentation.
- **Use `>` scalar, not `|`.** Folded scalar (`>`) collapses newlines to spaces, producing a single continuous string — correct for descriptions. Literal scalar (`|`) preserves newlines, which can create unexpected whitespace when parsed.

```yaml
# Correct — verbatim phrases in core, category routing suffix, negative trigger
description: >
  This skill should be used when the user asks to "create a hook",
  "add validation", "implement lifecycle automation", or mentions
  pre/post tool events. Make sure to use this skill whenever the user
  mentions hook lifecycle, automation, or tool events — even if they
  don't explicitly say "hook". Not for modifying existing hooks or
  debugging hook failures.

# Wrong — vague, no trigger phrases, not third-person
description: Provides guidance for hooks.
```

### For Commands (user-invoked) — principles

- **Verb-first, under 60 chars.** The description appears as a single scannable line in the `/` menu — treat it as a menu label, not a sentence.
- **Describe the action, not the tool.** "Fix GitHub issue by number" orients by outcome. "GitHub issue fixer" orients by tool name. Users scan for what they want to accomplish.

```yaml
description: Fix GitHub issue by number
description: Review code for security issues
description: Deploy to staging environment
```

---

## Execution Modifiers

- **`hooks`** — Run scripts at lifecycle events, scoped to this skill's lifetime. See the Common Frontmatter Options block above for the full structure (matcher, type, timeout, statusMessage, once).

- **`context: fork`** — Run the skill in an isolated subagent. The skill content becomes the subagent's prompt; it won't have access to conversation history. Use for task-type skills (deploy, generate, research) where isolation prevents accidental side effects. Pair with `agent` to choose the subagent type (Explore, Plan, general-purpose, or a custom agent from `.claude/agents/`).

- **`effort`** — Override session effort level for this skill. Options: low, medium, high, max (max is Opus 4.6 only). Use high/max for skills requiring deep reasoning; low for simple lookup skills.

- **`paths`** — Glob patterns (comma-separated or YAML list) limiting auto-activation. When set, Claude loads the skill automatically only when working with files matching the patterns. Use for language-specific or framework-specific skills.

- **`shell`** — Shell for `!`backtick`` and ` ```! ` blocks: bash (default) or powershell. Setting powershell runs inline shell commands via PowerShell on Windows.

- **`disable-model-invocation: true`** — Prevent Claude from auto-loading this skill. Use for skills with side effects you want to trigger manually only.

- **`user-invocable: false`** — Hide from the `/` command menu. Use for background-knowledge skills that should trigger automatically but not appear as slash commands.

---

## Tool Selection

Default generous, restrict only when needed. The principle: restrict tools that have destructive or side-effect potential, not tools that are read-only or purely generative.

**YAML format:** `allowed-tools` must be a YAML list — block sequence (`- Tool`) or flow sequence (`[Tool, Tool]`). Never comma-separated on one line (`allowed-tools: Read, Glob, Edit`) — YAML parses that as a single string, not a list.

| Tier | Tools | Why |
|------|-------|-----|
| **Always allow** | Read, Grep, Glob | Read-only, no side effects |
| **Usually allow** | Edit, Write, WebSearch, WebFetch, Task | Core work tools; restrict if skill is deliberately read-only |
| **Scope Bash** | `Bash(git:*)`, `Bash(npm:*)`, `Bash(pytest:*)` | Bash is the highest blast-radius tool — scope to known commands |
| **If interactive** | AskUserQuestion | Required any time the skill needs user decisions mid-workflow |
| **If delegating** | Skill | Required to invoke other skills programmatically |
| **If notebooks** | NotebookEdit | Jupyter-specific; omit unless skill touches `.ipynb` files |
| **If plan-gated** | ExitPlanMode, EnterPlanMode | For workflows requiring explicit user approval before execution |

---

## Skill Content Lifecycle

When invoked, the rendered SKILL.md enters the conversation as a single message and stays
for the rest of the session. Claude Code does not re-read the file on later turns — write
guidance as standing instructions, not one-time steps.

Auto-compaction carries invoked skills forward within a token budget: the first 5,000
tokens of each skill are retained after compaction, and all recently invoked skills share
a combined 25,000-token budget (filled most-recent-first). Skills exceeding 5,000 tokens
lose their tail after compaction. Skills invoked long ago may be dropped entirely if the
budget is exhausted. If a skill seems to stop influencing behavior, re-invoke it.

Design implications: keep SKILL.md under 500 lines. Front-load critical instructions
within the first ~5,000 tokens. Move detailed reference material to separate files that
are loaded on demand.

---

## Progressive Disclosure

For complex skills, organize into subdirectories:

```
skill-name/
├── SKILL.md          # Core instructions (keep under 500 lines)
├── scripts/          # Executable code (Python/Bash)
├── references/       # Docs loaded into context as needed
├── examples/         # Working code examples users can copy directly
└── assets/           # Files used in output (templates, icons, fonts)
```

**scripts/** — Deterministic, token-efficient. May be executed without loading into context. Use when the same code is rewritten repeatedly or reliability is critical.

**references/** — Documentation Claude reads while working. Keeps SKILL.md lean. For files >100 lines, include a table of contents. Only load when needed.

**examples/** — Working code examples: complete, runnable scripts, configuration files, template files, real-world usage examples. Users can copy and adapt these directly. Distinct from references (docs) and scripts (utilities).

**assets/** — Files NOT loaded into context. Used in output: templates, images, fonts, boilerplate.

### Pattern 1: High-level guide with references

```markdown
# PDF Processing

## Quick start
Extract text with pdfplumber:
[code example]

## Advanced features
- **Form filling**: See references/forms.md
- **API reference**: See references/api.md
```

Claude loads references only when needed.

### Pattern 2: Domain-specific organization

```
bigquery-skill/
├── SKILL.md (overview and navigation)
└── references/
    ├── finance.md (revenue, billing)
    ├── sales.md (pipeline, opportunities)
    └── product.md (API usage, features)
```

When user asks about sales, Claude only reads sales.md.

### Pattern 3: Variant-based organization

```
cloud-deploy/
├── SKILL.md (workflow + provider selection)
└── references/
    ├── aws.md
    ├── gcp.md
    └── azure.md
```

User chooses AWS → Claude only reads aws.md.
