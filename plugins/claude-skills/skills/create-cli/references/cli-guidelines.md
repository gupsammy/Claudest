# Command Line Interface Guidelines (condensed)

Source + contribution:
- Full guide: https://clig.dev/
- Propose changes: https://github.com/cli-guidelines/cli-guidelines

Table of contents:
- Foreword
- Introduction
- Philosophy
  - Human-first design
  - Simple parts that work together
  - Consistency across programs
  - Saying (just) enough
  - Ease of discovery
  - Conversation as the norm
  - Robustness
  - Empathy
  - Chaos
- Guidelines
  - The Basics
  - Help
  - Documentation
  - Output
  - Errors
  - Arguments and flags
  - Interactivity
  - Subcommands
  - Robustness
  - Future-proofing
  - Signals and control characters
  - Configuration
  - Environment variables
  - Naming
  - Distribution
  - Analytics
  - Further reading
- Authors

This is a practical rubric for designing CLI interfaces (args/flags/subcommands/help/output/errors/config). Keep humans first, but preserve composability and scriptability.

## Foreword

- CLI still uniquely powerful: inspect/control systems; works interactively and in automation.
- Modern CLI = human-first text UI, not just a machine-first REPL veneer.
- Goal: maximize utility + accessibility; design for humans and composition.

## Introduction

- This guide mixes philosophy + concrete rules; bias: examples over theorizing.
- Out of scope: full-screen TUIs (vim/emacs-like).
- Language/tooling agnostic: apply principles regardless of implementation stack.

## Philosophy

### Human-first design

- Optimize for humans by default; scripts still work via stable modes (`--json`, `--plain`, exit codes).
- Don't leak developer-only output to normal users; reserve for verbose/debug.

### Simple parts that work together

- Assume your output becomes someone else's input.
- Respect stdio, exit codes, signals; keep primary output on stdout.
- Prefer line-oriented plain text for piping; add JSON for structured needs.

### Consistency across programs

- Follow common conventions unless they harm usability.
- Reuse standard flag names (`--help`, `--version`, `--json`, `--dry-run`, …).

### Saying (just) enough

- Too little: "hangs" with no feedback. Too much: noisy debug spew.
- Make progress/status visible, but keep success output brief.

### Ease of discovery

- Help text is part of UX. Put examples first; suggest next commands.
- When user errs, help them recover: point to the right syntax/flag.

### Conversation as the norm

- Expect trial-and-error loops. Design for repeated invocations.
- Provide safe "dry run"/preview; show intermediate state; confirm scary actions.

### Robustness

- Be correct *and* feel robust: responsive, clear, no scary traces by default.
- Handle bad input gracefully; validate early; clear errors.

### Empathy

- Be on the user's side. Make success likely; make failure informative.
- Character is fine; clutter is not.

### Chaos

- Terminal ecosystem inconsistent; follow norms, but break them intentionally when needed.
- If you diverge, do it with clarity and document it.

## Guidelines

### The Basics

- Use a real argument parsing library when possible (built-in or reputable OSS).
- Exit codes: `0` on success, non-zero on failure; map a few important failure modes.
- Stdout for primary output (and machine-readable output). Stderr for messages/logs/errors.

### Help

- Always support `-h`/`--help`. Do not overload `-h`.
- If run with missing required args, show concise help + 1–2 examples + "use --help".
- Git-like CLIs: support `mycmd help`, `mycmd help subcmd`, `mycmd subcmd --help`.
- Link to a support path (repo/issues/docs). Prefer deep links per subcommand (when you have web docs).
- Lead with examples; show common flags/commands first; keep formatting readable without escape-char soup.

### Documentation

- Provide web docs (searchable, linkable).
- Provide terminal docs (`mycmd help ...`); consider man pages where sensible.

### Output

