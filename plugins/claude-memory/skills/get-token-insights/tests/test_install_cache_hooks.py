#!/usr/bin/env python3
"""install_cache_hooks must remove hooks retired by earlier versions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import install_cache_hooks as ich  # noqa: E402

_OLD_CMD = "python3 ~/.claude/hooks/cache-resume-detect.py"


@pytest.fixture
def home(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(ich, "HOOKS_DIR", hooks)
    monkeypatch.setattr(ich, "SETTINGS_PATH", settings)
    (hooks / "cache-resume-detect.py").write_text("# old\n")
    settings.write_text(json.dumps({"hooks": {
        "SessionStart": [
            {"matcher": "*", "hooks": [{"type": "command", "command": _OLD_CMD}]},
            {"matcher": "*", "hooks": [{"type": "command", "command": "python3 other.py"}]},
        ],
    }}))
    return hooks, settings


def test_install_removes_retired_hook_file_and_registration(home):
    hooks, settings = home
    ich.install()
    cfg = json.loads(settings.read_text())
    assert not (hooks / "cache-resume-detect.py").exists()
    assert _OLD_CMD not in settings.read_text()
    # unrelated hooks on the same event survive
    assert cfg["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "python3 other.py"
    # current hooks are installed
    assert (hooks / "cache-expiry-warn.py").exists()
    assert "cache-expiry-warn.py" in json.dumps(cfg["hooks"]["UserPromptSubmit"])


def test_dry_run_leaves_retired_hook_in_place(home):
    hooks, settings = home
    before = settings.read_text()
    assert ich.install(dry_run=True) is True
    assert (hooks / "cache-resume-detect.py").exists()
    assert settings.read_text() == before


def test_second_install_is_a_no_op(home):
    ich.install()
    assert ich.install() is False


def test_install_tolerates_hand_edited_settings(home):
    hooks, settings = home
    cfg = json.loads(settings.read_text())
    cfg["hooks"]["SessionStart"] += [
        "not-a-dict",
        {"matcher": "*", "hooks": [{"type": "command", "command": None}, "junk"]},
        {"matcher": "*"},
    ]
    cfg["hooks"]["Notification"] = {"unexpected": "shape"}
    settings.write_text(json.dumps(cfg))
    ich.install()
    out = json.loads(settings.read_text())
    assert _OLD_CMD not in settings.read_text()
    assert out["hooks"]["Notification"] == {"unexpected": "shape"}
    assert "not-a-dict" in out["hooks"]["SessionStart"]


def test_shared_block_keeps_users_own_hook(home):
    hooks, settings = home
    cfg = json.loads(settings.read_text())
    cfg["hooks"]["SessionStart"] = [{"matcher": "*", "hooks": [
        {"type": "command", "command": _OLD_CMD},
        {"type": "command", "command": "python3 mine.py"},
    ]}]
    settings.write_text(json.dumps(cfg))
    ich.install()
    out = json.loads(settings.read_text())
    assert out["hooks"]["SessionStart"] == [
        {"matcher": "*", "hooks": [{"type": "command", "command": "python3 mine.py"}]}
    ]


def test_failed_settings_write_keeps_retired_script(home, monkeypatch):
    hooks, settings = home
    real_write = Path.write_text

    def failing_write(self, *a, **kw):
        if self == settings:
            raise OSError("disk full")
        return real_write(self, *a, **kw)

    monkeypatch.setattr(Path, "write_text", failing_write)
    with pytest.raises(OSError):
        ich.install()
    assert (hooks / "cache-resume-detect.py").exists()


def test_non_list_owned_event_raises_install_error(home):
    hooks, settings = home
    cfg = json.loads(settings.read_text())
    cfg["hooks"]["Stop"] = {"oops": True}
    settings.write_text(json.dumps(cfg))
    with pytest.raises(ich.InstallError):
        ich.install()
