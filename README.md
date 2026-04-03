<div align="center">

<h1>Claudest</h1>

<p>A curated Claude Code plugin marketplace. Everything here is something I personally use, build, and iterate on across real projects. If it's in this marketplace, it works.</p>

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Release](https://img.shields.io/github/v/release/gupsammy/claudest?style=flat-square)
![Stars](https://img.shields.io/github/stars/gupsammy/claudest?style=social)

</div>

---

## ⚡ Installation

Add the marketplace, then install any plugin:

```
/plugin marketplace add gupsammy/claudest
/plugin install claude-memory@claudest
/plugin install claude-research@claudest
/plugin install claude-coding@claudest
/plugin install claude-skills@claudest
/plugin install claude-thinking@claudest
/plugin install claude-content@claudest
/plugin install claude-utilities@claudest
/plugin install claude-claw@claudest
```

To enable auto-updates, run `/plugin`, go to the Marketplaces tab, and toggle auto-update for Claudest.

---

<a id="claude-memory"></a>

### 🧠 claude-memory &nbsp; ![v0.8.37](https://img.shields.io/badge/v0.8.37-blue?style=flat-square)

Conversation memory for Claude Code. Stores every session in a SQLite database with full-text search (FTS5, BM25 ranking, zero external dependencies) and makes past conversations available to the agent automatically.

- **Automatic context injection** — on every session start, precomputed context summaries from recent sessions are injected automatically. The agent knows what you worked on last time before you say a word.
- **`recall-conversations`** — search conversation history by keywords, browse recent sessions, or run structured analyses like retrospectives and gap-finding.
- **`extract-learnings`** — reads past conversations, identifies non-obvious insights worth preserving, and proposes placing them at the right layer in the memory hierarchy (CLAUDE.md, MEMORY.md, or topic files). Includes a consolidation mode with dedicated `memory-auditor` and `signal-discoverer` agents for batch processing.

For the full story behind the architecture: [What I Learned Building a Memory System for My Coding Agent](https://www.reddit.com/r/ClaudeCode/comments/1r1w397/comment/o5294lk/).

```
/plugin install claude-memory@claudest
```

---

<a id="claude-research"></a>

### 🔍 claude-research &nbsp; ![v0.2.2](https://img.shields.io/badge/v0.2.2-blue?style=flat-square)

Cross-platform research skills for Claude Code.

- **`run-research`** — autonomous research agent that queries Reddit, X/Twitter, YouTube, and the web simultaneously, then synthesizes findings weighted by engagement. Sources are detected at runtime — missing tools are skipped silently.
- **`search-youtube`** — YouTube research toolkit built on `yt-dlp`. Search with filters, extract transcripts, pull audio, scan channels, and batch process URLs. Research mode runs adaptive multi-round discovery with parallel agents.

```bash
# Prerequisites — install only what you need, each source is optional
pip install yt-dlp           # YouTube (used by both skills)
pip install reddit-cli       # Reddit
brew install bird            # X / Twitter
# brave-cli — one-command installer included in run-research skill output
# Web search falls back to Claude's native WebSearch if brave-cli is missing
```

```
/plugin install claude-research@claudest
```

---

<a id="claude-coding"></a>

### 💻 claude-coding &nbsp; ![v0.2.18](https://img.shields.io/badge/v0.2.18-blue?style=flat-square)

Coding workflow skills for Claude Code. Eight skills and two agents covering the commit loop, project maintenance, documentation, and code quality.

- **`commit`** — analyzes changes, groups files by purpose, runs linters, writes conventional commit messages. Splits multi-concern changes automatically.
- **`push-pr`** — cuts a feature branch if needed, pushes, and creates or updates a PR. Calls `commit` first if there are uncommitted changes.
- **`clean-branches`** — finds merged and stale branches, confirms before deleting. Protected branches are never touched.
- **`update-claudemd`** — audits and optimizes your CLAUDE.md against the actual codebase. Creates a backup before writing.
- **`make-readme`** — generates a README through a structured interview with shields.io badges and styled headers.
- **`make-changelog`** — creates or updates CHANGELOG.md from git history using Keep-a-Changelog format. Parallel Haiku subagents for token efficiency.
- **`update-readme`** — refreshes an existing README using codebase state and git history with three parallel research agents.
- **`setup-github-actions`** — scaffolds and validates GitHub Actions workflows for your project.
- **`code-auditor`** — proactive code quality agent for readability, correctness, dead code, and naming consistency.
- **`architecture-auditor`** — architectural review agent for cross-module boundary design and structural decisions.

```
/plugin install claude-coding@claudest
```

---

<a id="claude-skills"></a>

### ✍️ claude-skills &nbsp; ![v0.5.15](https://img.shields.io/badge/v0.5.15-blue?style=flat-square)

Skill authoring tools for Claude Code. Six skills covering the full lifecycle from creation to repair.

- **`create-skill`** — interviews you about requirements, fetches latest Claude Code docs, generates a skill with correct frontmatter and progressive disclosure structure. Scans for steps that should be CLI tools.
- **`repair-skill`** — structural audit across seven dimensions: frontmatter quality, agentic/deterministic split, verbosity, workflow clarity, and more. Distinguishes violations from gaps.
- **`improve-skill`** — effectiveness audit. Models user intent, walks through the skill as Claude would, verifies claims against current docs, scans for missing capabilities.
- **`create-agent`** — generates Claude Code agent files with correct YAML frontmatter, tool access, isolation, and triggering conditions.
- **`repair-agent`** — audits agent files for system prompt quality, tool scope, context isolation, and triggering correctness.
- **`create-cli`** — designs a complete CLI surface (flags, subcommands, output format, error schema) through a structured interview. Agent-aware by default.

A `skill-lint` agent runs automatically after `create-skill` and `improve-skill` to validate structural quality before delivery.

```
/plugin install claude-skills@claudest
```

---

<a id="claude-thinking"></a>

### 🧠 claude-thinking &nbsp; ![v0.3.2](https://img.shields.io/badge/v0.3.2-blue?style=flat-square)

Structured thinking and multi-perspective deliberation tools for Claude Code.

- **`brainstorm`** — in-depth interview calibrated to the domain: adversarial for strategy, gentle for personal decisions, Socratic for abstract topics. Detects saturation and produces a synthesis document.
- **`council`** — spawns parallel agents with distinct cognitive personas (Architect, Skeptic, Pragmatist, Innovator, Advocate, Strategist) to deliberate on a question. Produces a dialectical synthesis with consensus, tensions, and verdict.

```
/plugin install claude-thinking@claudest
```

---

<a id="claude-content"></a>

### 🎬 claude-content &nbsp; ![v0.4.4](https://img.shields.io/badge/v0.4.4-blue?style=flat-square)

Content creation and processing tools for Claude Code. Image generation and the full video/audio manipulation workflow.

- **`generate-image`** — Gemini API for text-to-image, image-to-image editing, and multi-reference composition. Two tiers: Nano Banana (fast, extended aspect ratios, thinking mode) and Nano Banana Pro (2K resolution).
- **`compress-video`** — quality-based (CRF) or size-based (2-pass) encoding. Profiles the source first, then applies the right strategy.
- **`convert-video`** — general-purpose manipulation: format conversion, trim, speed, slow motion, timelapse, frame extraction, resize, rotate, flip, remux. Multi-operation requests chain into a single ffmpeg invocation.
- **`make-gif`** — mandatory 2-pass palette workflow for correct color reproduction. No banding artifacts.
- **`share-social`** — platform-specific video prep. Presets for Instagram, YouTube Shorts, TikTok, Twitter, Facebook, and LinkedIn.
- **`extract-audio`** — rips audio from video with format selection: FLAC, MP3 VBR, or AAC.

Requires `ffmpeg` and `ffprobe`. Image generation additionally requires `GEMINI_API_KEY` and `uv`.

```
/plugin install claude-content@claudest
```

---

<a id="claude-utilities"></a>

### 🔧 claude-utilities &nbsp; ![v0.2.2](https://img.shields.io/badge/v0.2.2-blue?style=flat-square)

Useful tools that don't fit in a specific plugin.

- **`convert-to-markdown`** — converts any webpage to clean markdown, stripping ads, navigation, popups, and cookie banners. Uses [ezycopy](https://github.com/gupsammy/EzyCopy) under the hood.

```bash
# Prerequisite
curl -sSL https://raw.githubusercontent.com/gupsammy/EzyCopy/main/install.sh | sh
```

```
/plugin install claude-utilities@claudest
```

---

<a id="claude-claw"></a>

### 🦞 claude-claw &nbsp; ![v0.3.2](https://img.shields.io/badge/v0.3.2-blue?style=flat-square)

OpenClaw advisory, troubleshooting, and configuration guidance for Claude Code. OpenClaw is a local AI gateway that routes model requests, manages channels, and handles multi-model configuration.

- **`claw-advisor`** — answers OpenClaw questions, suggests optimal configuration, and diagnoses issues. Uses `clawdocs` for documentation lookup and `openclaw` for live state inspection. Responses include exact config keys with full dot-paths.
- **`create-claw-skill`** — generates new OpenClaw-compatible skills adapted to clawhub frontmatter conventions and OpenClaw-specific tool access patterns.

```
/plugin install claude-claw@claudest
```

---

## 🤝 Contributing

Contributions are welcome. This is a curated set of tools I personally maintain, so the bar for inclusion is that it works well enough to use daily — but bug reports, feature ideas, and PRs that improve existing plugins are all appreciated. Open an issue first for anything substantial so we can align on direction.

If you want to build your own marketplace with your own battle-tested tools, fork this and make it yours — that's what the plugin system is designed for.

## 📄 License

MIT
