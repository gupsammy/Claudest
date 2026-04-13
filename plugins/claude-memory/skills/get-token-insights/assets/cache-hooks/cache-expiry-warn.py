#!/usr/bin/env python3
"""
UserPromptSubmit hook: warn once when prompt cache has likely expired.

Claude Code uses the 5-minute prompt cache TTL (ephemeral_5m). When the user
has been idle >5 minutes since Claude last responded, the cached context
will expire on this turn — costing full re-creation tokens. This hook fires
before the prompt is sent and blocks once per idle gap so the user can
compact before the expensive re-creation happens.

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
from datetime import datetime, timezone
from pathlib import Path

CACHE_TTL_SECONDS = 300       # Claude Code uses 5-minute prompt cache TTL (ephemeral_5m, changed ~Apr 3 2026)
WARN_THRESHOLD_SECONDS = 300  # Warn at exactly 5 min (TTL expiry)
CACHE_WARN_DIR = Path.home() / ".claude-memory" / "cache-warn"


def _gap_bucket(last_stop_time_iso: str) -> str:
    """Stable ID for this idle gap — floor of last_stop_time in whole minutes."""
    dt = datetime.fromisoformat(last_stop_time_iso.replace("Z", "+00:00"))
    return str(math.floor(dt.timestamp() / 60))


def _format_tokens(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def _safe_state_path(cache_dir: Path, prefix: str, session_id: str) -> Path | None:
    """Return resolved path only if it stays within cache_dir; else None."""
    try:
        candidate = (cache_dir / f"{prefix}{session_id}.json").resolve()
        candidate.relative_to(cache_dir.resolve())
        return candidate
    except (ValueError, OSError, RuntimeError):
        return None


def check_resume_warn(session_id: str) -> str | None:
    """If a resume-pending flag exists for this session, consume it and return a warning."""
    flag_path = _safe_state_path(CACHE_WARN_DIR, "resume-pending-", session_id)
    if flag_path is None or not flag_path.exists():
        return None

    try:
        flag = json.loads(flag_path.read_text())
        cached_tokens = flag.get("cached_tokens", 0)
    except (json.JSONDecodeError, OSError):
        cached_tokens = 0
    finally:
        try:
            flag_path.unlink()
        except OSError:
            pass

    token_str = f"~{_format_tokens(cached_tokens)} cached tokens" if cached_tokens else "cached context"
    return (
        f"Resumed session: this turn will re-process {token_str} from scratch "
        f"(5-minute cache TTL likely expired while session was inactive).\n"
        f"Run /compact to reduce re-creation cost, then re-send — or re-send now to proceed."
    )


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

    # Check resume warning first — takes priority over idle-gap warning
    resume_warning = check_resume_warn(session_id)
    if resume_warning:
        # Also mark the current gap as warned so the idle-gap check doesn't
        # double-fire on the re-send after this block.
        state_path = _safe_state_path(CACHE_WARN_DIR, "", session_id)
        if state_path is not None and state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                last_stop_iso = state.get("last_stop_time", "")
                if last_stop_iso:
                    warned_gaps = state.get("warned_gaps", [])
                    bucket = _gap_bucket(last_stop_iso)
                    if bucket not in warned_gaps:
                        warned_gaps.append(bucket)
                        state_path.write_text(json.dumps({
                            "session_id": session_id,
                            "last_stop_time": last_stop_iso,
                            "warned_gaps": warned_gaps,
                        }))
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        print(json.dumps({"decision": "block", "reason": resume_warning}))
        return

    state_path = _safe_state_path(CACHE_WARN_DIR, "", session_id)
    if state_path is None or not state_path.exists():
        # Stop hook hasn't fired yet this session — no baseline to compare
        print(json.dumps({"continue": True}))
        return

    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        print(json.dumps({"continue": True}))
        return

    last_stop_iso = state.get("last_stop_time", "")
    warned_gaps: list = state.get("warned_gaps", [])

    if not last_stop_iso:
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
        state_path.write_text(json.dumps({
            "session_id": session_id,
            "last_stop_time": last_stop_iso,
            "warned_gaps": warned_gaps,
        }))
    except OSError:
        pass

    idle_minutes = gap_seconds / 60

    overrun = gap_seconds - CACHE_TTL_SECONDS
    status = f"expired ~{overrun / 60:.0f} min ago" if overrun >= 60 else "just expired"

    warning = (
        f"Cache warning: prompt cache {status} (idle: {idle_minutes:.1f} min). "
        f"This turn will re-process full context from scratch.\n"
        f"Re-send to proceed, or run /compact first to reduce cost."
    )

    print(json.dumps({
        "decision": "block",
        "reason": warning,
    }))


if __name__ == "__main__":
    main()
