# claude-research ![v0.2.4](https://img.shields.io/badge/v0.2.4-blue?style=flat-square)

Cross-platform research skills for Claude Code.

## Installation

```
/plugin marketplace add gupsammy/claudest
/plugin install claude-research@claudest
```

## Skills

### run-research

Multi-source research across Reddit, X/Twitter, YouTube, and the web. Surfaces what people are actually discussing, recommending, and debating right now. Classifies intent (recommendations, news, prompting, general) and produces a structured report with real citations and a stats block.

Triggers on "research a topic", "run-research", "what's happening with X", "find the best X", "X prompts", "latest on X", "X news".

**Prerequisites:**

```bash
# Reddit (optional but recommended — one-command installer)
curl -fsSL https://raw.githubusercontent.com/gupsammy/reddit-cli/main/install.sh | bash

# X / Twitter (optional)
brew install bird         # or follow https://github.com/steipete/bird

# YouTube (optional)
pip install yt-dlp

# Web (optional — one-command installer; falls back to Claude's native WebSearch)
curl -fsSL https://raw.githubusercontent.com/gupsammy/brave-cli/main/install.sh | sh
```

If reddit-cli or brave-cli is not installed when you run a research task, the skill will detect this automatically and offer to install it for you before proceeding.

### search-youtube

YouTube research toolkit built on `yt-dlp` (`yt_research.py` v0.2.0). Operates in two modes.

Toolkit mode handles individual operations: search, transcript, metadata, audio extraction, channel scanning, and batch processing. The v0.2.0 CLI is agent-aware — all subcommands produce clean, parseable output with well-defined exit codes, making it safe to invoke from Task agents without shell quoting issues or ambiguous failure modes.

Research mode runs an adaptive multi-round discovery pipeline designed for niche and emerging topics where popular videos often under-serve. Round 1 spawns parallel Task agents across 4-6 query variants and applies niche-first heuristics during evaluation — preferring small technical channels and penalizing high-view generalist content on narrow topics. Round 2 drills into the strongest channels and refines queries using terminology from Round 1 hits. Round 3 confirms candidates via metadata, downloads transcripts in parallel Task agents, and synthesizes a structured report with cross-referenced findings, source attribution, and gaps in coverage.

Triggers on "search YouTube", "find videos about", "get a transcript", "download subtitles", "extract audio from YouTube", "scan a channel", "research a topic on YouTube", "summarize this video", "what is this video about", "analyze a channel", "batch download transcripts".

**Prerequisite:**

```bash
pip install yt-dlp
```

## License

MIT
