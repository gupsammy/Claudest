# Frontmatter Options & Patterns Reference

**Authoritative source for skill/command frontmatter.** Keep current with Claude Code
releases — this file is the single source of truth used by create-skill. No live
documentation fetch is performed; accuracy depends on this file being maintained.

Load before writing frontmatter in Phase 1, Step 2. Contains the full field catalog,
description patterns, execution modifiers, tool selection framework, and progressive
disclosure patterns.

---

## Essential Frontmatter

Every skill needs these fields. Start here.

```yaml
# Complete field catalog — most skills only need: name, description, allowed-tools
---
name: identifier                    # Required — unique skill identifier
description: >                      # How it's described/triggered (see patterns below)
  [See description patterns below]
allowed-tools:                      # Restrict available tools (see Tool Selection below)
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
effort: high                        # Override session effort: low | medium | high | max (max: Opus 4.6 only)
paths: "*.py,src/**/*.ts"           # Glob patterns limiting auto-activation to matching files
shell: bash                         # Shell for bang+backtick inline-command blocks: bash (default) or powershell

# Behavior modifiers
user-invocable: true                # Show in /command menu (default true)
disable-model-invocation: true      # Prevent programmatic invocation (commands only)
argument-hint: "[arg1] [arg2]"      # Document expected arguments; quote if value contains [...]
---
```

**`argument-hint` quoting rule:** Values containing `[...]` must be quoted (`"[arg]"`), because YAML treats unquoted `[` as the start of a flow sequence. Values using only `<...>` do not need quoting.

## Advanced Frontmatter

Use when needed — most skills don't require these.

```yaml
# Lifecycle hooks (scoped to this skill's lifetime)
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
effort: high                        # Override session effort: low | medium | high | max (max: Opus 4.6 only)
paths: "*.py,src/**/*.ts"           # Glob patterns limiting auto-activation to matching files
shell: bash                         # Shell for bang+backtick inline-command blocks: bash (default) or powershell

# Behavior modifiers (commands)
disable-model-invocation: true      # Prevent programmatic invocation (commands only)
```

**Hooks structure:** Each hook event (PreToolUse, PostToolUse, Stop, SessionStart, etc.)
accepts an array of entries. Each entry can have a `matcher` (filter by tool name) and a
`hooks` array with handlers. Handler fields: `type` (command, http, prompt, agent),
`command`, `timeout` (seconds), `statusMessage` (custom spinner text), `once` (skills
only — run once then auto-remove). Hooks are scoped to the skill's lifetime and cleaned
up when it finishes.

---

## Description Patterns

### For Skills (auto-triggered) — the two-layer model

Skill descriptions serve one purpose: helping the routing model decide when to trigger. They use a two-layer structure where a broad routing directive provides primary coverage and a few verbatim phrases anchor the intent.

**Layer 1: Routing directive (primary coverage).** A sentence at the end that tells the model to trigger broadly across an intent category. Format: "Make sure to use this skill whenever the user mentions [X, Y, Z] — even if they don't explicitly say '[skill name]'." X/Y/Z are intent categories and concept words (broad, generalizable), not verbatim query phrases. This is the main coverage mechanism — it catches the long tail of phrasings you can't anticipate.

**Layer 2: Verbatim anchors (precision).** 2–3 quoted phrases representing the exact words a user would type. These provide high-confidence matches for common cases. Derive them from real user language, not formalized paraphrases: `"fix my skill"` not `"skill remediation"`. Cover the naive phrasing — what someone would say who has never heard of this skill.

The two layers are complementary, not interchangeable: verbatim phrases optimize for precision on known patterns; the routing directive optimizes for recall across unknown patterns.

#### Additional principles

