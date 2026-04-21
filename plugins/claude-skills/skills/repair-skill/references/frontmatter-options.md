# Frontmatter Options Reference

Complete reference for auditing skill and command frontmatter. Load this before running
Dimension 2 audits. Every field listed here is the full set of valid options — anything
not on this list is not a valid frontmatter key.

## Fields

### `name` (string)
Lowercase letters, digits, and hyphens only. Max 64 characters. Omit to use the directory
name as the identifier. Prefer short, verb-led names for commands. Namespace by tool when
it aids routing clarity: `gh-address-comments`, `linear-close-issue`.

### `description` (string)
The primary triggering mechanism. Always in context — costs tokens on every session
regardless of whether the skill is active. Use `>` folded scalar (not `|` literal). Must
be third-person for skills ("This skill should be used when..."), verb-first under 60
chars for commands.

Audit rules for description quality:
- **Token budget:** Under 150 tokens for most skills, 200 absolute max. Anthropic's hard limit is 1024 characters (~250 tokens); descriptions over 250 characters are truncated in the skill listing. Prioritize trigger phrases over explanatory prose.
- **Trigger phrase derivation:** Phrases should be verbatim user speech — the exact words someone would type, not formalized paraphrases. "fix my skill" triggers better than "skill remediation workflow."
- **Negative triggers:** In crowded domains (multiple skills with overlapping concerns), include "Not for X" or "Don't use for Y" to sharpen the routing decision boundary.
- **3–5 varied trigger phrases minimum.** Single-phrase descriptions have high miss rates. Include naive phrasing from a user who has never heard of this skill.
- **Overtriggering check:** Claude tends to undertrigger skills. If the description has no routing directive ("Make sure to use this skill whenever the user mentions [X, Y, Z] — even if they don't explicitly say '[skill name]'"), flag it for the author to consider adding one. The routing suffix uses intent categories and concept words (broad, generalizable), not verbatim query phrases (which overfit). The core uses verbatim phrases (optimized for recall); the suffix uses category words (broad, anti-overfit). These two layers are not interchangeable.

### `allowed-tools` (list)

Restricts which tools the skill can use. Default is unrestricted. Specifying this list
is a security and scope constraint — use it to limit blast radius for sensitive skills.

**YAML format constraint:** `allowed-tools` must be a YAML list — either a block sequence (`- Tool`) or a flow sequence (`[Tool, Tool]`). A comma-separated string on one line (`allowed-tools: Read, Glob, Edit`) parses as the scalar string `"Read, Glob, Edit"`, not a 3-element list. Tools may not be recognized by the runtime. *Critical if wrong.*

**Complete tool list:**

| Tool | Category | Notes |
|------|----------|-------|
| `Read` | File ops | Read-only, no side effects |
| `Write` | File ops | Creates/overwrites files |
| `Edit` | File ops | String replacement in existing files |
| `Glob` | Search | Pattern-based file discovery |
| `Grep` | Search | Regex content search |
| `Bash` | Execution | Highest blast radius — scope with patterns |
| `Bash(git:*)` | Execution | Scoped to git commands only |
| `Bash(npm:*)` | Execution | Scoped to npm commands only |
| `Bash(pytest:*)` | Execution | Scoped to pytest commands only |
| `WebFetch` | Web | Fetches a specific URL; distinct from search |
| `WebSearch` | Web | Queries a search engine |
| `Task` | Orchestration | Spawns subagents |
| `AskUserQuestion` | Interaction | Required for any mid-workflow user decision |
| `Skill` | Invocation | Required to invoke other skills programmatically |
| `NotebookEdit` | Notebooks | Jupyter-specific; omit unless skill touches `.ipynb` |
| `EnterPlanMode` | Plan flow | Required for plan-gated workflows |
| `ExitPlanMode` | Plan flow | Required for plan-gated workflows |
| `mcp__<server>__<tool>` | MCP | Any tool from an installed MCP server |

**Audit rule:** `Bash` unrestricted is almost always wrong — scope it. `AskUserQuestion`
must be present if the skill asks the user anything. `Skill` must be present if the skill
invokes another skill by name.

### Tool Selection Framework

The core principle: restrict tools that have destructive or side-effect potential, not
tools that are read-only or purely generative. Over-restriction breaks the skill; under-
restriction is a security and scope risk.

| Tier | Tools | Why | When to restrict |
|------|-------|-----|------------------|
| **Always allow** | `Read`, `Grep`, `Glob` | Read-only, no side effects | Only if skill must be strictly read-only |
| **Usually allow** | `Edit`, `Write`, `WebSearch`, `WebFetch`, `Task` | Core work tools | Restrict if skill is deliberately non-modifying |
| **Scope Bash** | `Bash(git:*)`, `Bash(npm:*)`, `Bash(pytest:*)` | Highest blast radius — scope to known commands | Never allow unrestricted `Bash` unless tool scope is genuinely unknown |
| **Require if interactive** | `AskUserQuestion` | Required any time the skill needs user decisions | Omit only if the skill is fully automated |
| **Require if delegating** | `Skill` | Required to invoke other skills programmatically | Omit if no delegation |
| **Require if notebook** | `NotebookEdit` | Jupyter-specific | Omit unless skill touches `.ipynb` |
| **Require if plan-gated** | `EnterPlanMode`, `ExitPlanMode` | For workflows requiring explicit approval before execution | Omit unless skill has a plan/execute split |

