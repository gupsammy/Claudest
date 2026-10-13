#!/usr/bin/env python3
"""
UserPromptSubmit hook: warn once when prompt cache has expired.

Claude Code uses the 1-hour prompt cache TTL (ephemeral_1h). When the user
has been idle >1 hour since Claude last responded, the cached context
will expire on this turn — costing full re-creation tokens. This hook fires
before the prompt is sent and blocks once per idle gap so the user can
compact before the expensive re-creation happens.

Resumed sessions need no special case: resume keeps the session_id, so the
idle gap is measured the same way. When no state file exists (Stop never
fired under this hook), the last assistant timestamp in the transcript is
used instead.

State: ~/.claude-memory/cache-warn/<session_id>.json (written by cache-warn-stop.py)
  - last_stop_time: ISO timestamp of last Claude response
  - warned_gaps: list of gap buckets already warned — prevents double-warn

Gap bucket = floor(last_stop_time_unix / 60) — stable per idle gap even if
the hook fires multiple times after the same idle period.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CACHE_TTL_SECONDS = 3600       # 1-hour prompt cache TTL (ephemeral_1h, reverted ~2026-04-13)
WARN_THRESHOLD_SECONDS = 3600  # Warn at exactly 1 hr (TTL expiry)
CACHE_WARN_DIR = Path.home() / ".claude-memory" / "cache-warn"
_CLAUDE_DIR = Path.home() / ".claude"
_TRANSCRIPT_TAIL_BYTES = 512 * 1024


def _gap_bucket(last_stop_time_iso: str) -> str:
    """Stable ID for this idle gap — floor of last_stop_time in whole minutes."""
    dt = datetime.fromisoformat(last_stop_time_iso.replace("Z", "+00:00"))
    return str(math.floor(dt.timestamp() / 60))


def _duration(minutes: float) -> str:
    return f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} h"


def _safe_state_path(cache_dir: Path, prefix: str, session_id: str) -> Path | None:
    """Return resolved path only if it stays within cache_dir; else None."""
    try:
        candidate = (cache_dir / f"{prefix}{session_id}.json").resolve()
        candidate.relative_to(cache_dir.resolve())
        return candidate
    except (ValueError, OSError, RuntimeError):
        return None


def _last_assistant_time(transcript_path: str) -> str:
    """ISO timestamp of the last assistant entry in the transcript tail; '' if none."""
    try:
        path = Path(str(transcript_path or "")).resolve()
        path.relative_to(_CLAUDE_DIR.resolve())
        with path.open("rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - _TRANSCRIPT_TAIL_BYTES))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except (ValueError, OSError, RuntimeError):
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") == "assistant" and entry.get("timestamp"):
            return entry["timestamp"]
    return ""


def main() -> None:
    try:
        _main()
    except Exception:
        # Never hard-block a UserPromptSubmit hook — always let the prompt through
        print(json.dumps({"continue": True}))


def _main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    session_id = hook_input.get("session_id", "")
    if not session_id:
        print(json.dumps({"continue": True}))
        return

    # Skip cache warning for system-generated messages — these are not human prompts
    # and blocking them breaks background task result delivery.
    _prompt = hook_input.get("prompt", "").lstrip()
    _SYSTEM_PREFIXES = (
        "<task-notification>",
        "<local-command-caveat>",
        "<command-name>",
        "<command-message>",
    )
    if any(_prompt.startswith(p) for p in _SYSTEM_PREFIXES):
        print(json.dumps({"continue": True}))
        return

    state_path = _safe_state_path(CACHE_WARN_DIR, "", session_id)
    if state_path is None:
        print(json.dumps({"continue": True}))
        return

    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except (json.JSONDecodeError, OSError):
            state = {}

    last_stop_iso = state.get("last_stop_time", "") or _last_assistant_time(
        hook_input.get("transcript_path", "")
    )
    warned_gaps: list = state.get("warned_gaps", [])

    if not last_stop_iso:
        # New session — no baseline to compare
        print(json.dumps({"continue": True}))
        return

    try:
        last_stop_dt = datetime.fromisoformat(last_stop_iso.replace("Z", "+00:00"))
    except ValueError:
        print(json.dumps({"continue": True}))
        return

    now = datetime.now(timezone.utc)
    gap_seconds = (now - last_stop_dt).total_seconds()

    if gap_seconds < WARN_THRESHOLD_SECONDS:
        print(json.dumps({"continue": True}))
        return

    bucket = _gap_bucket(last_stop_iso)
    if bucket in warned_gaps:
        # Already warned for this idle gap — let this prompt through
        print(json.dumps({"continue": True}))
        return

    # First warning for this gap — record and block
    warned_gaps.append(bucket)
    try:
        CACHE_WARN_DIR.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "session_id": session_id,
            "last_stop_time": last_stop_iso,
            "warned_gaps": warned_gaps,
        }))
    except OSError:
        pass

    expired_at = (last_stop_dt + timedelta(seconds=CACHE_TTL_SECONDS)).astimezone()
    overrun_min = (gap_seconds - CACHE_TTL_SECONDS) / 60
    if expired_at.date() == now.astimezone().date():
        when = expired_at.strftime("%H:%M")
    else:
        when = expired_at.strftime("%b %d %H:%M")
    warning = (
        f"Cache warning: prompt cache expired at {when} (~{_duration(overrun_min)} ago; "
        f"last reply {_duration(gap_seconds / 60)} ago). "
        f"This turn will re-process full context from scratch.\n"
        f"Re-send to proceed, or run /compact first to reduce cost."
    )

    print(json.dumps({
        "decision": "block",
        "reason": warning,
    }))


if __name__ == "__main__":
    main()
