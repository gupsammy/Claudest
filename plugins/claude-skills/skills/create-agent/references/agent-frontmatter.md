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

| Value | Use when |
|-------|----------|
| `inherit` | Agent should use same model as parent conversation |
| `sonnet` | Complex multi-step reasoning, code analysis, generation tasks |
| `haiku` | Fast, cheap tasks with simple structure (classification, extraction) |
| `opus` | Highest-complexity reasoning; use sparingly — cost scales |

### `color`

Visual identifier in the Claude Code UI. Choose distinct colors for agents in the same plugin.

| Color | Semantic signal | Suitable for |
|-------|----------------|--------------|
| `blue` | Analysis, review | Code review, security audit, quality analysis |
| `cyan` | Information gathering | Research, documentation, data extraction |
| `green` | Generation, creation | Code generation, content writing, scaffolding |
| `yellow` | Validation, caution | Linting, testing, configuration validation |
| `red` | Critical, destructive | Security scanning, dangerous operations |
| `magenta` | Transformation, creative | Refactoring, reformatting, creative tasks |

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

### `disallowedTools`

Explicitly remove tools from the inherited/specified set. Useful when you want most tools but
need to block one destructive operation.

```yaml
disallowedTools: ["Bash", "Write"]
```

### `permissionMode`

How the agent handles permission prompts. Default: `default`.

| Value | Behavior |
|-------|----------|
| `default` | Standard permission handling, inherits from parent |
| `acceptEdits` | Auto-approve file edits without prompting |
| `dontAsk` | Suppress confirmations for most actions |
| `bypassPermissions` | Skip all permission checks (dangerous — use only in controlled contexts) |
| `plan` | Require plan approval before executing |

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

Persistent memory scope for cross-session agent learning.

| Value | Scope |
|-------|-------|
| `user` | Persists to `~/.claude/` — shared across all projects |
| `project` | Persists to `.claude/` — scoped to current project |
| `local` | In-session only (equivalent to no `memory` field) |

### `hooks`

Lifecycle hooks scoped to this agent's execution. Same format as Claude Code session hooks.

```yaml
hooks:
  PreToolUse:
    - command: "validate-before-write.sh"
  PostToolUse:
    - command: "log-tool-use.sh"
```

### `mcpServers`

MCP servers available to this agent. Can reference named servers or provide inline config.

```yaml
mcpServers:
  - my-database-server
  - name: inline-server
    command: npx
    args: ["-y", "@example/mcp-server"]
```

---

## Field Summary Table

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `name` | Yes | — | lowercase-hyphens, 3-50 chars |
| `description` | Yes | — | "Use this agent when..." — concise `>` scalar, 50-70 tokens, no examples |
| `model` | No | `inherit` | inherit/sonnet/haiku/opus |
| `color` | No | — | blue/cyan/green/yellow/magenta/red |
| `tools` | No | all tools | Least-privilege allowlist |
| `disallowedTools` | No | none | Explicit denylist |
| `permissionMode` | No | `default` | default/acceptEdits/dontAsk/bypassPermissions/plan |
| `maxTurns` | No | unlimited | Positive integer |
| `skills` | No | none | Comma-separated skill names to preload |
| `background` | No | false | Run as background task |
| `isolation` | No | none | Only value: `worktree` |
| `memory` | No | none | user/project/local |
| `hooks` | No | none | PreToolUse/PostToolUse/Stop lifecycle hooks |
| `mcpServers` | No | none | Named or inline MCP server config |

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
