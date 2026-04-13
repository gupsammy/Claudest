#!/usr/bin/env python3
"""
SessionStart hook: detect resume sessions and write a pending-warn flag.

When source == "resume", reads the transcript JSONL to find the last
cache_creation_input_tokens value, then writes a flag file so
cache-expiry-warn.py can block the first UserPromptSubmit with a cost warning.

Flag file: ~/.claude-memory/cache-warn/resume-pending-<session_id>.json
  - cached_tokens: last known cache_creation_input_tokens from transcript
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CACHE_WARN_DIR = Path.home() / ".claude-memory" / "cache-warn"
_CLAUDE_DIR = Path.home() / ".claude"


def _safe_state_path(cache_dir: Path, prefix: str, session_id: str) -> Path | None:
    """Return resolved path only if it stays within cache_dir; else None."""
    candidate = (cache_dir / f"{prefix}{session_id}.json").resolve()
    try:
        candidate.relative_to(cache_dir.resolve())
        return candidate
    except ValueError:
        return None


def get_cached_tokens(transcript_path: str) -> int:
    """Read last assistant message usage from transcript JSONL."""
    path = Path(transcript_path)
    try:
        resolved = path.resolve()
        resolved.relative_to(_CLAUDE_DIR.resolve())
    except ValueError:
        return 0
    if not path.exists():
        return 0

    cached_tokens = 0
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Look for assistant turns with usage stats
            msg = entry.get("message", {})
            if msg.get("role") == "assistant":
                usage = msg.get("usage", {})
                ct = usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                if ct:
                    cached_tokens = ct
    except OSError:
        pass

    return cached_tokens


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    if data.get("source") != "resume":
        return

    session_id = data.get("session_id", "")
    transcript_path = data.get("transcript_path", "")

    if not session_id:
        return

    cached_tokens = get_cached_tokens(transcript_path)

    CACHE_WARN_DIR.mkdir(parents=True, exist_ok=True)
    flag_path = _safe_state_path(CACHE_WARN_DIR, "resume-pending-", session_id)
    if flag_path is None:
        return
    flag_path.write_text(json.dumps({
        "session_id": session_id,
        "cached_tokens": cached_tokens,
    }))


if __name__ == "__main__":
    main()
