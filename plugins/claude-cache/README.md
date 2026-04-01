# claude-cache ![version](https://img.shields.io/badge/version-0.1.0-blue)

Cache warmth management for Claude Code. Prevents prompt cache expiry from silently burning through your token budget.

## What It Does

Claude API prompt caching has a 5-minute TTL. When you're idle for longer than that, your next turn reprocesses the entire context at full cost — often 10-30x more expensive. This plugin provides:

- Desktop notifications at 3, 4, 4.5, and 5 minutes of idle time
- A block-and-warn gate when cache has expired, recommending `/clear`
- An optional `/keep-warm` skill that pings every 4 minutes to keep the cache alive

## Hooks

| Event | Hook | Purpose |
|-------|------|---------|
| SessionStart | `cache-cleanup.py` | Clean up stale state from previous sessions |
| Stop | `cache-timer-start.py` | Spawn background countdown timer |
| UserPromptSubmit | `cache-check.py` | Kill timer, block if cache expired |

## Skills

| Skill | Trigger | Purpose |
|-------|---------|---------|
| `/keep-warm` | "keep warm", "stepping away", "brb" | Activate cache keepalive pings |

## Requirements

- Python 3.7+
- No external dependencies (stdlib only)

### Desktop Notifications

| Platform | Mechanism | Notes |
|----------|-----------|-------|
| macOS | `osascript` (built-in) | Works out of the box |
| Linux | `notify-send` | Install `libnotify` if not present |
| Windows | BurntToast PowerShell module | Install via `Install-Module BurntToast`. Notifications silently skip if not installed |

Notifications are best-effort — the plugin works without them (the 5-minute block gate is the safety net).
