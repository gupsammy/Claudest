---
name: web-to-markdown
description: >
  Convert any webpage to clean markdown using ezycopy CLI. Use when user asks to
  "convert this page to markdown", "extract this webpage", "save this article",
  "grab content from URL", "get markdown from this link", "scrape this page",
  provides a URL to extract, or wants clean web content without ads and clutter.
---

# EzyCopy CLI

Extracts clean markdown from URLs. Default: fast HTTP fetch. Use `--browser` for Chrome when needed.

## Usage

```
ezycopy <URL> [flags]
```

**Flags:**
- `-c` — Copy output to clipboard
- `-o <path>` — Save to file/directory
- `--browser` — Use Chrome (for JS-heavy or authenticated sites)
- `--no-images` — Strip image links
- `-t <duration>` — Timeout (default: 30s)

## When to use `--browser`

- Twitter/X, SPAs, or JS-rendered sites
- Authenticated/paywalled content
- If default returns empty or suspiciously short content

## Execution Notes

- When using `--browser` mode, run as a foreground process
- Don't use `2>&1` - let stderr flow naturally

## Install

If not installed: `curl -sSL https://raw.githubusercontent.com/gupsammy/EzyCopy/main/install.sh | sh`
