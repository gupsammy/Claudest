"""Tests for plugins/claude-memory/hooks/write_config.py.

write_config.py inserts memory_lib into sys.path at module level, so import
works correctly once the hooks directory is on sys.path. The pyproject.toml
pythonpath already covers memory_lib; we import write_config via importlib to
avoid polluting the test module's namespace with its sys.path mutation.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import write_config as a module (it does sys.path.insert at import time).
# We do this once at collection time; subsequent tests share the same module.
# ---------------------------------------------------------------------------
_HOOKS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "plugins" / "claude-memory" / "hooks"
)
_WRITE_CONFIG_PATH = _HOOKS_DIR / "write_config.py"

spec = importlib.util.spec_from_file_location("write_config", _WRITE_CONFIG_PATH)
write_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(write_config)

from memory_lib.db import CURRENT_ONBOARDING_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_config_path(monkeypatch, path: Path) -> None:
    """Patch CONFIG_PATH in both memory_lib.db and the imported write_config module."""
    import memory_lib.db as db_mod
    monkeypatch.setattr(db_mod, "CONFIG_PATH", path)
    monkeypatch.setattr(write_config, "CONFIG_PATH", path)


def _run_main(args=None):
    """Call write_config.main() with sys.argv set to args."""
    original_argv = sys.argv[:]
    sys.argv = ["write_config.py"] + (args or [])
    try:
        write_config.main()
    finally:
        sys.argv = original_argv


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWriteConfigDefaults:
    def test_defaults_flag_writes_onboarding_completed(self, tmp_path, monkeypatch):
        """--defaults must mark onboarding complete — this is the primary post-onboarding action."""
        cfg = tmp_path / "config.json"
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--defaults"])

        result = json.loads(cfg.read_text())
        assert result["onboarding_completed"] is True

    def test_defaults_flag_writes_current_onboarding_version(self, tmp_path, monkeypatch):
        """--defaults must write the current version so the onboarding hook becomes a no-op."""
        cfg = tmp_path / "config.json"
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--defaults"])

        result = json.loads(cfg.read_text())
        assert result["onboarding_version"] == CURRENT_ONBOARDING_VERSION

    def test_defaults_produces_valid_dict(self, tmp_path, monkeypatch):
        """--defaults output must be a JSON object, not an array or primitive."""
        cfg = tmp_path / "config.json"
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--defaults"])

        result = json.loads(cfg.read_text())
        assert isinstance(result, dict)

    def test_defaults_includes_standard_keys(self, tmp_path, monkeypatch):
        """--defaults must write all expected config keys."""
        cfg = tmp_path / "config.json"
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--defaults"])

        result = json.loads(cfg.read_text())
        for key in ("auto_inject_context", "consolidation_reminder_enabled",
                    "consolidation_min_hours", "consolidation_min_sessions"):
            assert key in result, f"Expected key '{key}' in config output"

    def test_defaults_resets_auto_inject_context_when_existing_config_has_it_false(
        self, tmp_path, monkeypatch
    ):
        """--defaults must ignore existing config and restore auto_inject_context to True.

        Without the fix, a user who previously set auto_inject_context=false then
        re-ran onboarding with --defaults would keep false — defeating the intent
        of --defaults being a clean reset to recommended values.
        """
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"auto_inject_context": False, "onboarding_completed": True}))
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--defaults"])

        result = json.loads(cfg.read_text())
        assert result["auto_inject_context"] is True


class TestWriteConfigNonDictExistingConfig:
    def test_non_dict_existing_config_is_discarded(self, tmp_path, monkeypatch):
        """When config.json contains a JSON array, it is silently discarded and defaults apply.

        Prevents a crash in the update path (dict.update() on a list) when a previous
        write was interrupted or the file was manually corrupted.
        """
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps([1, 2, 3]))
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--defaults"])

        result = json.loads(cfg.read_text())
        assert isinstance(result, dict)
        assert result["onboarding_completed"] is True

    def test_no_crash_when_existing_config_is_array(self, tmp_path, monkeypatch):
        """Running write_config on a corrupted config file must not raise an exception."""
        cfg = tmp_path / "config.json"
        cfg.write_text("[]")
        _patch_config_path(monkeypatch, cfg)

        # Should not raise
        _run_main(["--defaults"])


class TestWriteConfigAtomicWrite:
    def test_no_tmp_file_left_on_write_failure(self, tmp_path, monkeypatch):
        """If the atomic write fails, no .tmp file should remain — prevents stale temp artifacts."""
        import os
        cfg = tmp_path / "config.json"
        _patch_config_path(monkeypatch, cfg)

        # Patch os.fdopen to raise after the tempfile is created
        def exploding_fdopen(fd, _mode):
            # Close the fd to avoid leaking it, then raise
            os.close(fd)
            raise OSError("simulated write failure")

        monkeypatch.setattr(os, "fdopen", exploding_fdopen)

        with pytest.raises(OSError):
            _run_main(["--defaults"])

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover .tmp files found: {tmp_files}"

    def test_original_config_unchanged_on_write_failure(self, tmp_path, monkeypatch):
        """If atomic write fails, the original config.json must not be modified."""
        import os
        original = {"onboarding_completed": False, "sentinel": "original-value"}
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps(original))
        _patch_config_path(monkeypatch, cfg)

        def exploding_fdopen(fd, _mode):
            os.close(fd)
            raise OSError("simulated write failure")

        monkeypatch.setattr(os, "fdopen", exploding_fdopen)

        with pytest.raises(OSError):
            _run_main(["--defaults"])

        surviving = json.loads(cfg.read_text())
        assert surviving["sentinel"] == "original-value"


class TestWriteConfigParentDirCreation:
    def test_parent_dir_auto_created(self, tmp_path, monkeypatch):
        """write_config must create the parent directory if it doesn't exist.

        Ensures first-run installs (no ~/.claude-memory/ yet) complete successfully.
        """
        cfg = tmp_path / "subdir" / "nested" / "config.json"
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--defaults"])

        assert cfg.exists(), "Config file should be written even when parent dirs are missing"
        result = json.loads(cfg.read_text())
        assert result["onboarding_completed"] is True


class TestWriteConfigCliArgs:
    def test_auto_inject_context_false(self, tmp_path, monkeypatch):
        """--auto-inject-context=false must persist as False in config."""
        cfg = tmp_path / "config.json"
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--auto-inject-context", "false"])

        result = json.loads(cfg.read_text())
        assert result["auto_inject_context"] is False

    def test_consolidation_min_hours_floor(self, tmp_path, monkeypatch):
        """--consolidation-min-hours=0 is clamped to 1 (minimum valid interval)."""
        cfg = tmp_path / "config.json"
        _patch_config_path(monkeypatch, cfg)

        _run_main(["--consolidation-min-hours", "0"])

        result = json.loads(cfg.read_text())
        assert result["consolidation_min_hours"] == 1
