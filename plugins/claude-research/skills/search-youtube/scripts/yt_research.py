#!/usr/bin/env python3
"""Multi-platform video research toolkit powered by yt-dlp.

Usage:
  yt_research.py [global flags] <subcommand> [args]
  yt_research.py search <query> [flags]
  yt_research.py metadata <url> [flags]
  yt_research.py transcript <url> [flags]
  yt_research.py audio <url> [flags]
  yt_research.py channel <url|@handle> [flags]

Examples:
  yt_research.py search "python async tutorial" --limit 5
  yt_research.py transcript "https://youtube.com/watch?v=abc" --save -t ml
  yt_research.py channel "@ThePrimeagen" --limit 30
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "0.2.0"
DEFAULT_DIR = Path.home() / "youtube-research"

# Resolved in main(); used by output_result and cmd_* handlers
_IS_TTY: bool = False


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def sanitize_title(title: str) -> str:
    """Sanitize a string for use as a filename."""
    title = re.sub(r'[/:\\?\"<>|*]', "-", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:200]


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def log(msg: str, quiet: bool = False) -> None:
    """Print diagnostic message to stderr (suppressed by --quiet)."""
    if not quiet:
        print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class ResearchError(Exception):
    """Raised by handlers; carries an exit code, machine-readable code, and hint."""

    def __init__(
        self,
        message: str,
        code: int = 1,
        error_code: str = "error",
        hint: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.error_code = error_code
        self.hint = hint


def emit_error(exc: ResearchError) -> None:
    """Write error to stderr: JSON object when piped, plain text when TTY."""
    if sys.stderr.isatty():
        print(f"Error: {exc}", file=sys.stderr)
    else:
        obj: dict = {"error": exc.error_code, "message": str(exc)}
        if exc.hint:
            obj["hint"] = exc.hint
        print(json.dumps(obj), file=sys.stderr)


def find_ytdlp() -> str:
    """Return path to yt-dlp binary or raise ResearchError."""
    path = shutil.which("yt-dlp")
    if path:
        return path
    raise ResearchError(
        "yt-dlp not found. Install with: pip install yt-dlp",
        code=2,
        error_code="dependency_missing",
        hint="pip install yt-dlp",
    )


def run_ytdlp(
    args: list,
    cookies: str | None = None,
    quiet: bool = False,
    timeout: int = 300,
    verbose: bool = False,
) -> subprocess.CompletedProcess:
    """Invoke yt-dlp and return the CompletedProcess."""
    cmd = [find_ytdlp()] + args
    if cookies:
        cmd.extend(["--cookies-from-browser", cookies])
    if quiet and not verbose:
        cmd.extend(["--quiet", "--no-warnings"])
    if verbose:
        print(f"[yt-dlp] {' '.join(cmd)}", file=sys.stderr)
    try:
        # When verbose, don't capture output so yt-dlp's own output is visible
        return subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ResearchError(
            f"yt-dlp timed out after {timeout}s",
            code=3,
            error_code="timeout",
        )
    except FileNotFoundError:
        raise ResearchError(
            "yt-dlp not found",
            code=2,
            error_code="dependency_missing",
            hint="pip install yt-dlp",
        )


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def format_entry(entry: dict, channel_fallback: str = "") -> dict:
    """Normalize a yt-dlp entry dict to the standard result shape.

    channel_fallback is used when per-entry channel is None (e.g. flat-playlist
    on channel pages where the channel is implicit from the playlist URL).
    """
    return {
        "id": entry.get("id") or "",
        "title": entry.get("title") or "",
        "channel": entry.get("channel") or entry.get("uploader") or channel_fallback,
        "duration": entry.get("duration"),
        "duration_string": entry.get("duration_string") or "",
        "view_count": entry.get("view_count"),
        "upload_date": entry.get("upload_date") or "",
        "url": entry.get("webpage_url") or entry.get("url") or "",
        "description": (entry.get("description") or "")[:500],
    }


def entries_to_text(entries: list) -> str:
    """Render a list of entry dicts as numbered human-readable text."""
    lines = []
    for i, e in enumerate(entries, 1):
        dur = e.get("duration_string") or ""
        views = e.get("view_count")
        view_str = f" | {views:,} views" if views else ""
        lines.append(f"{i}. {e['title']}")
        lines.append(f"   {e['channel']} | {dur}{view_str}")
        lines.append(f"   {e['url']}")
        lines.append("")
    return "\n".join(lines)


def output_result(data, fmt: str, ndjson: bool = False) -> None:
    """Print data in the requested format."""
    if fmt == "json":
        if ndjson and isinstance(data, list):
            for item in data:
                print(json.dumps(item, ensure_ascii=False))
        else:
            print(json.dumps(data, indent=2, ensure_ascii=False))
    elif isinstance(data, list):
        print(entries_to_text(data))
    elif isinstance(data, dict):
        for key, val in data.items():
            if key == "chapters" and val:
                print("chapters:")
                for ch in val:
                    print(f"  {ch['start_time']:.0f}s-{ch['end_time']:.0f}s: {ch['title']}")
            elif key == "description":
                desc = val[:300] + ("..." if len(val) > 300 else "")
                print(f"description: {desc}")
            elif isinstance(val, list):
                print(f"{key}: {', '.join(str(v) for v in val)}")
            else:
                print(f"{key}: {val}")
    else:
        print(data)


# ---------------------------------------------------------------------------
# VTT / SRT transcript cleaning
# ---------------------------------------------------------------------------

def clean_vtt(content: str, keep_timestamps: bool = False) -> str:
    """Convert VTT/SRT content to clean text or normalized SRT."""
    lines = content.split("\n")
    if keep_timestamps:
        return _to_srt(lines)
    return _to_plain(lines)


def _to_plain(lines: list) -> str:
    """Strip VTT to deduplicated plain text."""
    seen: set = set()
    result: list = []
    for raw in lines:
        line = raw.strip()
        if (
            not line
            or line.startswith("WEBVTT")
            or line.startswith("Kind:")
            or line.startswith("Language:")
            or "-->" in line
            or re.match(r"^\d+$", line)
        ):
            continue
        clean = re.sub(r"<[^>]*>", "", line)
        clean = (
            clean.replace("&amp;", "&")
            .replace("&gt;", ">")
            .replace("&lt;", "<")
            .replace("&nbsp;", " ")
            .replace("&quot;", '"')
            .replace("\\h", " ")
        )
        # Collapse multiple spaces from \h replacements
        clean = re.sub(r" {2,}", " ", clean).strip()
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return "\n".join(result)


def _to_srt(lines: list) -> str:
    """Convert VTT lines to SRT format preserving timestamps.

    Auto-generated captions use a scrolling window where each cue repeats
    the previous line plus appends a new one. We deduplicate by tracking
    the previous cue's text and only emitting lines that are new.
    """
    result: list = []
    counter = 0
    prev_texts: list = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            ts = line.replace(".", ",")
            i += 1
            cur_texts: list = []
            while i < len(lines) and lines[i].strip():
                text = re.sub(r"<[^>]*>", "", lines[i].strip())
                text = (
                    text.replace("&amp;", "&")
                    .replace("&gt;", ">")
                    .replace("&lt;", "<")
                    .replace("\\h", " ")
                )
                text = re.sub(r" {2,}", " ", text).strip()
                if text:
                    cur_texts.append(text)
                i += 1
            # Only keep lines not already shown in the previous cue
            new_texts = [t for t in cur_texts if t not in prev_texts]
            if new_texts:
                counter += 1
                result.append(str(counter))
                result.append(ts)
                result.extend(new_texts)
                result.append("")
            prev_texts = cur_texts
        else:
            i += 1
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Subcommand: search
# ---------------------------------------------------------------------------

def cmd_search(args, g):
    """Search YouTube for videos matching a query."""
    query = args.query

    if args.count > 50:
        log(f"Warning: --limit capped at 50 (requested {args.count})", g.quiet)
    count = min(args.count, 50)

    has_filters = any(
        [args.min_duration, args.max_duration, args.after, args.before, args.min_views]
    )
    fetch_count = min(count * 2, 50) if has_filters else count

    if g.dry_run:
        print(f'yt-dlp --flat-playlist --dump-single-json "ytsearch{fetch_count}:{query}"')
        return

    log(f"Searching: {query} (fetching {fetch_count})...", g.quiet)

    result = run_ytdlp(
        ["--flat-playlist", "--dump-single-json", f"ytsearch{fetch_count}:{query}"],
        cookies=g.cookies,
        quiet=True,
        timeout=g._timeout,
        verbose=getattr(g, "verbose", False),
    )

    if result.returncode != 0:
        raise ResearchError(
            f"yt-dlp search failed: {result.stderr.strip()}",
            code=1,
            error_code="ytdlp_error",
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ResearchError(
            "Failed to parse yt-dlp output",
            code=1,
            error_code="parse_error",
        )

    raw_entries = [format_entry(e) for e in data.get("entries", [])]

    if getattr(g, "verbose", False):
        log(f"[verbose] Raw entries from yt-dlp: {len(raw_entries)}", g.quiet)

    entries = raw_entries

    # Client-side filtering
    if args.min_duration:
        entries = [e for e in entries if (e.get("duration") or 0) >= args.min_duration]
    if args.max_duration:
        entries = [e for e in entries if (e.get("duration") or 0) <= args.max_duration]

    # Date filtering: yt-dlp flat-playlist search results often return upload_date=None.
    # Gracefully degrade — warn and skip the filter rather than silently returning nothing.
    if args.after or args.before:
        dated = [e for e in entries if e.get("upload_date")]
        if not dated:
            log(
                "Warning: yt-dlp search results have no upload_date metadata; "
                "--after/--before filters were skipped. "
                "Use --metadata on individual URLs to fetch full dates.",
                g.quiet,
            )
        else:
            if args.after:
                entries = [e for e in entries if (e.get("upload_date") or "") >= args.after]
            if args.before:
                entries = [e for e in entries if (e.get("upload_date") or "") <= args.before]

    if args.min_views:
        entries = [e for e in entries if (e.get("view_count") or 0) >= args.min_views]

    entries = entries[:count]

    if not entries:
        if _IS_TTY:
            log("No results found matching criteria.", g.quiet)
        else:
            output_result([], g.format or "json", ndjson=getattr(g, "ndjson", False))
        return

    output_result(entries, g.format or "json", ndjson=getattr(g, "ndjson", False))


# ---------------------------------------------------------------------------
# Subcommand: metadata
# ---------------------------------------------------------------------------

def cmd_metadata(args, g):
    """Extract full metadata for a video or playlist."""
    url = args.url

    ytdlp_args = (
        ["--flat-playlist", "--dump-single-json"]
        if args.playlist
        else ["--dump-json", "--skip-download"]
    )

    if g.dry_run:
        print(f'yt-dlp {" ".join(ytdlp_args)} "{url}"')
        return

    log(f"Extracting metadata: {url}...", g.quiet)

    result = run_ytdlp(
        ytdlp_args + [url],
        cookies=g.cookies,
        quiet=True,
        timeout=g._timeout,
        verbose=getattr(g, "verbose", False),
    )
    if result.returncode != 0:
        raise ResearchError(
            f"yt-dlp failed: {result.stderr.strip()}",
            code=1,
            error_code="ytdlp_error",
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ResearchError(
            "Failed to parse yt-dlp output",
            code=1,
            error_code="parse_error",
        )

    fmt = g.format or "json"

    if args.playlist:
        entries = [format_entry(e) for e in data.get("entries", [])]
        output_result(entries, fmt, ndjson=getattr(g, "ndjson", False))
    else:
        meta = {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "channel": data.get("channel") or data.get("uploader", ""),
            "channel_url": data.get("channel_url", ""),
            "duration": data.get("duration"),
            "duration_string": data.get("duration_string", ""),
            "upload_date": data.get("upload_date", ""),
            "view_count": data.get("view_count"),
            "like_count": data.get("like_count"),
            "comment_count": data.get("comment_count"),
            "tags": data.get("tags", []),
            "categories": data.get("categories", []),
            "chapters": [
                {
                    "title": c.get("title", ""),
                    "start_time": c.get("start_time", 0),
                    "end_time": c.get("end_time", 0),
                }
                for c in (data.get("chapters") or [])
            ],
            "thumbnail_url": data.get("thumbnail", ""),
            "subtitle_languages": sorted(
                set(
                    list((data.get("subtitles") or {}).keys())
                    + list((data.get("automatic_captions") or {}).keys())
                )
            ),
            "url": data.get("webpage_url", ""),
        }
        output_result(meta, fmt, ndjson=getattr(g, "ndjson", False))


# ---------------------------------------------------------------------------
# Subcommand: transcript
# ---------------------------------------------------------------------------

def cmd_transcript(args, g):
    """Download and clean transcript for a video."""
    url = args.url
    lang = args.lang

    # List available languages
    if lang == "all":
        if g.dry_run:
            print(f'yt-dlp --dump-json --skip-download "{url}"')
            return

        log(f"Listing subtitles for: {url}...", g.quiet)
        result = run_ytdlp(
            ["--dump-json", "--skip-download", url],
            cookies=g.cookies,
            quiet=True,
            timeout=g._timeout,
            verbose=getattr(g, "verbose", False),
        )
        if result.returncode != 0:
            raise ResearchError(
                f"yt-dlp failed: {result.stderr.strip()}",
                code=1,
                error_code="ytdlp_error",
            )

        data = json.loads(result.stdout)
        info = {
            "manual_subtitles": sorted((data.get("subtitles") or {}).keys()),
            "auto_generated": sorted((data.get("automatic_captions") or {}).keys()),
        }
        print(json.dumps(info, indent=2))
        return

    if g.dry_run:
        print(
            f'yt-dlp --write-subs --write-auto-subs --sub-langs "{lang}" '
            f'--convert-subs srt --skip-download "{url}"'
        )
        return

    log(f"Downloading transcript ({lang}): {url}...", g.quiet)

    with tempfile.TemporaryDirectory() as tmpdir:
        sub_file = None

        # Try manual subs first, then auto-generated
        for flag in ["--write-subs", "--write-auto-subs"]:
            run_ytdlp(
                [
                    flag,
                    "--sub-langs", lang,
                    "--convert-subs", "srt",
                    "--skip-download",
                    "-o", os.path.join(tmpdir, "%(id)s"),
                    url,
                ],
                cookies=g.cookies,
                quiet=True,
                timeout=g._timeout,
                verbose=getattr(g, "verbose", False),
            )
            found = [f for f in os.listdir(tmpdir) if f.endswith((".srt", ".vtt"))]
            if found:
                sub_file = os.path.join(tmpdir, found[0])
                break

        if not sub_file:
            raise ResearchError(
                f"No subtitles available in '{lang}'.",
                code=4,
                error_code="no_subtitles",
                hint=f"yt_research.py transcript --lang all {url}",
            )

        with open(sub_file, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()

        cleaned = clean_vtt(raw, keep_timestamps=args.timestamps)

        if args.save:
            title_r = run_ytdlp(
                ["--print", "%(title)s", "--skip-download", url],
                cookies=g.cookies,
                quiet=True,
                timeout=g._timeout,
            )
            title = sanitize_title(title_r.stdout.strip() or "transcript")
            ext = ".srt" if args.timestamps else ".txt"
            out_dir = ensure_dir(Path(g.dir) / g.topic)
            out_path = out_dir / f"{title}{ext}"
            out_path.write_text(cleaned, encoding="utf-8")
            print(str(out_path))
            log(f"Saved to: {out_path}", g.quiet)
        else:
            print(cleaned)


# ---------------------------------------------------------------------------
# Subcommand: audio
# ---------------------------------------------------------------------------

def cmd_audio(args, g):
    """Download audio from a video."""
    url = args.url
    audio_fmt = args.audio_format
    quality = args.quality

    if g.dry_run:
        print(
            f'yt-dlp -x --audio-format {audio_fmt} --audio-quality {quality} "{url}"'
        )
        return

    log(f"Downloading audio: {url}...", g.quiet)

    # Audio downloads can be slow; use at least 600s regardless of _timeout
    audio_timeout = max(g._timeout, 600)

    # Get title for filename
    title_r = run_ytdlp(
        ["--print", "%(title)s", "--skip-download", url],
        cookies=g.cookies,
        quiet=True,
        timeout=g._timeout,
        verbose=getattr(g, "verbose", False),
    )
    title = sanitize_title(title_r.stdout.strip() or "audio")

    out_dir = ensure_dir(Path(g.dir) / g.topic / "audio")
    out_template = str(out_dir / f"{title}.%(ext)s")

    result = run_ytdlp(
        [
            "-x",
            "--audio-format", audio_fmt,
            "--audio-quality", quality,
            "-o", out_template,
            url,
        ],
        cookies=g.cookies,
        quiet=g.quiet,
        timeout=audio_timeout,
        verbose=getattr(g, "verbose", False),
    )

    if result.returncode != 0:
        raise ResearchError(
            f"Audio download failed: {result.stderr.strip()}",
            code=1,
            error_code="ytdlp_error",
        )

    # Find the actual output file (extension may vary)
    matches = sorted(out_dir.glob(f"{title}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    saved = matches[0] if matches else out_dir / f"{title}.{audio_fmt}"
    print(str(saved))
    log(f"Saved to: {saved}", g.quiet)


# ---------------------------------------------------------------------------
# Subcommand: channel
# ---------------------------------------------------------------------------

def cmd_channel(args, g):
    """Scan a channel's videos, shorts, streams, or playlists."""
    raw = args.url
    tab = args.tab

    # Expand @handle → full URL
    if raw.startswith("@"):
        url = f"https://www.youtube.com/{raw}/{tab}"
    elif "youtube.com" in raw and f"/{tab}" not in raw:
        url = raw.rstrip("/") + "/" + tab
    else:
        url = raw

    has_filters = args.after or args.before
    fetch_limit = args.limit * 2 if has_filters else args.limit

    if g.dry_run:
        print(
            f'yt-dlp --flat-playlist --dump-single-json '
            f'--playlist-items "1:{fetch_limit}" "{url}"'
        )
        return

    log(f"Scanning channel: {url} (limit {fetch_limit})...", g.quiet)

    result = run_ytdlp(
        [
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-items", f"1:{fetch_limit}",
            url,
        ],
        cookies=g.cookies,
        quiet=True,
        timeout=g._timeout,
        verbose=getattr(g, "verbose", False),
    )

    if result.returncode != 0:
        raise ResearchError(
            f"yt-dlp failed: {result.stderr.strip()}",
            code=1,
            error_code="ytdlp_error",
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ResearchError(
            "Failed to parse yt-dlp output",
            code=1,
            error_code="parse_error",
        )

    # Channel name is on the playlist object, not per-entry in flat mode
    ch_name = data.get("channel") or data.get("uploader") or ""
    entries = [format_entry(e, channel_fallback=ch_name) for e in data.get("entries", [])]

    if args.after:
        entries = [e for e in entries if (e.get("upload_date") or "") >= args.after]
    if args.before:
        entries = [e for e in entries if (e.get("upload_date") or "") <= args.before]

    if args.sort == "views":
        entries.sort(key=lambda e: e.get("view_count") or 0, reverse=True)

    entries = entries[: args.limit]

    if not entries:
        if _IS_TTY:
            log("No videos found matching criteria.", g.quiet)
        else:
            output_result([], g.format or "json", ndjson=getattr(g, "ndjson", False))
        return

    fmt = g.format or "json"
    output_result(entries, fmt, ndjson=getattr(g, "ndjson", False))


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def run_batch(handler, items: list, args, g):
    """Run a handler for each item (URL or search query), collecting results."""
    results = []
    errors = []

    for raw_item in items:
        item = raw_item.strip()
        if not item or item.startswith("#") or item.startswith(";"):
            continue

        # For search, each line is a query; for all others it's a URL
        if handler is cmd_search:
            args.query = item
        else:
            args.url = item

        old_stdout = sys.stdout
        sys.stdout = buf = io.StringIO()

        try:
            handler(args, g)
            output = buf.getvalue()
        except ResearchError as exc:
            sys.stdout = old_stdout
            errors.append({"item": item, "error": str(exc), "code": exc.code})
            log(f"Failed: {item} — {exc}", g.quiet)
            continue
        finally:
            sys.stdout = old_stdout

        fmt = g.format or "json"
        if fmt == "json":
            try:
                results.append(json.loads(output))
            except json.JSONDecodeError:
                results.append(output.strip())
        else:
            results.append(output.strip())

    if not results:
        log("All items failed.")
        sys.exit(3)

    fmt = g.format or "json"
    if fmt == "json":
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for i, r in enumerate(results):
            if i > 0:
                print("\n--- next ---\n")
            print(r)

    if errors:
        log(
            f"\n{len(errors)} of {len(errors) + len(results)} items failed.",
            g.quiet,
        )


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _add_global_flags(p: argparse.ArgumentParser) -> None:
    """Add global flags to a parser (called on each subparser)."""
    p.add_argument(
        "-f", "--format", choices=["json", "text"], default=None,
        help="Output format. Default: text when stdout is a TTY, json when piped.",
    )
    p.add_argument(
        "--human", action="store_true",
        help="Force human-readable text output even when piped (alias for --format text)",
    )
    p.add_argument(
        "--ndjson", action="store_true",
        help="Output one JSON object per line (NDJSON) instead of a JSON array",
    )
    p.add_argument(
        "-t", "--topic", default="general",
        help="Topic subdirectory for saved files (default: general)",
    )
    p.add_argument(
        "-d", "--dir", default=None,
        help="Base output directory (default: ~/youtube-research)",
    )
    p.add_argument(
        "--cookies", default=None, metavar="BROWSER",
        help="Browser for cookie auth (chrome, firefox, safari, brave, edge)",
    )
    p.add_argument("-q", "--quiet", action="store_true",
                   help="Suppress progress messages on stderr")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show yt-dlp commands and extra diagnostic output")
    p.add_argument("--dry-run", action="store_true",
                   help="Show yt-dlp command without executing")
    p.add_argument(
        "-b", "--batch", default=None, metavar="FILE",
        help=(
            "Read items from file (one per line). Use - for stdin. "
            "For 'search', each line is treated as a search query."
        ),
    )
    p.add_argument("--no-color", action="store_true", help="Disable colored output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt_research.py",
        description="Multi-platform video research toolkit powered by yt-dlp.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            '  %(prog)s search "python async tutorial" --limit 5\n'
            '  %(prog)s transcript "https://youtube.com/watch?v=abc" --save -t ml\n'
            "  %(prog)s channel @ThePrimeagen --limit 30\n"
            '  %(prog)s transcript --batch urls.txt --save -t talks\n'
            '  %(prog)s search --batch queries.txt --limit 5\n'
            "\nenvironment variables:\n"
            "  YT_RESEARCH_DIR      Base output directory (default: ~/youtube-research)\n"
            "  YT_RESEARCH_TOPIC    Topic subdirectory for saved files (default: general)\n"
            "  YT_RESEARCH_COOKIES  Browser for cookie auth (chrome, firefox, safari, brave, edge)\n"
            "  YT_RESEARCH_TIMEOUT  Request timeout in seconds (default: 300)\n"
            "  NO_COLOR             Disable colored output when set\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    subs = parser.add_subparsers(dest="command", help="subcommands")

    # -- search --
    p_s = subs.add_parser("search", help="Search YouTube for videos")
    _add_global_flags(p_s)
    p_s.add_argument("query", nargs="?", help="Search query string")
    p_s.add_argument(
        "--limit", "--count", type=int, default=10, dest="count",
        help="Max results (default: 10, max: 50)",
    )
    p_s.add_argument("--min-duration", type=int, default=None, metavar="SECS")
    p_s.add_argument("--max-duration", type=int, default=None, metavar="SECS")
    p_s.add_argument("--after", default=None, metavar="YYYYMMDD")
    p_s.add_argument("--before", default=None, metavar="YYYYMMDD")
    p_s.add_argument("--min-views", type=int, default=None)

    # -- metadata --
    p_m = subs.add_parser("metadata", help="Extract video/playlist metadata")
    _add_global_flags(p_m)
    p_m.add_argument("url", help="Video or playlist URL")
    p_m.add_argument(
        "--playlist", action="store_true",
        help="Treat as playlist; return flat entry list",
    )

    # -- transcript --
    p_t = subs.add_parser("transcript", help="Download and clean transcript")
    _add_global_flags(p_t)
    p_t.add_argument("url", nargs="?", help="Video URL")
    p_t.add_argument(
        "-l", "--lang", default="en",
        help="Subtitle language (default: en; 'all' to list)",
    )
    p_t.add_argument(
        "--timestamps", action="store_true",
        help="Preserve SRT timestamps instead of plain text",
    )
    p_t.add_argument("--save", action="store_true",
                     help="Save to file in topic directory")

    # -- audio --
    p_a = subs.add_parser("audio", help="Download audio from video")
    _add_global_flags(p_a)
    p_a.add_argument("url", nargs="?", help="Video URL")
    p_a.add_argument(
        "--audio-format", default="mp3",
        choices=["mp3", "m4a", "opus", "wav"],
        help="Audio format (default: mp3)",
    )
    p_a.add_argument("--quality", default="192K",
                     help="Audio quality/bitrate (default: 192K)")

    # -- channel --
    p_c = subs.add_parser("channel", help="Scan channel videos/playlists")
    _add_global_flags(p_c)
    p_c.add_argument("url", help="Channel URL or @handle")
    p_c.add_argument("--limit", type=int, default=20,
                     help="Max entries (default: 20)")
    p_c.add_argument(
        "--tab", default="videos",
        choices=["videos", "shorts", "streams", "playlists"],
    )
    p_c.add_argument("--after", default=None, metavar="YYYYMMDD")
    p_c.add_argument("--before", default=None, metavar="YYYYMMDD")
    p_c.add_argument("--sort", default="date", choices=["date", "views"],
                     help="Sort order")

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

HANDLERS = {
    "search": cmd_search,
    "metadata": cmd_metadata,
    "transcript": cmd_transcript,
    "audio": cmd_audio,
    "channel": cmd_channel,
}


def main() -> None:
    global _IS_TTY

    parser = build_parser()

    # Handle bare invocation with no subcommand
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        parser.print_help()
        if len(sys.argv) < 2:
            sys.exit(1)
        sys.exit(0)
    if sys.argv[1] == "--version":
        parser.parse_args(["--version"])

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Resolve TTY state (module-global for use by handlers)
    _IS_TTY = sys.stdout.isatty()

    # Resolve defaults from env
    if args.dir is None:
        args.dir = os.environ.get("YT_RESEARCH_DIR", str(DEFAULT_DIR))
    if args.topic == "general":
        args.topic = os.environ.get("YT_RESEARCH_TOPIC", "general")
    if args.cookies is None:
        args.cookies = os.environ.get("YT_RESEARCH_COOKIES")

    # Resolve configurable timeout
    args._timeout = int(os.environ.get("YT_RESEARCH_TIMEOUT", "300"))

    # Resolve output format via TTY auto-detection
    if getattr(args, "human", False):
        args.format = "text"
    elif args.format is None:
        if args.command == "transcript":
            args.format = "text"  # always text — content, not structure
        else:
            args.format = "text" if _IS_TTY else "json"

    handler = HANDLERS[args.command]

    try:
        if args.batch:
            if args.batch == "-":
                items = sys.stdin.read().strip().split("\n")
            else:
                with open(args.batch, "r") as fh:
                    items = fh.read().strip().split("\n")
            run_batch(handler, items, args, args)
        else:
            if args.command not in ("search",) and not getattr(args, "url", None) and not args.batch:
                parser.parse_args([args.command, "--help"])
            if args.command == "search" and not getattr(args, "query", None) and not args.batch:
                parser.parse_args(["search", "--help"])
            handler(args, args)
    except ResearchError as exc:
        emit_error(exc)
        sys.exit(exc.code)


if __name__ == "__main__":
    main()
