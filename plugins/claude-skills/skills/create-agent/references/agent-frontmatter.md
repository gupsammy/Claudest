# Agent Frontmatter Reference

**Authoritative source for agent frontmatter.** Keep current with Claude Code releases —
this file is the single source of truth used by create-agent. No live documentation fetch
is performed; accuracy depends on this file being maintained.

Load before writing frontmatter in Phase 1, Step 2. Contains the full field catalog,
description format, color semantics, tool selection, and execution modifiers for agents.

---

## Required Fields

### `name`

Unique agent identifier within its scope.

- **Format:** lowercase letters, numbers, hyphens only
- **Length:** 3–50 characters
- **Pattern:** must start and end with alphanumeric; no consecutive hyphens
- Good: `code-reviewer`, `test-generator`, `api-docs-writer`, `security-analyzer`
- Bad: `helper` (too generic), `ag` (too short), `-agent-` (leading/trailing hyphen), `my_agent` (underscore)

### `description`

Defines when Claude delegates to this agent. Loaded into context every session — token
budget matters.

Use `>` folded scalar. Target 50-70 tokens. No `<example>` blocks.

**Format:**
- Start with "Use this agent when [trigger conditions]."
- Add proactive hint if applicable ("Recommended PROACTIVELY after...")
- Add scope boundary if adjacent agents exist ("Not for X — use Y-agent.")
- State when NOT to trigger if ambiguity with other agents exists

---

## Optional Fields

### `model`

Model the agent uses. Default: `inherit` (recommended for most cases).

Accepts aliases or full model IDs (e.g., `claude-opus-4-6`, `claude-sonnet-4-6`).

| Value | Use when |
|-------|----------|
| `inherit` | Agent should use same model as parent conversation |
| `sonnet` | Complex multi-step reasoning, code analysis, generation tasks |
| `haiku` | Fast, cheap tasks with simple structure (classification, extraction) |
| `opus` | Highest-complexity reasoning; use sparingly — cost scales |
| Full model ID | Pin to a specific model version (e.g., `claude-sonnet-4-6`) |

Model resolution order (first match wins):
1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable
2. Per-invocation `model` parameter from the caller
3. Agent definition's `model` frontmatter
4. Main conversation's model

### `color`

Visual identifier in the Claude Code UI. Choose distinct colors for agents in the same plugin.

| Color | Semantic signal | Suitable for |
|-------|----------------|--------------|
| `blue` | Analysis, review | Code review, security audit, quality analysis |
| `cyan` | Information gathering | Research, documentation, data extraction |
| `green` | Generation, creation | Code generation, content writing, scaffolding |
| `yellow` | Validation, caution | Linting, testing, configuration validation |
| `red` | Critical, destructive | Security scanning, dangerous operations |
| `purple` | Transformation, creative | Refactoring, reformatting, creative tasks |
| `orange` | Operations, infrastructure | Build, deploy, CI/CD, configuration |
| `pink` | Communication, social | Notifications, messaging, collaboration |

### `tools`

Restrict the agent to a specific allowlist of tools. If omitted, agent has access to all tools.
**Apply least-privilege** — agents run autonomously with no human in the loop to catch errors.

Common minimal sets:

```yaml
# Read-only analysis
tools: "Read", "Grep", "Glob"

# Code generation
tools: "Read", "Write", "Grep", "Glob"

# Testing / validation
tools: "Read", "Bash", "Grep", "Glob"

# Full access (use sparingly)
# Omit the field entirely
```

**Scoping Bash:** Prefer scoped patterns like `Bash(git:*)`, `Bash(npm:*)`, `Bash(pytest:*)`.
Unscoped `Bash` grants full shell access — the highest blast-radius tool.

**Scoping Agent:** For agents running as main thread via `--agent`, restrict which subagents
they can spawn using `Agent(worker, researcher)` syntax. Without parentheses (`Agent`),
any subagent can be spawned. If `Agent` is omitted from `tools` entirely, the agent cannot
spawn subagents. This restriction only applies to `--agent` mode — subagents cannot spawn
other subagents regardless.

### `disallowedTools`

Explicitly remove tools from the inherited/specified set. Useful when you want most tools but
need to block one destructive operation. If both are set, `disallowedTools` removes from the
inherited pool first, then `tools` restricts to its allowlist. A tool in both is removed.

```yaml
disallowedTools: Write, Edit
```

### `permissionMode`

How the agent handles permission prompts. Default: `default`.

| Value | Behavior |
|-------|----------|
| `default` | Standard permission handling, inherits from parent |
| `acceptEdits` | Auto-approve file edits without prompting |
| `auto` | Background classifier reviews commands and protected-directory writes |
| `dontAsk` | Auto-deny permission prompts (explicitly allowed tools still work) |
| `bypassPermissions` | Skip all permission checks (dangerous — use only in controlled contexts) |
| `plan` | Plan mode (read-only exploration) |

If the parent uses `bypassPermissions`, it takes precedence and cannot be overridden. If the
parent uses `auto`, the subagent inherits auto mode and any `permissionMode` in frontmatter
is ignored.

### `maxTurns`

Maximum agentic turns before stopping. Prevents runaway loops on unbounded tasks.
Set when the agent's task has a predictable completion horizon.

```yaml
maxTurns: 10
```

### `skills`

Skills to preload into the agent's context at startup. Full skill content is injected,
not just made available. Use to equip the agent with domain knowledge without embedding
it in the system prompt.

```yaml
skills: code-conventions, api-patterns
```

### `background`

Run agent as a background task. Default: `false`.

```yaml
background: true
```

### `isolation`

