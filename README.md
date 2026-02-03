# Claudest

The only Claude Code plugin marketplace that isn't trash.

## Philosophy

Most plugin marketplaces are filled with half-baked garbage. Skills that are nothing more than wishful thinking—incomplete documents with no scripts, no utilities, no resources. Overcomplicated setups that break on first use. README stars that promise the moon and deliver nothing.

**Claudest is different.**

Every plugin here is something I personally use, build, test, and improve. These aren't weekend experiments or proof-of-concepts. They're tools that have been run hundreds of times across real projects, iterated based on actual friction, and refined until they just work.

If it's in this marketplace, it's battle-tested.

---

## Installation

### Add the Marketplace

**Slash command (inside Claude Code):**
```
/plugin marketplace add gupsammy/claudest
```

**CLI:**
```bash
claude plugin marketplace add gupsammy/claudest
```

### Enable Auto-Updates

Run `/plugin`, go to the **Marketplaces** tab, and toggle **Enable auto-update** for Claudest.

### Install a Plugin

**Slash command:**
```
/plugin install claude-memory@claudest
```

**CLI:**
```bash
claude plugin install claude-memory@claudest
```

---

## Plugins

### claude-memory

Conversation memory that actually solves the right problems.

Most memory solutions are bloated or over-engineered. I built claude-memory to solve the two problems that actually matter:

1. **Immediate context** — Resume exactly where you left off. No manual searching through conversation history.
2. **Recall** — Find that discussion about OAuth from two weeks ago. Full-text search that just works.

There's a third problem I'm still exploring: **long-term learnings** — extracting facts, preferences, and patterns that persist across projects. This is experimental.

No vector search. It felt heavy and unnecessary for most use cases. Still investigating where embeddings actually help and how to do it locally without the latency and storage bloat.

**What's included:**

The **past-conversations** skill handles recall. Ask "what did we discuss about authentication" or "remember when we debugged that API issue" and it searches your history, retrieves relevant sessions, and synthesizes an answer. It also includes a lens system for structured analysis—restore context, extract learnings, find gaps, run retros.

The **/claude-memory** command gives you direct control: sync conversations manually, check database stats, search with custom queries, and manage your memory store.

**How it works:**
- Auto-syncs every session to `~/.claude-memory/conversations.db`
- FTS5 full-text search with Porter stemming and BM25 ranking
- Cross-project search by default, filter by project when needed
- Lens system for structured analysis (restore context, extract learnings, find gaps, run retros)
- Triggers naturally: "remember when", "continue where we left off", "what did we discuss"

**Install:** `/plugin install claude-memory@claudest`

---

### claude-utilities

A growing collection of useful tools.

**Current skills:**

**web-to-markdown** — Convert any webpage to clean markdown. Strips ads, navigation, popups, cookie banners—everything except the actual content. Uses [ezycopy](https://github.com/gupsammy/EzyCopy) under the hood.

```bash
# Prerequisite
curl -sSL https://raw.githubusercontent.com/gupsammy/EzyCopy/main/install.sh | sh
```

Triggers: "convert this page to markdown", "extract this webpage", "save this article", "grab content from URL", "scrape this page"

**Install:** `/plugin install claude-utilities@claudest`

---

## What's Coming

Both plugins will continue to grow, and new plugins covering different capabilities will be added.

**claude-memory:**
- Long-term memory and fact extraction (experimental)

**claude-utilities:**
- More tools as I build and battle-test them
- If I use it daily, it'll end up here

**New plugins:**
- Different capabilities as I find gaps in my workflow and build solutions

---

## Contributing

This isn't a community marketplace where anyone can submit plugins. It's a curated set of tools I personally maintain.

If you have suggestions or find bugs, open an issue. If you want to run your own marketplace with your own battle-tested tools, fork this and make it yours.

---

## License

MIT
