#!/usr/bin/env python3
"""Stop hook - background sync for current session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def main():
    is_codex = False
    try:
        # Read hook input from stdin
        hook_input = sys.stdin.read()
        codex_thread_id = os.environ.get("CODEX_THREAD_ID")
        try:
            parsed_input = json.loads(hook_input) if hook_input else {}
        except json.JSONDecodeError:
            parsed_input = {}
        transcript_path = str(parsed_input.get("transcript_path") or "")
        session_id = parsed_input.get("session_id")
        is_codex = bool(
            (codex_thread_id and (not session_id or session_id == codex_thread_id))
            or "/.codex/sessions/" in transcript_path
        )

        # Write to temp file (cross-platform stdin piping to detached process is unreliable)
        # Use os.fdopen on the fd directly to avoid TOCTOU race; mkstemp already sets 0o600
        fd, tmp_path = tempfile.mkstemp(prefix="claude-memory-sync-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(hook_input)
        except Exception:
            # fd is closed by os.fdopen even on error; clean up the file
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # Background the sync
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / "sync_current.py"), "--input-file", tmp_path],
            **kwargs
        )
    except Exception:
        pass

    print(json.dumps({} if is_codex else {"continue": True}))


if __name__ == "__main__":
    main()
