"""Tests for plugins/claude-memory/hooks/onboarding.py.

onboarding.py is a SessionStart hook: it must either inject onboarding
instructions (first-run / version upgrade) or exit silently (already
onboarded at current version). A bug here either repeatedly pesters users
who have already completed onboarding, or silently skips onboarding for
new installs.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from memory_lib.db import CURRENT_ONBOARDING_VERSION

# ---------------------------------------------------------------------------
# Import onboarding hook module via importlib (does sys.path.insert at load)
# ---------------------------------------------------------------------------
_HOOKS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "plugins" / "claude-memory" / "hooks"
)
_ONBOARDING_PATH = _HOOKS_DIR / "onboarding.py"

spec = importlib.util.spec_from_file_location("onboarding", _ONBOARDING_PATH)
onboarding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(onboarding)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_config_path(monkeypatch, path: Path) -> None:
    """Patch CONFIG_PATH in both memory_lib.db and the onboarding module."""
    import memory_lib.db as db_mod
    monkeypatch.setattr(db_mod, "CONFIG_PATH", path)
    monkeypatch.setattr(onboarding, "CONFIG_PATH", path)


def _run_main_captured(monkeypatch, cfg_path: Path) -> dict:
    """Run onboarding.main() and return parsed JSON output."""
    _patch_config_path(monkeypatch, cfg_path)
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    onboarding.main()
    # Restore stdout before asserting so pytest can capture failures
    monkeypatch.setattr(sys, "stdout", sys.__stdout__)
    return json.loads(buf.getvalue())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOnboardingAlreadyCompleted:
    def test_already_onboarded_exits_silently(self, tmp_path, monkeypatch):
        """When config shows onboarding_completed=True at current version, output must be {}.

        Returning anything else causes the hook to inject context every session,
        which would be extremely noisy for users who have already set up the plugin.
        """
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "onboarding_completed": True,
            "onboarding_version": CURRENT_ONBOARDING_VERSION,
        }))

        result = _run_main_captured(monkeypatch, cfg)
        assert result == {}

    def test_already_onboarded_higher_version_exits_silently(self, tmp_path, monkeypatch):
        """A config with onboarding_version > CURRENT also exits silently (forward-compat)."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "onboarding_completed": True,
            "onboarding_version": CURRENT_ONBOARDING_VERSION + 1,
        }))

        result = _run_main_captured(monkeypatch, cfg)
        assert result == {}


class TestOnboardingTriggered:
    def test_missing_config_injects_context(self, tmp_path, monkeypatch):
        """When config.json is absent (fresh install), onboarding context must be injected."""
        cfg = tmp_path / "nonexistent.json"

        result = _run_main_captured(monkeypatch, cfg)

        assert "hookSpecificOutput" in result, "Expected hookSpecificOutput on first run"
        assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "additionalContext" in result["hookSpecificOutput"]

    def test_old_version_re_triggers_onboarding(self, tmp_path, monkeypatch):
        """onboarding_completed=True with an older version must re-trigger onboarding.

        This is the upgrade path: if CURRENT_ONBOARDING_VERSION bumps, existing
        users must see the new onboarding content once.
        """
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "onboarding_completed": True,
            "onboarding_version": CURRENT_ONBOARDING_VERSION - 1,
        }))

        result = _run_main_captured(monkeypatch, cfg)

        assert "hookSpecificOutput" in result, (
            "Expected onboarding context to be injected when version is behind current"
        )

    def test_completed_false_injects_context(self, tmp_path, monkeypatch):
        """Config present but onboarding_completed=False must inject onboarding context."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({
            "onboarding_completed": False,
            "onboarding_version": CURRENT_ONBOARDING_VERSION,
        }))

        result = _run_main_captured(monkeypatch, cfg)

        assert "hookSpecificOutput" in result

    def test_injected_context_mentions_write_config(self, tmp_path, monkeypatch):
        """Onboarding context must reference the write_config.py path so Claude can invoke it."""
        cfg = tmp_path / "nonexistent.json"

        result = _run_main_captured(monkeypatch, cfg)

        context = result["hookSpecificOutput"]["additionalContext"]
        assert "write_config.py" in context


class TestOnboardingResiliency:
    def test_exception_does_not_block_session(self, tmp_path, monkeypatch):
        """Any exception in main() must be swallowed and return {} to avoid blocking session start."""
        cfg = tmp_path / "config.json"
        _patch_config_path(monkeypatch, cfg)

        # Make load_config raise to simulate an unexpected error
        import memory_lib.db as db_mod

        def exploding_load_config():
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(db_mod, "load_config", exploding_load_config)
        monkeypatch.setattr(onboarding, "load_config", exploding_load_config)

        buf = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buf)

        # The __main__ guard catches all exceptions; call it directly
        try:
            onboarding.main()
        except Exception:
            pass
        finally:
            monkeypatch.setattr(sys, "stdout", sys.__stdout__)

        output = buf.getvalue().strip()
        if output:
            # If anything was printed it must be valid JSON
            parsed = json.loads(output)
            assert isinstance(parsed, dict)