- **Third-person framing is a routing signal.** "This skill should be used when..." reads as a condition to test. "Use this skill when..." reads as an instruction to execute. The routing model treats these differently.
- **Include negative triggers for adjacent domains.** Explicit exclusions sharpen the decision boundary. Add "Not for X" when the skill could plausibly false-trigger on a related domain.
- **The description is always in context.** Every session pays the token cost of every skill's description. Keep it dense — the routing directive eliminates the need for exhaustive trigger phrase lists.
- **Keep descriptions under 150 tokens (200 absolute max).** Anthropic's hard limit is 1024 characters (~250 tokens). Descriptions longer than 250 characters are truncated. The routing directive adds ~20-30 tokens — the budget accommodates it.
- **Use `>` scalar, not `|`.** Folded scalar (`>`) collapses newlines to spaces — correct for descriptions. Literal scalar (`|`) preserves newlines, which can break parsing.

```yaml
# Correct — verbatim anchors, broad routing directive, negative trigger
description: >
  This skill should be used when the user asks to "create a hook"
  or "add lifecycle automation". Make sure to use this skill whenever
  the user mentions hook authoring, tool-event automation, or
  validation pipelines — even if they don't explicitly say "hook".
  Not for modifying existing hooks or debugging hook failures.

# Wrong — exhaustive trigger phrases, no routing directive
description: >
  This skill should be used when the user asks to "create a hook",
  "add validation", "implement lifecycle automation", "set up
  pre-tool hooks", "add post-tool cleanup", or "write hook scripts".

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

## Essential Field Reference

- **`name`** — Unique identifier. Required for all skills and commands.

- **`description`** — How the routing model decides when to trigger this skill, or the label shown in the `/` menu for commands. See Description Patterns below.

- **`allowed-tools`** — Restrict which tools the skill can use. Default is all tools. See Tool Selection below.

For `hooks`, `context`, `effort`, and `paths` — see Advanced Field Reference below.

- **`shell`** — Shell for inline !\`cmd\` blocks and ` ```! ` fenced blocks: bash (default) or powershell. Setting powershell runs inline shell commands via PowerShell on Windows. Requires `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` env var for inline bang-prefix commands to execute via PowerShell.

- **`disable-model-invocation: true`** — Commands only. Prevents Claude from auto-loading based on description. Has no effect on skills — use `user-invocable: false` instead.

- **`user-invocable`** — Whether the skill appears in the `/` command menu. Default `true`. Set to `false` for background-knowledge skills that should trigger automatically but not appear as slash commands.

- **`argument-hint`** — Shown in autocomplete when the user types the command. Documents expected arguments.

## Advanced Field Reference

These fields add execution control, lifecycle hooks, and platform-specific behavior. Most skills don't need them.

- **`hooks`** — Run scripts at lifecycle events, scoped to this skill's lifetime. See the Advanced Frontmatter block above for the full structure (matcher, type, timeout, statusMessage, once).

- **`context: fork`** — Run the skill in an isolated subagent. The skill content becomes the subagent's prompt; it won't have access to conversation history. Use for task-type skills (deploy, generate, research) where isolation prevents accidental side effects. Pair with `agent` to choose the subagent type (Explore, Plan, general-purpose, or a custom agent from `.claude/agents/`).

- **`effort`** — Override session effort level for this skill. Options: low, medium, high, max (max is Opus 4.6 only). Use high/max for skills requiring deep reasoning; low for simple lookup skills.

- **`paths`** — Glob patterns (comma-separated or YAML list) limiting auto-activation. When set, Claude loads the skill automatically only when working with files matching the patterns. Use for language-specific or framework-specific skills.

- **`shell`** — Shell for inline !\`cmd\` blocks and ` ```! ` fenced blocks: bash (default) or powershell. Setting powershell runs inline shell commands via PowerShell on Windows. Requires `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` env var for inline bang-prefix commands to execute via PowerShell.

- **`disable-model-invocation: true`** — Commands only. Prevents Claude from auto-loading based on description. Has no effect on skills — use `user-invocable: false` instead for skills you want to hide from auto-triggering.

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
