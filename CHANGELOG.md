# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2026-04-08]

### Fixed
- Add `encoding="utf-8"` to all `read_text`/`write_text` calls across hooks and scripts to prevent silent data loss on non-UTF-8 Windows locales (claude-memory) — thanks to @lalalavir for identifying the root cause in #44

## [2026-04-05]

### Added
- Add `get-token-insights` skill with token analysis pipeline, normalization, and interactive dashboard (claude-memory)
- Add `get-pr-comments` skill for fetching and organizing PR review feedback with text-output optimization (claude-coding)
- Add `/clear` handoff system to hard-link cleared sessions in context injection (claude-memory)
- Add session UUID and session origin marker to injected context for debugging and recall (claude-memory)
- Add SessionEnd handoff contract tests (claude-memory)
- Add comprehensive test suite for get-pr-comments (claude-coding)

### Changed
- Migrate `/clear` handoff from UserPromptSubmit to SessionEnd hook (claude-memory)
- Normalize worktree paths to base repo for project key (claude-memory)
- Trim agent descriptions and drop example blocks from skills for token efficiency (claude-skills)
- Skip claude-review workflow when actor is claude[bot] (CI)

### Fixed
- Add end_reason guard to clear-handoff.py and restore contract test (claude-memory)
- Address PR review must-fixes: exception guard, stale unlink, end_reason handling (claude-memory)
- Remove stale `<example>` instruction from create-agent, tighten CLAUDE.md (claude-skills)
- Update stale --strict help text, add __future__ annotations (claude-skills)
- Align agent tooling with no-examples convention (claude-skills)

## [2026-03-27]

### Added
- Add plugin agents (memory-auditor, signal-discoverer) for extract-learnings consolidation (claude-memory)
- Add precomputed context summaries for session injection on startup (claude-memory)
- Add consolidation mode to extract-learnings with check hook nudge (claude-memory)
- Add disposition tag, topic rendering, and gap summaries to session selection (claude-memory)
- Add tool counts to branch metadata and verbose output (claude-memory)
- Add council skill for multi-perspective deliberation (claude-thinking)
- Add code-auditor and architecture-auditor agents (claude-coding)

### Changed
- Rewrite extract-learnings as unified subagent workflow (claude-memory)
- Widen context summary window to first 2 + last 6 exchanges (claude-memory)
- Tighten skill descriptions for context efficiency across plugins (claude-skills, claude-coding)
- Enforce dynamic content injection with examples and severity bump (claude-skills)

### Fixed
- Add cross-platform hook runner for Windows Git Bash compatibility (claude-memory)
- Fix key conflicts in import_conversations and add missing indexes (claude-memory)
- Scope agent Bash access and add phase exit conditions (claude-memory)
- Add WAL mode consistency to backfill check (claude-memory)
- Fix routing accuracy and early invocation for generate-image skill (claude-content)
- Add cross-skill routing boundaries and fix broken reference (claude-skills)
- Fix variable-binding preservation in meta-skills (claude-skills)

## [2026-03-17]

### Added
- Enrich session context injection with branch name, dates, topic summary, and tool usage statistics (claude-memory)

### Changed
- Rewrite setup-github-actions with Anthropic workflow patterns and project-customized review prompts (claude-coding)
- Replace PyYAML dependency with stdlib-only frontmatter parser in skill validator (claude-skills)
- Simplify skill frontmatter for make-changelog and update-readme skills (claude-coding)

### Removed
- Remove unreliable `model`, `context: fork`, and `agent` frontmatter options from skill authoring tools (claude-skills)

### Fixed
- Rename review workflow canonical name to claude-code-review.yml (claude-coding)

## [2026-03-10]

### Added
- Add `setup-github-actions` command to claude-coding for GitHub Actions workflow setup and validation
- Add test coverage for FTS sanitization (14 cases), context injection (9 cases), database migration (4 cases), session search (11 cases), and import pipeline (3 cases)
- Add allowed-tools support and audit rubric to create-cli skill

### Changed
- Improve create-cli skill with separate design vs audit paths and new quality check phase

