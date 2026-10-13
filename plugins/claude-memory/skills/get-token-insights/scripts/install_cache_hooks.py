#!/usr/bin/env python3
"""
Cache-bust hook installer for get-token-insights.

Copies 2 validated hook scripts into ~/.claude/hooks/ and merges the
corresponding hook matcher blocks into ~/.claude/settings.json. Retired hooks
from earlier versions (RETIRED_HOOKS) are removed from both.

Hooks installed:
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
    "cache-warn-stop.py",
    "cache-expiry-warn.py",
]

# Exact matcher blocks to inject — keyed by (event, command_substring) for
# idempotency check. The installer skips injection if any existing block's
# command already contains the substring.
HOOK_BLOCKS = {
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


# Shipped by earlier versions; cache-expiry-warn.py now covers resumed sessions
# by reading the transcript when no Stop state exists.
RETIRED_HOOKS = ["cache-resume-detect.py"]


def _hook_mentions(hook: object, substr: str) -> bool:
    """True if a hook entry's command contains substr.

    settings.json is user-edited, so tolerate non-dict entries and null commands.
    """
    return isinstance(hook, dict) and substr in str(hook.get("command") or "")


def _block_mentions(block: object, substr: str) -> bool:
    """True if a matcher block has a hook command containing substr."""
    return isinstance(block, dict) and any(
        _hook_mentions(h, substr) for h in block.get("hooks") or []
    )


def _without_hook(event_list: list, substr: str) -> list:
    """event_list minus hook entries mentioning substr; blocks left empty are dropped.

    Filters per hook, not per block, so a user's own hook sharing a block survives.
    """
    kept = []
    for b in event_list:
        if not _block_mentions(b, substr):
            kept.append(b)
            continue
        hooks = [h for h in b["hooks"] if not _hook_mentions(h, substr)]
        if hooks:
            kept.append({**b, "hooks": hooks})
    return kept


def _already_in_hooks(event_list: list, substr: str) -> bool:
    """Return True if any existing hook command contains substr."""
    return any(_block_mentions(b, substr) for b in event_list)


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


class InstallError(Exception):
    """Raised by install() on hard failures; caught by main() which calls sys.exit(1)."""


def install(dry_run: bool = False) -> bool:
    """
    Install hook scripts and settings.json entries.
    Returns True if any change was made (or would be made in dry-run).
    Raises InstallError on hard failures (missing assets, bad settings.json).
    """
    changed = False

    # 1. Copy hook scripts (skip if identical)
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    for fname in HOOK_FILES:
        src = ASSET_DIR / fname
        dest = HOOKS_DIR / fname
        if not src.exists():
            raise InstallError(f"Asset not found: {src}")
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
        raise InstallError("~/.claude/settings.json not found — cannot wire hooks")

    try:
        cfg = json.loads(SETTINGS_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise InstallError(f"settings.json is malformed: {exc}") from exc

    hooks_cfg = cfg.setdefault("hooks", {})
    settings_changed = False

    retired_files = []
    for fname in RETIRED_HOOKS:
        dest = HOOKS_DIR / fname
        if dest.exists():
            print(f"  [remove] {fname} — retired")
            retired_files.append(dest)
            changed = True
        for event, event_list in list(hooks_cfg.items()):
            if not isinstance(event_list, list):
                continue
            kept = _without_hook(event_list, fname)
            if kept != event_list:
                print(f"  [remove] settings.json {event} hook → {fname}")
                if not dry_run:
                    if kept:
                        hooks_cfg[event] = kept
                    else:
                        del hooks_cfg[event]
                settings_changed = True
                changed = True

    for event, spec in HOOK_BLOCKS.items():
        event_list = hooks_cfg.setdefault(event, [])
        if not isinstance(event_list, list):
            raise InstallError(f"settings.json hooks.{event} is not a list")
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

    # Delete retired scripts only after settings.json no longer points at them,
    # so a failed write never leaves a registration to a missing file.
    if not dry_run:
        for dest in retired_files:
            dest.unlink()

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

    try:
        changed = install(dry_run=args.dry_run)
    except InstallError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    if not changed:
        print("\nAll hooks already installed — nothing to do.")
    elif args.dry_run:
        print("\n[dry-run complete] Re-run without --dry-run to apply.")
    else:
        print("\nInstall complete. Restart Claude Code for hooks to take effect.")
        print("What changes when you idle past the 1-hour cache TTL:")
        print("  1. Your next prompt will be blocked once with a cost warning.")
        print("  2. Press ↑ to resend as-is (accepts the cache rebuild cost).")
        print("  3. Or run /compact to compress context, then resend.")
        print("  4. Or run /clear to start a fresh session with no rebuild cost.")
        print("One warning per idle gap — not per prompt. No repeat nags.")


if __name__ == "__main__":
    main()
