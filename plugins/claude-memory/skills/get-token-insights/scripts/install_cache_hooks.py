#!/usr/bin/env python3
"""
Cache-bust hook installer for get-token-insights.

Copies 3 validated hook scripts into ~/.claude/hooks/ and merges the
corresponding hook matcher blocks into ~/.claude/settings.json.

Hooks installed:
  SessionStart  → cache-resume-detect.py  (flags resumed sessions for warn)
  Stop          → cache-warn-stop.py      (stamps last-idle timestamp)
  UserPromptSubmit → cache-expiry-warn.py (blocks prompt when cache expired)

Usage:
  python3 install_cache_hooks.py [--dry-run]

Flags:
  --dry-run   Print what would change without writing anything.
  --status    Show which hooks are installed, skip install.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ASSET_DIR = Path(__file__).parent.parent / "assets" / "cache-hooks"
HOOKS_DIR = Path.home() / ".claude" / "hooks"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

HOOK_FILES = [
    "cache-resume-detect.py",
    "cache-warn-stop.py",
    "cache-expiry-warn.py",
]

# Exact matcher blocks to inject — keyed by (event, command_substring) for
# idempotency check. The installer skips injection if any existing block's
# command already contains the substring.
HOOK_BLOCKS = {
    "SessionStart": {
        "check_substr": "cache-resume-detect.py",
        "block": {
            "matcher": "*",
            "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/cache-resume-detect.py"}],
        },
    },
    "Stop": {
        "check_substr": "cache-warn-stop.py",
        "block": {
            "matcher": "*",
            "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/cache-warn-stop.py"}],
        },
    },
    "UserPromptSubmit": {
        "check_substr": "cache-expiry-warn.py",
        "block": {
            "matcher": "*",
            "hooks": [{"type": "command", "command": "python3 ~/.claude/hooks/cache-expiry-warn.py"}],
        },
    },
}


def _already_in_hooks(event_list: list[dict], substr: str) -> bool:
    """Return True if any existing hook command contains substr."""
    for matcher_block in event_list:
        for hook in matcher_block.get("hooks", []):
            if substr in hook.get("command", ""):
                return True
    return False


def check_status() -> dict[str, dict]:
    """Return install status for each component."""
    result: dict[str, dict] = {}
    for fname in HOOK_FILES:
        dest = HOOKS_DIR / fname
        result[fname] = {"file_exists": dest.exists()}
    if SETTINGS_PATH.exists():
        try:
            cfg = json.loads(SETTINGS_PATH.read_text())
        except json.JSONDecodeError:
            cfg = {}
        hooks_cfg = cfg.get("hooks", {})
        for event, spec in HOOK_BLOCKS.items():
            wired = _already_in_hooks(hooks_cfg.get(event, []), spec["check_substr"])
            result[f"settings:{event}"] = {"wired": wired}
    return result


def install(dry_run: bool = False) -> bool:
    """
    Install hook scripts and settings.json entries.
    Returns True if any change was made (or would be made in dry-run).
    """
    changed = False

    # 1. Copy hook scripts (skip if identical)
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    for fname in HOOK_FILES:
        src = ASSET_DIR / fname
        dest = HOOKS_DIR / fname
        if not src.exists():
            print(f"[ERROR] Asset not found: {src}", file=sys.stderr)
            return False
        if dest.exists():
            if dest.read_bytes() == src.read_bytes():
                print(f"  [skip] {fname} — already up to date")
                continue
            else:
                print(f"  [update] {fname} — content differs, will overwrite")
        else:
            print(f"  [install] {fname}")
        if not dry_run:
            shutil.copy2(src, dest)
            dest.chmod(0o644)
        changed = True

    # 2. Merge settings.json
    if not SETTINGS_PATH.exists():
        print("[ERROR] ~/.claude/settings.json not found — cannot wire hooks", file=sys.stderr)
        return False

    try:
        cfg = json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError as exc:
        print(f"[ERROR] settings.json is malformed: {exc}", file=sys.stderr)
        return False

    hooks_cfg = cfg.setdefault("hooks", {})
    settings_changed = False

    for event, spec in HOOK_BLOCKS.items():
        event_list = hooks_cfg.setdefault(event, [])
        if _already_in_hooks(event_list, spec["check_substr"]):
            print(f"  [skip] settings.json {event} hook — already wired")
            continue
        print(f"  [add] settings.json {event} hook → {spec['check_substr']}")
        if not dry_run:
            event_list.append(spec["block"])
        settings_changed = True
        changed = True

    if settings_changed and not dry_run:
        # Backup before write
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = SETTINGS_PATH.with_suffix(f".json.bak-{ts}")
        shutil.copy2(SETTINGS_PATH, backup)
        print(f"  [backup] settings.json → {backup.name}")
        SETTINGS_PATH.write_text(json.dumps(cfg, indent=2))
        print("  [wrote] ~/.claude/settings.json")

    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Claude cache-bust warning hooks")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    parser.add_argument("--status", action="store_true", help="Show current install status and exit")
    args = parser.parse_args()

    if args.status:
        status = check_status()
        print("Cache-bust hook install status:")
        for key, val in status.items():
            flags = ", ".join(f"{k}={v}" for k, v in val.items())
            print(f"  {key}: {flags}")
        return

    if args.dry_run:
        print("[dry-run] No files will be written.\n")

    changed = install(dry_run=args.dry_run)

    if not changed:
        print("\nAll hooks already installed — nothing to do.")
    elif args.dry_run:
        print("\n[dry-run complete] Re-run without --dry-run to apply.")
    else:
        print("\nInstall complete. Restart Claude Code for hooks to take effect.")
        print("What changes when you idle >5 minutes:")
        print("  1. Your next prompt will be blocked once with a cost warning.")
        print("  2. Press ↑ to resend as-is (accepts the cache rebuild cost).")
        print("  3. Or run /compact to compress context, then resend.")
        print("  4. Or run /clear to start a fresh session with no rebuild cost.")
        print("One warning per idle gap — not per prompt. No repeat nags.")


if __name__ == "__main__":
    main()
