---
name: create-cli
description: >
  This skill should be used when the user asks to "design a CLI", "help me design
  command-line flags", "what flags should my tool have", "create a CLI spec",
  "refactor my CLI interface", "design a CLI my agent can call", or wants to design
  command-line UX (args/flags/subcommands/help/output/errors/config) before
  implementation or audit an existing CLI surface for consistency and composability.
argument-hint: "[tool-name and one-line description]"
allowed-tools:
  - AskUserQuestion
  - Read
  - Glob
  - Grep
  - Bash
  - Write
---

# Create CLI

Design CLI surface area (syntax + behavior), agent-aware, human-friendly.

## Phase 1 — Prepare

Read `${CLAUDE_PLUGIN_ROOT}/skills/create-cli/references/cli-guidelines.md`. Apply it as the default CLI rubric, including the Agent Ergonomics section.

For new CLI designs, also read `${CLAUDE_PLUGIN_ROOT}/skills/create-cli/references/language-selection.md` to inform the language recommendation in Phase 2. Skip it for audits — the language is already chosen.

Proceed when cli-guidelines.md is loaded.

## Phase 2 — Clarify

Determine whether this is a **new design** or an **audit** from the user's trigger.

### New design

Ask, then proceed with best-guess defaults if user is unsure:

- Command name + one-sentence purpose.
- Primary consumer: agent/LLM, human at a terminal, scripted automation, or mixed.
- Input sources: args vs stdin; files vs URLs; secrets (never via flags).
- Output contract: human text by default, `--json` for structured output, exit codes.
- Backend/data layer: does it wrap an existing API? Source of the surface — OpenAPI/docs, live URL,
  or HAR capture (undocumented/browser-fed)? (cli-guidelines.md → Wrapping an existing API.)
- Interactivity: prompts allowed? need `--no-input`? confirmations for destructive ops?
- Config model: flags/env/config-file; precedence; XDG vs repo-local.
- Language & distribution: ask for the user's preferred implementation language, or offer to
  recommend one. Ask whether a single binary (no runtime needed on target machine) is required,
  or whether a runtime dependency is acceptable. Apply language-selection.md to recommend if
  the user is unsure. Platform: macOS/Linux/Windows.

If an existing CLI spec or tool description is provided, read it first — skip questions already answered by it.

**Data Layer gate** — required when the tool reads from a backend. Run the Data Layer Decision
scorecard (cli-guidelines.md → Stateful CLIs); record `adopt cache` or `stateless` + a one-line
rationale. Decide this before drawing the command tree — it changes whether `sync`/local-read
subcommands exist at all.

### Audit

Ask:

- CLI name and source location (repo path, or provide `--help` output).
- Primary consumer: agent, human, or mixed.
- Known pain points or specific areas to focus on.

Then explore the codebase: use Glob/Grep to find command definitions, flag registrations, output formatting, and error handling. Run `<cli> --help` via Bash to capture actual behavior.

Proceed when answers are confirmed or user is unsure — use best-guess defaults.

## Phase 3 — Conventions

Apply the conventions from cli-guidelines.md (loaded in Phase 1), including the Agent Ergonomics section. The rules below are the key conventions to enforce — cli-guidelines.md provides the full rubric for edge cases.

If primary consumer is human-only, the Errors and Reduce Tool Calls subsections are optional — apply them only if the user wants script-friendliness.

### Output
- Default output is human-readable text; an explicit `--json`/`--plain` flag sets the data format and overrides TTY state. Cosmetics (color, spinners) may TTY-detect; the data format must not rely on it (see cli-guidelines.md → Agent Ergonomics for the TTY/PTY rationale).
- List commands in `--json` mode use NDJSON (one JSON object per line) — enables streaming and `jq` piping without buffering. For paginated results with metadata, a JSON object with an `items` array is acceptable. If the CLI extends an existing ecosystem that uses JSON arrays (kubectl, aws, gh), match the ecosystem convention.
- Primary data to stdout; diagnostics/errors to stderr.
- Suppress ANSI codes, progress spinners, and decorative output when `--json` is passed or when stdout is not a TTY.

### Errors (agent/mixed consumers only)
- When `--json` is active, emit error objects on stderr: `{"error": "<snake_case_code>", "message": "...", "hint": "<exact CLI invocation or null>"}` — so agent callers can route recovery logic without parsing free-text stderr. The `hint` field must be an executable command, not prose.
- Exit codes: `0` success, `1` runtime error, `2` invalid usage; for agent/mixed consumers, extend with the typed table from cli-guidelines.md → Exit codes (typed) (`3` not-found, `4` auth, `5` upstream, `7` conflict) — apply identically across all subcommands so agents branch on the code.

### Flags
- `-h/--help` always shows help; ignores other args.
- `--version` prints version to stdout.
- `--json` preferred for structured output. `--output json`/`-o json` acceptable when the CLI needs multiple output formats (yaml, table, csv) under a single flag. Pick one and apply consistently.
- For commands an agent calls in a loop, offer `--compact` (opt-in): same JSON shape, minimal whitespace, essential fields only — `--json` stays the full-fidelity default. See cli-guidelines.md → Output defaults.
- Consistent flag names across all subcommands for the same concept (`--id`, `--force`, `--json`) — agents learn the naming pattern once and apply it everywhere without guessing.
- Prompts only when stdin is a TTY; `--no-input` disables prompts. `--non-interactive` acceptable if the ecosystem already uses it.
- Destructive operations: interactive confirmation; non-interactive requires `--force`.
- Respect `NO_COLOR`, `TERM=dumb`; provide `--no-color`.
- Handle Ctrl-C: exit fast; bounded cleanup; crash-only when possible.