### Fixed
- Harden FTS injection prevention by stripping FTS5 `-` and `^` operators in `sanitize_fts_term`
- Fix vacuous and weak test assertions in test suite (4 existing tests strengthened)
- Correct YAML frontmatter in clean-branches, make-readme, and improve-skill (block sequence format)
- Clarify push-only-if-requested behavior in commit skill

## [2026-03-05]

### Added
- Add haiku+AskUserQuestion incompatibility constraint to create-skill and repair-skill frontmatter reference docs (claude-skills 0.2.3)
- Add YAML list format constraint for `allowed-tools` to frontmatter reference docs (claude-skills 0.2.3)
- Add `find-candidates.sh` script to clean-branches for isolated candidate detection (claude-coding 0.1.13)

### Fixed
- Correct `allowed-tools` frontmatter to use YAML block sequence in clean-branches, make-readme, and improve-skill (claude-coding 0.1.13, claude-skills 0.2.3)
- Remove `model: haiku` from clean-branches skill — incompatible with AskUserQuestion (claude-coding 0.1.13)

## [2026-03-04]

### Added
- Add explicit output modes (`--json`, `--markdown`, `--text` flags) to create-cli skill, replacing TTY auto-detection (claude-skills 0.2.2)

### Fixed
- Make push-pr branch management non-destructive: use origin-based comparisons and avoid deleting local branches (claude-coding 0.1.12)
- Align create-cli structured error output with explicit output mode contract (claude-skills 0.2.2)

## [2026-03-02]

### Fixed
- Update skill formatting and year references in recall-conversations and run-research (claude-memory 0.7.8, claude-research 0.1.5)

## [2026-02-28]

### Added
- Add Editing Failure Modes guide to generate-image covering color label drift, high-contrast swap targets, Gemini camera move limitations, multi-pass editing, and base scene contamination (claude-content 0.3.1)
- Add Fashion & Garment Editing reference section with base image selection, garment swap prompts, and multi-step editing patterns (claude-content 0.3.1)
- Add Working from Video References section with frame extraction and scene detection techniques (claude-content 0.3.1)
- Extend generate-image thinking mode guidance to cover multi-reference composition, text/sign placement, and complex spatial edits (claude-content 0.3.1)

### Fixed
- Add YAML quoting rule for `argument-hint` frontmatter values containing brackets to prevent parse errors (claude-skills 0.2.1)

## [2026-02-27]

### Added
- Add discovery validation, edge case testing, and description quality rules to skill-lint (claude-skills 0.2.0)
- Add create-claw-skill to claude-claw for OpenClaw ecosystem skill authoring with full OpenClaw adaptation (claude-claw 0.2.0)
- Add repair-agent skill to claude-skills for auditing and improving agent files (claude-skills 0.1.10)

### Changed
- Upgrade generate-image to dual-model tier system with Pro/Flash quality tiers (claude-content 0.3.0)
- Improve create-claw-skill with porting support and OpenClaw spec alignment
- Refactor create-skill and create-agent workflows: remove live documentation fetching, establish reference files as authoritative sources, restructure phases and delivery steps
- Update Bash tool scopes in clean-branches to restrict to git and python3 operations
- Add context:fork to update-readme skill for isolated parallel research workflow

### Fixed
- Remove context:fork rule from improve-skill Phase 2d
- Remove redundant agent:general-purpose defaults from commit, make-changelog, and update-readme skills
- Correct context:fork and agent: frontmatter semantics in repair-skill and create-skill documentation

## [2026-02-24]