**Gap audit questions:**
- Does the skill need user decisions but lacks `AskUserQuestion`? → add it
- Does the skill invoke another skill but lacks `Skill`? → add it
- Does the skill write files but has no `Edit` or `Write`? → add them
- Does the skill have unrestricted `Bash` when a scoped pattern would work? → scope it
- Does the skill have `Bash(git:*)` but never uses git? → remove it (dead scope)

### `hooks` (object)

Scoped to this skill's lifetime — runs only when the skill is active, cleaned up when it
finishes.

```yaml
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
          once: true
```

All hook events are supported (PreToolUse, PostToolUse, Stop, SessionStart, etc.). Each
entry can have a `matcher` (filter by tool name) and a `hooks` array with handlers.
Handler fields: `type` (command, http, prompt, agent), `command`, `timeout` (seconds),
`statusMessage` (custom spinner text), `once` (skills only — run once then auto-remove).

Audit rule: hooks with no `matcher` fire on every tool call — verify this is intentional.
Hooks using `once: true` outside of skills are invalid (agents don't support it).

### `user-invocable` (boolean)

| Value | Effect |
|-------|--------|
| `true` | Default. Skill appears in the `/` command menu. |
| `false` | Hidden from `/` menu. Skill still triggers automatically via description routing. |

Use `false` for background-knowledge skills that should activate automatically but
shouldn't clutter the command menu.

### `disable-model-invocation` (boolean)

Commands only. Prevents Claude from auto-loading this skill based on its description.
Forces manual invocation only. Default: `false`.

### `argument-hint` (string)

Shown in autocomplete when the user types the command. Documents expected argument
syntax. Examples: `"[issue-number]"`, `<path-to-skill>`, `"[skill|command] [name]"`.

**Quoting rule:** values that contain `[...]` must be quoted (`"[arg]"`), because YAML
treats unquoted `[` as the start of a flow sequence. Values using only `<...>` do not
need quoting.

Audit rule: any skill that reads `$ARGUMENTS` or `$1`/`$2` should have `argument-hint`
set so users know what to pass.

### `context` (string)

Set to `fork` to run the skill in an isolated subagent. The skill content becomes the
subagent's prompt; it won't have access to conversation history. Use for task-type skills
where isolation prevents accidental side effects. Pair with `agent`.

### `agent` (string)

Which subagent type to use when `context: fork` is set. Options: built-in agents
(`Explore`, `Plan`, `general-purpose`) or custom agents from `.claude/agents/`. If
omitted, uses `general-purpose`.

Audit rule: `agent` without `context: fork` is dead config — flag it.

### `effort` (string)

Override session effort level: `low`, `medium`, `high`, `max` (max is Opus 4.6 only).
Use high/max for skills requiring deep reasoning; low for simple lookup skills.

### `paths` (string or list)

Glob patterns limiting auto-activation. When set, Claude loads the skill automatically
only when working with files matching the patterns. Accepts comma-separated string or
YAML list. Uses the same format as path-specific rules in CLAUDE.md.

Audit rule: skills with `paths` set should not also have broad descriptions — the path
filter narrows scope, so the description should match that narrowed scope.

### `shell` (string)

Shell for inline !\`cmd\` blocks and ` ```! ` fenced blocks: `bash` (default) or `powershell`. Only
relevant for skills using inline shell execution.

---

## Skill Content Lifecycle

When invoked, the rendered SKILL.md enters the conversation as a single message and stays
for the rest of the session. Claude Code does not re-read the file on later turns — write
guidance as standing instructions, not one-time steps.

Auto-compaction carries invoked skills forward within a token budget: the first 5,000
tokens of each skill are retained after compaction, and all recently invoked skills share
a combined 25,000-token budget (filled most-recent-first). Skills exceeding 5,000 tokens
lose their tail after compaction. Skills invoked long ago may be dropped entirely if the
budget is exhausted.

Audit relevance: if a skill's critical instructions appear after the first ~5,000 tokens,
they will be lost after compaction. Flag this as a structural issue — front-load critical
content or move reference material to separate files.

---

## Dynamic Content Syntax

These substitutions are processed before the skill body reaches Claude.

| Syntax | Resolves to |
|--------|-------------|
| `$ARGUMENTS` | All arguments passed to the skill as a single string |
| `$1`, `$2`, `$3` | Individual positional arguments (shell-style quoting for multi-word values) |
| `@path/to/file` | Contents of the file at that path, loaded inline |
| `@$1` | Contents of the file whose path was passed as the first argument |
| bang + backtick-wrapped command (e.g. `!date`) | Output of executing the command in a shell, injected inline |
| `${CLAUDE_SKILL_DIR}` | Path to the skill's own directory (for referencing bundled scripts/files) |
| `${CLAUDE_SESSION_ID}` | Current session ID (for logging or session-specific output files) |

Audit rule: skills that accept a file path as input should use `@$1` to load it inline
rather than requiring a separate Read tool call — the injection happens before the model
sees the skill, saving a tool round-trip. The bang-backtick pattern is underused:
real-time data like git branch, file tree, or env vars can be injected without tool calls.