### Reduce Tool Calls (agent/mixed consumers only)
- Compound output: operations return enough data to avoid a follow-up call. `create` returns the new resource's ID and key fields. `delete` echoes what was removed.
- Rich JSON defaults: in `--json` mode, return full objects not just IDs.
- Bounded lists: list commands default to a safe limit (e.g., 50 items) with `--limit` to adjust. In JSON mode, include `has_more` (bool) and optionally `next_cursor` for keyset pagination. Unbounded output wastes tokens and risks context overflow for agent callers.
- Idempotent by default: where possible, commands are safe to repeat; document preconditions explicitly — agents rely on safe retries for error recovery without human intervention.

Apply all applicable conventions, then proceed to Phase 4.

## Phase 4 — Deliver

### Audits

Evaluate the existing CLI against every Phase 3 subsection. For each convention, state: what the CLI does today, whether it conforms, and what to change. Also check:

- Flag naming consistency across subcommands.
- Help text quality (examples present, common flags first, fits one screen).
- Config precedence (flags > env > project config > user config > defaults).
- Destructive-op safety (confirmations, --force, --dry-run).
- Shell completion availability.
- Data layer fit: if the CLI reads from a backend, run the Data Layer Decision scorecard; flag when it re-fetches live data that a local cache + compound queries would serve (cli-guidelines.md → Stateful CLIs).

Produce a gap report organized by severity: Breaking (requires API change), Major (agent-breaking or convention violation), Minor (cosmetic/polish). Each finding: current behavior, convention violated, recommended fix with migration risk (none/low/breaking).

### New designs

Produce a compact spec the user can implement. Include all relevant sections:

- Command tree + USAGE synopsis.
- Args/flags table (types, defaults, required/optional, examples).
- Subcommand semantics (what each does; idempotence; state changes).
- Output rules: stdout vs stderr; `--json` for structured output; `--quiet`/`--verbose`.
- Error + exit code map (top failure modes).
- Safety rules: `--dry-run`, confirmations, `--force`, `--no-input`.
- Config/env rules + precedence (flags > env > project config > user config > system).
- Data layer decision: `adopt cache` | `stateless` verdict + rationale (required when the tool reads from a backend).
- API provenance (when wrapping an existing API): source + endpoint→command mapping; for HAR, the secret/auth/coverage/fragility/ToS checks.
- Shell completion story (if relevant): install/discoverability; generation command or bundled scripts.
- 5–10 example invocations (common flows; include piped/stdin examples).

Use this skeleton, dropping irrelevant sections:

0. **Language & distribution**: `Go` · `cobra` · single binary · `goreleaser` for CI
   *(Omit if language was not determined.)*
1. **Name**: `mycmd`
2. **One-liner**: `...`
3. **USAGE**:
   - `mycmd [global flags] <subcommand> [args]`
4. **Subcommands**:
   - `mycmd init ...`
   - `mycmd run ...`
5. **Global flags**:
   - `-h, --help`
   - `--version`
   - `-q, --quiet` / `-v, --verbose` (define exactly)
   - `--json` (structured JSON output; NDJSON for list commands)
6. **I/O contract**:
   - stdout:
   - stderr:
7. **Exit codes**:
   - `0` success
   - `1` generic failure
   - `2` invalid usage (parse/validation)
   - (add command-specific codes only when actually useful)
8. **Env/config**:
   - env vars:
   - config file path + precedence:
9. **Data Layer Decision** (required when the tool reads from a backend; omit if no backend):
   `adopt cache` | `stateless` + one-line rationale. If `adopt`: `sync` command, local schema
   sketch, local-vs-live reads, write invalidation note.
10. **API Provenance** (only when wrapping an existing API; omit for from-scratch tools):
    source (OpenAPI/URL/HAR); subcommand ← method+path mapping; for HAR, the five checks.
11. **Examples**:
   - …

See `${CLAUDE_PLUGIN_ROOT}/skills/create-cli/examples/example-cli-spec.md` for a complete worked example.

If the spec is destined for a skill body or CLAUDE.md, omit unused sections entirely (do not mark them "N/A") and limit examples to ≤5 invocations that each demonstrate multiple patterns.

## Phase 5 — Verify

For new specs: confirm the spec covers all applicable sections from the Phase 4 skeleton. Verify the examples section demonstrates at least: `--json` output, error recovery (if agent/mixed consumer), and one piped/stdin usage.

For backend/API-wrapping specs: confirm a Data Layer Decision verdict is recorded with a rationale (not left implicit), and — when the source is a HAR/undocumented API — that no secret values appear anywhere in the spec (only env-var names) and the coverage/fragility caveats are stated.

For audits: confirm the gap report addresses every Phase 3 subsection and includes at least one example invocation showing the recommended fix for each Major finding.

Skill is complete when verification passes.

## Notes

- Once language is selected (Phase 2), include the idiomatic parsing library in the spec (see language-selection.md). If language remains undetermined, omit the library.
- If the request is "design parameters", do not drift into implementation.
