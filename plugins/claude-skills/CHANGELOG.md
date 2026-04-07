# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Align `create-agent` and `repair-agent` with current subagent spec: `tools`/`disallowedTools` use YAML block sequences, updated `effort`/`memory`/`model` semantics
- `memory: local` now means project-specific but not checked into VCS (previously meant in-session only, equivalent to no `memory` field)
- `validate_agent.py`: warn when `effort: max` is paired with a non-Opus model

## [0.3.2] - 2026-03-10

### Changed
- Patch version bump by auto-version hook after version-badge correction commit

## [0.3.1] - 2026-03-10

### Changed
- Patch version bump by auto-version hook after docs refresh commit

## [0.3.0] - 2026-03-10

### Added
- Overhaul `create-cli` with separate design and audit paths: Phase 2 branches between new CLI design and audit of an existing CLI, Phase 4 delivers an audit rubric, Phase 5 adds a quality check pass
- Fold `agent-aware-design.md` into `cli-guidelines.md` to eliminate triple content duplication (~40% context savings per audit invocation)

### Changed
- Expand `create-cli` allowed-tools to include `Glob`, `Grep`, `Bash`, and `Write`

## [0.2.2] - 2026-02-27

### Changed
- Replace TTY auto-detection with explicit output modes (`--json`) in `create-cli` conventions — eliminates surprises for agent callers

## [0.2.1] - 2026-02-27

### Fixed
- Add `argument-hint` YAML quoting rule to `create-skill` and `skill-lint` to prevent frontmatter parse errors on colon-containing hints

## [0.2.0] - 2026-02-26

### Added
- `create-skill`: new Step 3 (discovery validation) tests description routing with should-trigger and shouldn't-trigger prompts before body writing
- `improve-skill`: new sub-analysis 2e (edge case stress test) with adversarial inputs — missing files, contradictory requirements, boundary conditions
- Description quality rules across `create-skill` and `repair-skill`: 100-token budget (150 max), trigger derivation from user language, negative triggers for adjacent domains
- `repair-skill` D1: three new gap checks — trigger accuracy, token budget compliance, negative trigger coverage
- `quality-checklist.md` and `generation-standards.md`: matching checklist items for new quality rules
- `skill-lint`: D1 summary expanded with new sub-checks

## [0.1.11] - 2026-02-24

### Added
- `repair-agent` skill for auditing Claude Code agent files against a gold standard (seven audit dimensions matching `repair-skill` structure)
- `audit-calibration.md` reference loaded by both `repair-skill` and `skill-lint` to avoid known false-positive patterns

## [0.1.10] - 2026-02-24

### Added
- Wire `skill-lint` agent invocation into `create-skill` and `improve-skill` completion flows

### Changed
- Audit and restructure `create-skill` and `create-agent` per canonical best practices; fix `context:fork` decision rule across `create`, `repair`, and `improve` skills
- Remove redundant `agent:general-purpose` references; fix docs

## [0.1.9] - 2026-02-23

### Added
- Add `create-agent` skill with scripts, references, and examples for generating Claude Code agent definitions

## [0.1.8] - 2026-02-23

### Added
- Add language selection feature to `create-cli` skill, supporting multi-language CLI scaffolding

## [0.1.5] - 2025-12-01

### Added
- Add `create-cli` skill for designing CLI surface areas — syntax, flags, subcommands, output contracts, and error codes

### Changed
- Improve `create-cli` with agent-aware design patterns (TTY auto-detection, NDJSON streaming, structured error objects)

## [0.1.4] - 2025-11-15

### Added
- Add `improve-skill` skill for effectiveness auditing of existing skills
- Rename `skill-creator` → `create-skill` and `skill-repair` → `repair-skill` to follow verb-first convention

## [0.1.3] - 2025-11-01

### Changed
- Repair `repair-skill` per audit findings — fix frontmatter and execution modifiers

## [0.1.2] - 2025-10-15

### Changed
- Repair `create-skill` (formerly skill-creator) per audit findings

## [0.1.1] - 2025-10-01

### Fixed
- Escape dynamic syntax pattern in skill documentation to prevent frontmatter parsing errors

## [0.1.0] - 2025-09-15

### Added
- Initial release with `create-skill` and `repair-skill` skills
- Replace placeholder symlinks with real skill directories
- Add shared `references/` library: `skill-anatomy.md`, `frontmatter-options.md`, `script-patterns.md`