- Humans first, machines second: detect TTY to choose formatting.
- If fancy human output breaks parsing, offer `--plain` (stable, line-based) and/or `--json`.
- On success: usually print *something*, but keep it brief; add `-q/--quiet` when useful.
- If you change state, say what changed and what the new state is.
- Suggest "next commands" in workflowy tools.
- Use color sparingly; disable when not a TTY, `NO_COLOR` set, `TERM=dumb`, or `--no-color`.
- No animations/progress bars when stdout isn't a TTY.
- Use a pager for long output only when interactive; common `less` opts: `-FIRX`.

### Errors

- Catch and rewrite expected errors for humans; avoid stack traces by default.
- Keep signal-to-noise high; group repeated errors.
- Put the most important info last; use red intentionally (don't drown the user).
- For unexpected crashes: provide a path to debug info + bug report instructions; write logs to a file if large.

### Arguments and flags

- Prefer flags over positional args for clarity and future flexibility.
- Provide long versions of all flags; use one-letter flags only for the most common.
- Multiple args ok for repeated simple items (`rm a b c`); avoid "2+ different positional concepts".
- Standard flag names (common set):
  - `-h, --help` help
  - `--version` version
  - `-q, --quiet` less output
  - `-v, --verbose` more output (avoid `-v` meaning version)
  - `-d, --debug` debug output
  - `-f, --force` skip confirmation / force
  - `-n, --dry-run` preview only
  - `--json` structured output
  - `-o, --output <file>` output path
  - `--no-input` disable prompts
- Default should be right for most users (don't rely on everyone aliasing a flag).
- Support `-` for stdin/stdout when input/output is a file.
- Avoid secrets in flags; prefer `--password-file` or stdin.
- Prefer order independence for flags/subcommands where the parser allows.

### Interactivity

- Prompt only if stdin is a TTY.
- `--no-input`: never prompt; if required input missing, fail with an actionable message.
- Password prompts: disable echo.
- Make escape hatch obvious (Ctrl-C, or explicit "press q", etc).

### Subcommands

- Use subcommands for complexity; share global flags/config/help.
- Be consistent across subcommands: naming, flags, output, formatting.
- Consider noun-verb (`docker container create`) or verb-noun; pick one and stick to it.
- Avoid ambiguous pairs (`update` vs `upgrade`) unless sharply differentiated.
- Avoid implicit "catch-all" subcommands; don't allow arbitrary abbreviations (future-proofing trap).

### Robustness

- Validate early; fail fast with good error messages.
- Be responsive: print something in <100ms (especially before network I/O).
- Show progress for long tasks (interactive only); don't interleave logs confusingly.
- Use timeouts for network calls; allow configuration.
- Make reruns safe: idempotent where possible; recoverable; "crash-only" where feasible.

### Future-proofing

- Interfaces are contracts: args, flags, subcommands, config, env vars, output modes.
- Keep changes additive; deprecate loudly + early; provide migration paths.
- Allow human output to evolve; keep scripts stable by encouraging `--plain`/`--json`.

### Signals and control characters

- Ctrl-C: exit quickly; say something immediately; bound cleanup.
- Second Ctrl-C: optionally force; tell user what it does.
- Assume cleanup might not run; design for crash-only recovery.

### Configuration

- Pick the right mechanism:
  - Per-invocation: flags (and sometimes env).
  - Per-user/machine: flags + env; possibly config file.
  - Per-project (checked in): config file in repo.
- Follow XDG base directories for user-level config when applicable.
- Precedence (high → low): flags > process env > project config > user config > system config.
- Don't silently modify other programs' config; ask consent; prefer new files over editing existing ones.

### Environment variables

- Names: uppercase + digits + underscores; single-line values preferred.
- Respect common vars when relevant: `NO_COLOR`, `DEBUG`, `EDITOR`, `PAGER`, proxy vars, `TERM`, `TMPDIR`, `HOME`, `COLUMNS/LINES`.
- `.env` can be useful for per-project non-secret knobs; don't use it as a full config system.
- Don't accept secrets via env vars by default; prefer files/pipes/sockets/secret managers.

### Naming

- Command name: simple, memorable, lowercase; avoid too-generic collisions.
- Keep it short but not cryptic; easy to type matters.

### Distribution

- Prefer single binary when practical; otherwise use native packaging for uninstallability.
- Make uninstall easy; include instructions.

### Analytics

- Never phone home without explicit consent; explain what/why/how/retention.
- Prefer opt-in; if opt-out, make it obvious and easy to disable.
- Consider alternatives: docs instrumentation, download metrics, talking to users.

### Further reading

- POSIX Utility Conventions
- GNU Coding Standards (esp. flags/help conventions)
- 12 Factor CLI Apps
- Heroku CLI Style Guide

## Agent Ergonomics

These guidelines extend the human-first philosophy for tools where the primary caller is an
AI agent (LLM-based coding assistant, automated pipeline, etc.). Agents are trained on
standard CLI conventions and will use them — the goal is not a new interface but rigorous
application of existing conventions with agent consumption in mind. The approach is
agent-aware, not agent-first: three pillars are token-efficient output, fewer tool calls
through good UX, and structured errors that enable programmatic recovery.

This is not a formal schema spec (no OpenAPI/JSON Schema for CLI interfaces) — principles
and strong conventions, not machine-readable contracts. The goal is fewer tool calls, not
shortest possible output; rich compound output often uses more tokens per call but fewer
calls total.

### Output defaults

- Data format (JSON vs human text) follows an explicit `--json`/`--plain` flag that overrides
  TTY state — never TTY alone. Agents pass the flag and must not rely on auto-detection: some
  harnesses allocate a PTY, which would silently flip an auto-detecting tool to human tables and
  break the parser. TTY-detecting the *default* (table on a TTY, JSON when piped) is fine for
  human-primary tools, but only with the explicit override as the documented stable contract.
- Cosmetics (color, spinners, progress, pagers, decorative output) TTY-detect always; suppress
  when piped, `NO_COLOR`, `TERM=dumb`, or `--json`.
- List commands in `--json` mode: NDJSON (one object per line) — enables streaming and `jq`
  piping without buffering. For paginated results with metadata, a JSON object with an
  `items` array is acceptable.
- For high-frequency agent calls, offer `--compact`: same JSON shape, minimal whitespace, only
  essential fields (drop verbose/derived ones). `--json` stays the full-fidelity default;
  `--compact` is the opt-in mode for commands an agent calls in a loop, where per-call token
  cost compounds.

### Structured errors

- Error objects on stderr when `--json` is active:
  `{"error": "<snake_case_code>", "message": "<sentence>", "hint": "<exact CLI invocation or null>"}`
- `hint` must be an executable command the agent can run directly — not a prose suggestion.
  Good: `"hint": "snapr list --json"`. Bad: `"hint": "Check available snapshots first."`.

### Exit codes (typed)

- Extend the base set (`0` success, `1` generic runtime, `2` usage/validation) with a small
  fixed table applied identically across every subcommand, so agents branch on the code instead
  of parsing stderr: `3` not-found, `4` auth/permission, `5` upstream/network, `7`
  conflict/precondition. Skip codes that don't apply; never renumber once shipped — the table is
  a contract.
- Pair each non-zero code with the `hint` command an agent can run to recover.

### Reduce tool calls

- Compound output: `create` returns the new resource's ID and key fields; `delete` echoes
  what was removed. Agents should not need a follow-up call to discover the result.
- Rich JSON defaults: in `--json` mode, return full objects. Include enough context that a
  second call is rarely needed.
- Bounded lists: default to a safe limit (e.g., 50 items) with `--limit` to adjust. In JSON
  mode, include `has_more` (bool) and optionally `next_cursor` for keyset pagination.
- Consistent patterns: same flag names across subcommands for the same concept. Agents learn
  the pattern once and apply it everywhere.
- Idempotency: document which commands are safe to repeat. Agents rely on idempotency for
  error recovery without human intervention.

### Stateful CLIs (local cache)

Most CLIs are stateless wrappers — one command, one backend call. When the same data is queried
repeatedly, or the useful questions span entities the backend can't join in a single call, a
local cache earns its complexity:

- `sync` pulls from the backend incrementally (cursor / updated-since) into local SQLite.
  Subsequent `list`/`search`/`filter` read local — fast, offline, rate-limit-free, deterministic
  within a snapshot.
- Full-text search (SQLite FTS5) and compound queries (joins/aggregates the API has no single
  endpoint for, e.g. "stale items whose blockers sat >7d") collapse N round-trips into one local
  command — the largest token and tool-call saving available.
- Cost: staleness, local storage, sync/cursor logic, schema design. Adopt only when the access
  pattern justifies it; a pure pass-through CLI stays stateless.

**Data Layer Decision (required scorecard).** For any CLI that reads from a backend, score these
five signals and record the verdict in the spec — a `stateless` verdict must be as deliberate as
an `adopt` one:

| # | Signal | Weight |
|---|--------|--------|
| 1 | Read-heavy (reads ≫ writes) | support |
| 2 | Same data feeds many questions over time | support |
| 3 | Compound / cross-entity / cross-period queries the API has no single endpoint for | STRONG |
| 4 | Fetches expensive or rate-limited | support |
| 5 | Staleness-tolerant (NOT must-be-live) | GATE |

- Signal 5 = NO → **stateless** (live correctness wins; stop here).
- Signal 3 = YES and 5 = YES → **adopt cache** (the decisive combo).
- Otherwise: 2+ support signals and 5 = YES → adopt; else stateless.

If `adopt`, the spec must specify: the `sync` command (incremental cursor), a local schema sketch
(tables + FTS5 if search is needed), which reads go local vs stay live, and that writes always go
live with a read-after-write invalidation note.

### Wrapping an existing API (input provenance)

When the CLI wraps an existing backend, the command surface is *derived from* an API rather than
designed from scratch. Record where that surface came from:

- API source: documented (OpenAPI / docs), a live URL, or a **HAR capture** (DevTools → Network →
  Export) that surfaces an undocumented, browser-fed API where no official spec exists.
- Map the endpoint inventory to the command tree and keep a **provenance appendix**: each
  subcommand ← method + path + a sample request.

A HAR / undocumented source adds five non-negotiable design checks:

- **Secrets are radioactive.** A HAR contains live cookies, tokens, and PII. Extract *structure*
  only; parameterize every credential as an env-var NAME, never echo a value into the spec.
- **Auth model.** Identify cookie / bearer / CSRF and how it enters the CLI (env var, never a
  flag); note token expiry.
- **Coverage.** A HAR is sampled — only endpoints you exercised appear. Mark which surfaces are
  covered vs unknown; don't imply completeness.
- **Fragility.** Undocumented endpoints have no contract and can change without notice. Isolate
  the endpoint-mapping layer so a break is a one-line patch.
- **ToS/legal.** Replaying a private API may violate terms. Surface this to the user; never assume.

### Discovery

- Compact top-level `--help`: tight enough that the agent reads it once and maps the full
  surface area without nested help-diving.
- The spec document (not `--help`) is the primary agent reference when the tool is used in a
  skill or CLAUDE.md context. Design it to be read by a model, not just by a person.

### Spec compactness

The CLI spec produced by this skill typically lives inside a skill body or as an embedded
reference block, not as a standalone file in the user's repo. This means the spec must be
compact enough to fit in an agent's context budget. Redundant sections should be omitted
(not just marked optional), and examples should be dense — demonstrate multiple patterns
in a single invocation rather than one-pattern-per-line.

## Authors

Original "Command Line Interface Guidelines" authors (and many contributors): Aanand Prasad, Ben Firshman, Carl Tashian, Eva Parish. Design by Mark Hurrell.