Run agent in a temporary git worktree — an isolated copy of the repository. Auto-cleaned
if the agent makes no changes; worktree path returned if changes were made.

```yaml
isolation: worktree
```

Use when: agent makes file modifications that shouldn't pollute the working tree until reviewed,
or when multiple parallel agents need independent working state.

### `memory`

Persistent memory directory that survives across conversations. Enables cross-session learning —
the agent accumulates codebase patterns, debugging insights, and architectural decisions over time.

| Value | Directory | Use when |
|-------|-----------|----------|
| `user` | `~/.claude/agent-memory/<name>/` | Learnings apply across all projects |
| `project` | `.claude/agent-memory/<name>/` | Knowledge is project-specific; shareable via VCS (recommended default) |
| `local` | `.claude/agent-memory-local/<name>/` | Knowledge is project-specific but should not be checked into VCS |

When memory is enabled, three things happen automatically:
1. The system prompt includes instructions for reading/writing to the memory directory
2. The first 200 lines or 25KB of `MEMORY.md` in the memory directory is injected into context
3. Read, Write, and Edit tools are auto-enabled so the agent can manage its memory files

When setting `memory`, add instructions in the system prompt body for the agent to maintain
its knowledge base (e.g., "Update your agent memory as you discover codepaths, patterns,
and key architectural decisions.").

### `effort`

Overrides the session effort level for this agent. Controls thinking depth.
Default: inherits from session.

| Value | Use when |
|-------|----------|
| `low` | Fast, cheap tasks — classification, extraction, simple lookups |
| `medium` | Balanced reasoning — most agents |
| `high` | Deep multi-step reasoning, complex code analysis |
| `max` | Maximum thinking depth (Opus 4.6 only) |

### `initialPrompt`

Auto-submitted as the first user turn when this agent runs as the main session agent
(via `--agent <name>` or the `agent` setting in `.claude/settings.json`). Commands and
skills in the prompt are processed. Prepended to any user-provided prompt.

Use for self-starting agents that should begin work immediately without waiting for
user input. Only relevant for agents designed to run as session agents, not subagents.

```yaml
initialPrompt: "/review-pr"
```

### `hooks`

Lifecycle hooks scoped to this agent's execution. Only run while the agent is active;
cleaned up when it finishes. Supported events: `PreToolUse`, `PostToolUse`, `Stop`
(auto-converted to `SubagentStop` at runtime).

```yaml
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/validate-command.sh"
  PostToolUse:
    - matcher: "Edit|Write"
      hooks:
        - type: command
          command: "./scripts/run-linter.sh"
```

Each entry has an optional `matcher` (regex against tool name) and a `hooks` array of
`{type: command, command: "..."}` objects. Hook commands receive JSON via stdin with
the tool input; exit code 2 blocks the operation.

### `mcpServers`

MCP servers available to this agent. Each entry is either a string reference (reuses a
server already configured in the parent session) or an inline definition (scoped to this
agent only — connected on start, disconnected on finish).

```yaml
mcpServers:
  # Inline definition: scoped to this agent only
  - playwright:
      type: stdio
      command: npx
      args: ["-y", "@playwright/mcp@latest"]
  # Reference by name: reuses an already-configured server
  - github
```

Inline definitions use the same schema as `.mcp.json` server entries (`stdio`, `http`,
`sse`, `ws`), keyed by the server name. Define servers inline here rather than in
`.mcp.json` to keep their tool descriptions out of the main conversation context.

Plugin agents cannot use `hooks`, `mcpServers`, or `permissionMode` — these fields are
ignored when loading agents from a plugin. Copy the agent to `.claude/agents/` or
`~/.claude/agents/` if needed.

---

## Field Summary Table

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `name` | Yes | — | lowercase-hyphens, 3-50 chars |
| `description` | Yes | — | "Use this agent when..." — concise `>` scalar, 50-70 tokens, no examples |
| `model` | No | `inherit` | inherit/sonnet/haiku/opus or full model ID (e.g., `claude-sonnet-4-6`) |
| `color` | No | — | red/blue/green/yellow/purple/orange/pink/cyan |
| `tools` | No | all tools | Least-privilege allowlist; supports `Agent(type)` scoping |
| `disallowedTools` | No | none | Explicit denylist; removes from inherited pool before `tools` allowlist |
| `permissionMode` | No | `default` | default/acceptEdits/auto/dontAsk/bypassPermissions/plan |
| `maxTurns` | No | unlimited | Positive integer |
| `skills` | No | none | Comma-separated skill names to preload |
| `background` | No | false | Run as background task (auto-denies unpre-approved permissions) |
| `isolation` | No | none | Only value: `worktree` |
| `memory` | No | none | user/project/local — enables persistent cross-session learning |
| `effort` | No | inherit | low/medium/high/max (max is Opus 4.6 only) |
| `initialPrompt` | No | none | First user turn when running as session agent via `--agent` |
| `hooks` | No | none | PreToolUse/PostToolUse/Stop with matcher/command structure |
| `mcpServers` | No | none | String references or inline server definitions |

---

## Proactive Agent Pattern

For agents that should trigger after an event (not just on explicit request), include
"Recommended PROACTIVELY after [event]" in the description. The description itself stays
concise — the proactive behavior is a hint to the routing model, not a worked example.

---

## Minimal Valid Agent

```markdown
---
name: simple-agent
description: >
  Use this agent when [trigger conditions]. Not for [adjacent concern] — use [other-agent].
model: inherit
color: blue
---

You are an expert [role] specializing in [domain].

**Process:**
1. [First step]
2. [Second step]

**Output:** [What to return]
```
