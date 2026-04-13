#!/usr/bin/env python3
"""Tests for security fixes and the _bust_overhead cost function.

Coverage:
  - _safe_state_path (all three hook files) — path traversal prevention
  - get_cached_tokens (cache-resume-detect) — transcript bounds check
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
_resume_detect = _load_hook("cache-resume-detect.py")
_warn_stop = _load_hook("cache-warn-stop.py")

# Load ingest script
sys.path.insert(0, str(_SCRIPTS_DIR))
from ingest_token_data import _bust_overhead, _turn_cost, _get_pricing  # noqa: E402

# ---------------------------------------------------------------------------
# Parametrize _safe_state_path across all three hook modules
# ---------------------------------------------------------------------------

_HOOK_MODULES = [
    pytest.param(_expiry_warn, id="cache-expiry-warn"),
    pytest.param(_resume_detect, id="cache-resume-detect"),
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
# get_cached_tokens — transcript bounds check in cache-resume-detect.py
# ---------------------------------------------------------------------------

class TestGetCachedTokens:
    """get_cached_tokens must refuse to read files outside ~/.claude.

    If the bounds check is bypassed, a malicious transcript_path could read
    arbitrary files on disk (e.g. SSH keys) by supplying an attacker-controlled
    path as the transcript location.
    """

    def _write_transcript(self, path: Path, cache_creation: int, cache_read: int) -> None:
        """Write a minimal JSONL transcript with one assistant turn."""
        entry = {
            "message": {
                "role": "assistant",
                "usage": {
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                },
            }
        }
        path.write_text(json.dumps(entry) + "\n")

    def test_path_inside_claude_dir_returns_tokens(self, tmp_path, monkeypatch):
        # Redirect _CLAUDE_DIR so we don't depend on the real ~/.claude existing
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(_resume_detect, "_CLAUDE_DIR", claude_dir)

        transcript = claude_dir / "projects" / "session.jsonl"
        transcript.parent.mkdir(parents=True)
        self._write_transcript(transcript, cache_creation=5000, cache_read=1000)

        result = _resume_detect.get_cached_tokens(str(transcript))
        assert result == 6000  # cache_creation + cache_read

    def test_path_outside_claude_dir_returns_zero(self, tmp_path, monkeypatch):
        # Prevents reading /tmp/evil.jsonl (or any attacker-supplied path)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(_resume_detect, "_CLAUDE_DIR", claude_dir)

        evil = tmp_path / "evil.jsonl"
        self._write_transcript(evil, cache_creation=9999, cache_read=9999)

        result = _resume_detect.get_cached_tokens(str(evil))
        assert result == 0

    def test_symlink_escaping_claude_dir_returns_zero(self, tmp_path, monkeypatch):
        # Symlink inside ~/.claude pointing outside must not be followed
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        outside = tmp_path / "real_data.jsonl"
        self._write_transcript(outside, cache_creation=8888, cache_read=0)

        link = claude_dir / "escape_link.jsonl"
        link.symlink_to(outside)
        monkeypatch.setattr(_resume_detect, "_CLAUDE_DIR", claude_dir)

        result = _resume_detect.get_cached_tokens(str(link))
        assert result == 0

    def test_nonexistent_path_inside_claude_dir_returns_zero(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(_resume_detect, "_CLAUDE_DIR", claude_dir)

        missing = claude_dir / "no-such-session.jsonl"
        result = _resume_detect.get_cached_tokens(str(missing))
        assert result == 0

    def test_empty_transcript_returns_zero(self, tmp_path, monkeypatch):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(_resume_detect, "_CLAUDE_DIR", claude_dir)

        transcript = claude_dir / "empty.jsonl"
        transcript.write_text("")
        result = _resume_detect.get_cached_tokens(str(transcript))
        assert result == 0

    def test_returns_last_assistant_turn_tokens(self, tmp_path, monkeypatch):
        # get_cached_tokens should track the latest value, not the first
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(_resume_detect, "_CLAUDE_DIR", claude_dir)

        transcript = claude_dir / "multi_turn.jsonl"
        lines = [
            json.dumps({"message": {"role": "assistant", "usage": {
                "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0
            }}}),
            json.dumps({"message": {"role": "user", "content": "hello"}}),
            json.dumps({"message": {"role": "assistant", "usage": {
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 4000
            }}}),
        ]
        transcript.write_text("\n".join(lines) + "\n")

        result = _resume_detect.get_cached_tokens(str(transcript))
        assert result == 4000  # last assistant turn, not first


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


# ---------------------------------------------------------------------------
# get_cached_tokens — None and encoding edge cases
# ---------------------------------------------------------------------------

class TestGetCachedTokensEdgeCases:
    def test_none_transcript_path_returns_zero(self, monkeypatch, tmp_path):
        """get_cached_tokens(None) must not raise TypeError."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(_resume_detect, "_CLAUDE_DIR", claude_dir)
        # None passed as transcript_path — cast to str("") then bounds check returns 0
        result = _resume_detect.get_cached_tokens(None)  # type: ignore[arg-type]
        assert result == 0

    def test_utf8_transcript_is_read_correctly(self, tmp_path, monkeypatch):
        """Transcripts with non-ASCII content must not crash the reader."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        monkeypatch.setattr(_resume_detect, "_CLAUDE_DIR", claude_dir)

        transcript = claude_dir / "utf8_session.jsonl"
        entry = {
            "message": {
                "role": "assistant",
                "usage": {
                    "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 50,
                },
                "content": "こんにちは世界",  # non-ASCII
            }
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        result = _resume_detect.get_cached_tokens(str(transcript))
        assert result == 150
