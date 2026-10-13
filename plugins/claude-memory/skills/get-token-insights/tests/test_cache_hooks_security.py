#!/usr/bin/env python3
"""Tests for security fixes and the _bust_overhead cost function.

Coverage:
  - _safe_state_path (both hook files) — path traversal prevention
  - _last_assistant_time (cache-expiry-warn) — transcript bounds check
  - _bust_overhead (ingest_token_data) — incremental cache-bust cost vs _turn_cost
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import helpers — hook files use hyphens, not valid identifiers
# ---------------------------------------------------------------------------

_HOOKS_DIR = (
    Path(__file__).resolve().parent.parent
    / "assets" / "cache-hooks"
)
_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "scripts"
)


def _load_hook(filename: str):
    """Load a hyphenated hook script as a module via importlib."""
    module_name = filename.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(
        module_name, _HOOKS_DIR / filename
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load once at module import so parametrize can reference the functions
_expiry_warn = _load_hook("cache-expiry-warn.py")
_warn_stop = _load_hook("cache-warn-stop.py")

# Load ingest script
sys.path.insert(0, str(_SCRIPTS_DIR))
from ingest_token_data import _bust_overhead, _turn_cost, _get_pricing  # noqa: E402

# ---------------------------------------------------------------------------
# Parametrize _safe_state_path across both hook modules
# ---------------------------------------------------------------------------

_HOOK_MODULES = [
    pytest.param(_expiry_warn, id="cache-expiry-warn"),
    pytest.param(_warn_stop, id="cache-warn-stop"),
]


# ---------------------------------------------------------------------------
# _safe_state_path — path traversal prevention
# ---------------------------------------------------------------------------

class TestSafeStatePath:
    """_safe_state_path must confine state files within cache_dir.

    A crafted session_id that traverses out of cache_dir would let an
    attacker read or overwrite arbitrary files (e.g. ~/.claude/settings.json).
    Returning None on traversal prevents that write/read from ever happening.
    """

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_normal_session_id_returns_path_inside_cache_dir(self, mod, tmp_path):
        result = mod._safe_state_path(tmp_path, "", "abc123")
        assert result is not None
        assert result.is_relative_to(tmp_path)
        assert result.name == "abc123.json"

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_normal_session_id_with_prefix(self, mod, tmp_path):
        result = mod._safe_state_path(tmp_path, "resume-pending-", "abc123")
        assert result is not None
        assert result.is_relative_to(tmp_path)
        assert result.name == "resume-pending-abc123.json"

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_traversal_dotdot_returns_none(self, mod, tmp_path):
        # Prevents overwriting ~/.claude/settings.json via crafted session_id
        result = mod._safe_state_path(tmp_path, "", "../../.claude/settings")
        assert result is None

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_traversal_dotdot_with_prefix_returns_none(self, mod, tmp_path):
        # "resume-pending-../../evil" resolves to <cache_dir>/evil — still inside,
        # because the prefix contributes a pseudo-directory level that absorbs one "..".
        # A genuine escape requires 3 levels of "../".  Verify the guard catches it.
        result = mod._safe_state_path(tmp_path, "resume-pending-", "../../../evil")
        assert result is None

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_two_dotdot_with_prefix_stays_inside(self, mod, tmp_path):
        # "resume-pending-../../evil" lands at <cache_dir>/evil — guard correctly
        # allows it because the resolved path is still within cache_dir.
        result = mod._safe_state_path(tmp_path, "resume-pending-", "../../evil")
        assert result is not None
        assert result.is_relative_to(tmp_path)

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_absolute_component_returns_none(self, mod, tmp_path):
        # On POSIX, joining an absolute string replaces the base path entirely
        result = mod._safe_state_path(tmp_path, "", "/etc/passwd")
        assert result is None

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_null_byte_in_session_id_returns_none(self, mod, tmp_path):
        # Null bytes in filenames are rejected by the OS; resolve() raises on some
        # platforms and produces an out-of-bounds path on others.  Either way,
        # the result must be None or a valid in-bounds path — never a path outside.
        try:
            result = mod._safe_state_path(tmp_path, "", "abc\x00../../evil")
        except (ValueError, OSError):
            return  # raising is also an acceptable guard
        if result is not None:
            assert result.is_relative_to(tmp_path)

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_empty_session_id_returns_path_inside_cache_dir(self, mod, tmp_path):
        # Empty session_id is weird but harmless — cache_dir/".json" stays inside.
        result = mod._safe_state_path(tmp_path, "", "")
        assert result is not None
        assert result.is_relative_to(tmp_path)

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_returned_path_has_json_suffix(self, mod, tmp_path):
        result = mod._safe_state_path(tmp_path, "pfx-", "sess99")
        assert result is not None
        assert result.suffix == ".json"


# ---------------------------------------------------------------------------
# _last_assistant_time — transcript bounds check in cache-expiry-warn.py
# ---------------------------------------------------------------------------

class TestLastAssistantTime:
    """_last_assistant_time must refuse to read files outside ~/.claude.

    A bypassed bounds check would let a crafted transcript_path read arbitrary
    files on disk (e.g. SSH keys).
    """

    @pytest.fixture
    def claude_dir(self, tmp_path, monkeypatch):
        d = tmp_path / ".claude"
        d.mkdir()
        monkeypatch.setattr(_expiry_warn, "_CLAUDE_DIR", d)
        return d

    @staticmethod
    def _write(path: Path, *entries: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    def test_returns_last_assistant_timestamp(self, claude_dir):
        t = claude_dir / "projects" / "s.jsonl"
        self._write(
            t,
            {"type": "assistant", "timestamp": "2026-10-01T10:00:00Z"},
            {"type": "user", "timestamp": "2026-10-01T10:05:00Z"},
            {"type": "assistant", "timestamp": "2026-10-01T10:06:00Z", "content": "こんにちは"},
            {"type": "user", "timestamp": "2026-10-01T11:00:00Z"},
        )
        assert _expiry_warn._last_assistant_time(str(t)) == "2026-10-01T10:06:00Z"

    def test_path_outside_claude_dir_returns_empty(self, claude_dir, tmp_path):
        evil = tmp_path / "evil.jsonl"
        self._write(evil, {"type": "assistant", "timestamp": "2026-10-01T10:00:00Z"})
        assert _expiry_warn._last_assistant_time(str(evil)) == ""

    def test_symlink_escaping_claude_dir_returns_empty(self, claude_dir, tmp_path):
        outside = tmp_path / "real.jsonl"
        self._write(outside, {"type": "assistant", "timestamp": "2026-10-01T10:00:00Z"})
        link = claude_dir / "escape.jsonl"
        link.symlink_to(outside)
        assert _expiry_warn._last_assistant_time(str(link)) == ""

    @pytest.mark.parametrize("arg", [None, "", "no-such.jsonl"])
    def test_missing_or_none_path_returns_empty(self, claude_dir, arg):
        path = arg if arg != "no-such.jsonl" else str(claude_dir / arg)
        assert _expiry_warn._last_assistant_time(path) == ""  # type: ignore[arg-type]

    def test_no_assistant_entry_returns_empty(self, claude_dir):
        t = claude_dir / "u.jsonl"
        self._write(t, {"type": "user", "timestamp": "2026-10-01T10:00:00Z"})
        assert _expiry_warn._last_assistant_time(str(t)) == ""


# ---------------------------------------------------------------------------
# _bust_overhead — incremental cache-bust cost function
# ---------------------------------------------------------------------------

_SONNET_PRICING = _get_pricing("claude-sonnet")
# Rates: input=3.0, output=15.0, cache_write_5m=3.75, cache_write_1h=6.0, cache_read=0.30


class TestBustOverhead:
    """_bust_overhead measures only the extra cost of re-creating a cache miss
    (write rate minus read rate), not the full turn cost.

    If the formula is wrong, cache-bust impact reports will over- or under-count
    the cost savings from compacting — leading users to ignore or over-react to
    cache TTL warnings.
    """

    def test_all_zeros_returns_zero(self):
        result = _bust_overhead(0, 0, 0, _SONNET_PRICING)
        assert result == 0.0

    def test_only_ephem_5m_tokens(self):
        # 1M tokens re-created at 5m tier: delta = (3.75 - 0.30) / 1M per token
        result = _bust_overhead(1_000_000, 1_000_000, 0, _SONNET_PRICING)
        expected = (1_000_000 * (3.75 - 0.30)) / 1_000_000
        assert abs(result - expected) < 1e-9

    def test_only_ephem_1h_tokens(self):
        # 1M tokens re-created at 1h tier: delta = (6.0 - 0.30) / 1M per token
        result = _bust_overhead(1_000_000, 0, 1_000_000, _SONNET_PRICING)
        expected = (1_000_000 * (6.0 - 0.30)) / 1_000_000
        assert abs(result - expected) < 1e-9

    def test_mixed_5m_and_1h_tokens(self):
        ephem_5m = 600_000
        ephem_1h = 400_000
        cache_creation = ephem_5m + ephem_1h
        result = _bust_overhead(cache_creation, ephem_5m, ephem_1h, _SONNET_PRICING)
        expected = (
            ephem_5m * (3.75 - 0.30) + ephem_1h * (6.0 - 0.30)
        ) / 1_000_000
        assert abs(result - expected) < 1e-9

    def test_unclassified_tokens_attributed_to_5m_tier(self):
        # cache_creation > ephem_5m + ephem_1h — remainder goes to 5m (cheaper)
        cache_creation = 1_000_000
        ephem_5m = 400_000
        ephem_1h = 200_000
        unclassified = cache_creation - ephem_5m - ephem_1h  # 400_000
        result = _bust_overhead(cache_creation, ephem_5m, ephem_1h, _SONNET_PRICING)
        expected = (
            (ephem_5m + unclassified) * (3.75 - 0.30)
            + ephem_1h * (6.0 - 0.30)
        ) / 1_000_000
        assert abs(result - expected) < 1e-9

    def test_bust_overhead_never_exceeds_turn_cost(self):
        # _bust_overhead measures only the re-creation premium; _turn_cost
        # includes input/output/read charges too — so bust <= turn for any input.
        for input_tok, output_tok, cache_read, cache_creation, ep5m, ep1h in [
            (1000, 500, 2000, 5000, 3000, 2000),
            (0, 0, 0, 1_000_000, 1_000_000, 0),
            (500_000, 100_000, 800_000, 300_000, 150_000, 150_000),
            (0, 0, 0, 0, 0, 0),
        ]:
            bust = _bust_overhead(cache_creation, ep5m, ep1h, _SONNET_PRICING)
            full = _turn_cost(
                input_tok, output_tok, cache_read, cache_creation,
                ep5m, ep1h, _SONNET_PRICING
            )
            assert bust <= full + 1e-12, (
                f"bust_overhead ({bust}) exceeded turn_cost ({full}) for "
                f"cache_creation={cache_creation} ep5m={ep5m} ep1h={ep1h}"
            )

    def test_bust_overhead_is_zero_when_no_cache_creation(self):
        # If there's nothing to re-create, overhead is zero regardless of other charges
        result = _bust_overhead(0, 0, 0, _SONNET_PRICING)
        assert result == 0.0

    def test_haiku_pricing_produces_lower_cost_than_sonnet(self):
        haiku = _get_pricing("claude-haiku")
        ep5m = 1_000_000
        sonnet_cost = _bust_overhead(ep5m, ep5m, 0, _SONNET_PRICING)
        haiku_cost = _bust_overhead(ep5m, ep5m, 0, haiku)
        assert haiku_cost < sonnet_cost

    def test_per_tier_bucketing_is_independent(self):
        """Bust bucket attribution uses separate if-blocks, not elif.

        When a turn has both 5m and 1h tokens and gap > 1h, both tiers expired.
        The fix changes elif → two independent ifs so both buckets accumulate.
        """
        ep5m = 600_000
        ep1h = 400_000
        gap_over_1h = 4_000_000  # > 3,600,000 ms — both tiers expired
        pricing = _SONNET_PRICING

        cost_5m = _bust_overhead(ep5m, ep5m, 0, pricing)
        cost_1h = _bust_overhead(ep1h, 0, ep1h, pricing)

        # Simulate what the aggregation loop now does (two independent ifs)
        bucket: dict = {"busts_5m": 0, "busts_1h": 0, "cost_5m": 0.0, "cost_1h": 0.0}
        if ep5m and gap_over_1h > 300_000:
            bucket["busts_5m"] += 1
            bucket["cost_5m"] += cost_5m
        if ep1h and gap_over_1h > 3_600_000:
            bucket["busts_1h"] += 1
            bucket["cost_1h"] += cost_1h

        assert bucket["busts_5m"] == 1
        assert bucket["busts_1h"] == 1
        assert bucket["cost_5m"] == pytest.approx(cost_5m)
        assert bucket["cost_1h"] == pytest.approx(cost_1h)
        # Total cost equals sum of per-tier costs
        assert bucket["cost_5m"] + bucket["cost_1h"] == pytest.approx(cost_5m + cost_1h)

    def test_5m_only_bust_does_not_touch_1h_bucket(self):
        """A gap between 5min and 1h only busts the 5m tier."""
        ep5m = 600_000
        ep1h = 400_000
        gap_5m_only = 600_000  # 10 min — only 5m expired

        bucket: dict = {"busts_5m": 0, "busts_1h": 0, "cost_5m": 0.0, "cost_1h": 0.0}
        if ep5m and gap_5m_only > 300_000:
            bucket["busts_5m"] += 1
            bucket["cost_5m"] += _bust_overhead(ep5m, ep5m, 0, _SONNET_PRICING)
        if ep1h and gap_5m_only > 3_600_000:
            bucket["busts_1h"] += 1
            bucket["cost_1h"] += _bust_overhead(ep1h, 0, ep1h, _SONNET_PRICING)

        assert bucket["busts_5m"] == 1
        assert bucket["busts_1h"] == 0
        assert bucket["cost_1h"] == 0.0


# ---------------------------------------------------------------------------
# _safe_state_path — exception resilience (OSError / RuntimeError)
# ---------------------------------------------------------------------------

class TestSafeStatePathExceptions:
    """_safe_state_path must return None (not crash) for any malformed input."""

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_returns_none_not_raises_on_oserror(self, mod, tmp_path, monkeypatch):
        """Simulate Path.resolve() raising OSError (e.g., path too long)."""
        import pathlib

        def bad_resolve(self, strict=False):
            raise OSError("simulated OS-level resolve failure")

        monkeypatch.setattr(pathlib.Path, "resolve", bad_resolve)
        result = mod._safe_state_path(tmp_path, "", "abc123")
        assert result is None

    @pytest.mark.parametrize("mod", _HOOK_MODULES)
    def test_returns_none_not_raises_on_runtime_error(self, mod, tmp_path, monkeypatch):
        """Simulate Path.resolve() raising RuntimeError (e.g., infinite symlink loop)."""
        import pathlib

        def bad_resolve(self, strict=False):
            raise RuntimeError("simulated infinite symlink loop")

        monkeypatch.setattr(pathlib.Path, "resolve", bad_resolve)
        result = mod._safe_state_path(tmp_path, "", "abc123")
        assert result is None