### Added
- Add create-agent skill to claude-skills with scripts, references, and examples (claude-skills 0.1.9)
- Add language selection to create-cli skill (claude-skills 0.1.8)
- Add one-command installers for reddit-cli and brave-cli in run-research skill
- Add claude-claw plugin (v0.1.0) with claw-advisor skill for OpenClaw configuration, troubleshooting, and guidance
- Add create-cli skill for structured agent-aware command-line application design
- Add update-readme skill for automated README refresh using codebase state and git history
- Overhaul youtube-research with adaptive multi-round discovery pipeline featuring parallel Task agents and niche-first heuristics
- Add improve-skill to claude-skills alongside create-skill and repair-skill (claude-skills 0.1.4)
- Add make-changelog skill and rename skills to make-* convention in claude-coding (claude-coding 0.1.8)
- Add readme-maker skill to claude-coding (claude-coding 0.1.5)
- Migrate deep-research and youtube-research into dedicated claude-research plugin
- Add claude-content plugin with ffmpeg-based content creation skills (compress-video, convert-video, make-gif, share-social, extract-audio, generate-image)
- Add claude-thinking plugin with brainstorm skill
- Add updateclaudemd skill to claude-coding (claude-coding 0.1.4)
- Add push-pr skill with multi-PR scope analysis and PR body script to claude-coding
- Add commit and clean-branches skills to claude-coding git workflow plugin
- Add claude-skills plugin with create-skill and repair-skill
- Add past-conversations and extract-learnings skills to claude-memory for conversation recall and knowledge synthesis
- Add branch-level full-text search index for improved session search performance
- Add tool usage tracking and reporting in conversation memory
- Add cross-platform FTS fallback support for improved compatibility
- Add claude-utilities plugin with convert-to-markdown skill
- Add extract-learnings skill for distilling conversation learnings into persistent memory (claude-memory 0.6.0)
- Add comprehensive test suite for claude-memory with pytest

### Changed
- Rename all skills and commands to verb-first convention for consistent imperative naming across plugins
- Improve create-cli with agent-aware design patterns
- Implement security hardening and performance improvements in claude-memory (claude-memory 0.7.0)
- Migrate to v3 schema with branch-aware conversation storage for better context tracking
- Filter teammate messages and task notifications from injected context to reduce noise (claude-memory 0.7.1)
- Refactor past-conversations skill with reference extraction and improved formatting
- Repair and improve multiple skills per audit findings (extract-learnings, web-to-markdown, skill-creator, skill-repair, thinking-partner, clean-branches, commit)
- Split memory_utils into modular package structure (db.py, content.py, parsing.py, formatting.py)
- Add shared utilities, settings, and logging support to claude-memory

### Fixed
- Repair YAML frontmatter parse errors in brainstorm, clean-branches, and create-skill skills
- Fix foreign key crash on conversation reimport and add versioned data migrations (claude-memory 0.7.2)
- Handle None channel gracefully in YouTube research format_entry
- Escape dynamic syntax pattern in skill documentation to prevent parsing errors
- Remove redundant name field from skill frontmatter

## [2026-02-23]

### Changed
- Rename all skills and commands to verb-first convention across 5 plugins: recall-conversations, manage-memory, convert-to-markdown, brainstorm, run-research, search-youtube, generate-image, compress-video, convert-video, make-gif, share-social, extract-audio

### Added
- Add YouTube research skill with yt-dlp integration to claude-utilities
- Add claude-content plugin with content creation skills (ffmpeg suite)
- Add claude-thinking plugin with thinking-partner skill
- Add claude-research plugin with deep-research and youtube-research capabilities
- Add updateclaudemd skill to claude-coding for maintaining CLAUDE.md files
- Add readme-maker skill to claude-coding for automated documentation generation
- Add commit, push-pr, and clean-branches skills to claude-coding for git workflows
- Add skill-creator and skill-repair skills to claude-skills plugin
- Add past-conversations and extract-learnings skills to claude-memory for conversation recall and knowledge synthesis
- Add branch-level full-text search index for improved session search performance
- Add tool usage tracking and reporting in conversation memory

### Changed
- Standardize claude-coding skills to make-* naming convention
- Repair and improve multiple skills per audit findings (extract-learnings, past-conversations, web-to-markdown, skill-creator, skill-repair, thinking-partner)
- Refactor past-conversations skill with reference extraction and improved formatting
- Implement security hardening and performance optimizations in claude-memory
- Add cross-platform support with FTS fallback for improved compatibility
- Migrate to v3 schema with branch-aware conversation storage for better context tracking
- Filter teammate messages and task notifications from injected context to reduce noise

### Fixed
- Handle None channel gracefully in YouTube research format_entry
- Prevent foreign key constraint crash on conversation reimport
- Remove redundant name field from skill frontmatter
- Escape dynamic syntax pattern in skill documentation to prevent parsing errors
