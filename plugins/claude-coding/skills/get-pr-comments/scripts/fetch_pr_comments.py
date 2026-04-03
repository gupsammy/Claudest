#!/usr/bin/env python3
"""
Fetch and organize all PR comments (issue-level, review bodies, inline review comments).

Usage:
  fetch_pr_comments.py <pr-number> [--repo OWNER/REPO] [--output json|text]

Examples:
  fetch_pr_comments.py 31
  fetch_pr_comments.py 33 --repo gupsammy/Claudest --output json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


BOT_SUFFIXES = ("[bot]",)


def run_gh(args: list[str]) -> str:
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        print(f"gh command failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return result.stdout.strip()


def _parse_slurped(raw: str) -> list:
    """Parse --slurp output which wraps each page's array in an outer array."""
    if not raw:
        return []
    pages = json.loads(raw)
    # --slurp produces [[page1...], [page2...], ...] — flatten to single list
    return [item for page in pages for item in page]


def is_bot(login: str) -> bool:
    return any(login.endswith(s) for s in BOT_SUFFIXES)


def _api_prefix(repo: str | None) -> str:
    """Return the repos/OWNER/REPO or repos/{owner}/{repo} prefix for gh api."""
    if repo:
        return f"repos/{repo}"
    return "repos/{owner}/{repo}"


def fetch_issue_comments(pr_number: int, repo: str | None) -> list[dict]:
    prefix = _api_prefix(repo)
    raw = run_gh(["api", f"{prefix}/issues/{pr_number}/comments",
                  "--paginate", "--slurp"])
    items = _parse_slurped(raw)
    return [
        {
            "type": "issue_comment",
            "id": c["id"],
            "user": c["user"]["login"],
            "is_bot": is_bot(c["user"]["login"]),
            "body": c["body"],
            "created_at": c["created_at"],
            "url": c.get("html_url", ""),
        }
        for c in items
    ]


def fetch_reviews(pr_number: int, repo: str | None) -> list[dict]:
    prefix = _api_prefix(repo)
    raw = run_gh(["api", f"{prefix}/pulls/{pr_number}/reviews",
                  "--paginate", "--slurp"])
    items = _parse_slurped(raw)
    return [
        {
            "type": "review",
            "id": r["id"],
            "user": r["user"]["login"],
            "is_bot": is_bot(r["user"]["login"]),
            "state": r["state"],
            "body": r["body"],
            "submitted_at": r.get("submitted_at", ""),
            "url": r.get("html_url", ""),
        }
        for r in items
        if r["body"].strip()  # skip empty review bodies
    ]


def fetch_inline_comments(pr_number: int, repo: str | None) -> list[dict]:
    prefix = _api_prefix(repo)
    raw = run_gh(["api", f"{prefix}/pulls/{pr_number}/comments",
                  "--paginate", "--slurp"])
    items = _parse_slurped(raw)
    return [
        {
            "type": "inline_comment",
            "id": c["id"],
            "user": c["user"]["login"],
            "is_bot": is_bot(c["user"]["login"]),
            "body": c["body"],
            "path": c.get("path", ""),
            "line": c.get("line") or c.get("original_line"),
            "side": c.get("side", ""),
            "diff_hunk": c.get("diff_hunk", ""),
            "created_at": c["created_at"],
            "url": c.get("html_url", ""),
            "in_reply_to_id": c.get("in_reply_to_id"),
        }
        for c in items
    ]


# --- Actionable item extraction ---

MUST_FIX_PATTERNS = [
    re.compile(r"^###?\s*must.fix", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\*\*must.fix\*\*", re.IGNORECASE),
    re.compile(r"^###?\s*(?:required|blocking|critical)", re.IGNORECASE | re.MULTILINE),
]

OPTIONAL_PATTERNS = [
    re.compile(r"^###?\s*optional", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\*\*optional\*\*", re.IGNORECASE),
    re.compile(r"^###?\s*(?:suggestions?|nit|minor|non-blocking)", re.IGNORECASE | re.MULTILINE),
]


INLINE_SEVERITY_PATTERNS = [
    (re.compile(r"!\[P1[^\]]*\]", re.IGNORECASE), "must_fix"),
    (re.compile(r"\*\*P1\*\*", re.IGNORECASE), "must_fix"),
    (re.compile(r"\bmust[- ]fix\b|\bblocking\b|\bcritical\b|\brequired\b", re.IGNORECASE), "must_fix"),
    (re.compile(r"!\[P2[^\]]*\]", re.IGNORECASE), "optional"),
    (re.compile(r"\*\*P2\*\*", re.IGNORECASE), "optional"),
    (re.compile(r"\boptional\b|\bsuggestion\b|\bnit\b|\bminor\b|\bnon-blocking\b", re.IGNORECASE), "optional"),
]


def _normalize_key(raw: str) -> str:
    """Normalize a dedup key by stripping inline code, numbers, and punctuation."""
    key = re.sub(r"`[^`]*`", "", raw)      # strip inline code refs
    key = re.sub(r"\d+\.\s*", "", key)      # strip numbering
    key = re.sub(r"[^\w\s]", " ", key)      # punctuation to spaces
    return " ".join(key.lower().split())     # collapse whitespace


def _extract_section_key(section: str) -> str:
    """Extract a dedup key from a section — uses the first bold title or header line."""
    # Match **bold title** on first or second line
    m = re.search(r"\*\*(.+?)\*\*", section[:300])
    if m:
        return _normalize_key(m.group(1))
    # Fall back to first non-header line
    for line in section.split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            return _normalize_key(line[:80])
    return _normalize_key(section[:80])


def extract_sections(body: str) -> dict:
    """Extract must-fix and optional sections from structured review bodies."""
    sections = {"must_fix": [], "optional": []}

    # Split by H2/H3 headers
    parts = re.split(r"(?=^###?\s)", body, flags=re.MULTILINE)

    for part in parts:
        part_stripped = part.strip()
        if not part_stripped:
            continue

        is_must_fix = any(p.search(part_stripped) for p in MUST_FIX_PATTERNS)
        is_optional = any(p.search(part_stripped) for p in OPTIONAL_PATTERNS)

        if is_must_fix or is_optional:
            # Skip sections that say "None" or are effectively empty
            body_lines = [
                ln for ln in part_stripped.split("\n")[1:]  # skip header line
                if ln.strip() and not ln.strip().startswith("---")
            ]
            body_text = " ".join(ln.strip() for ln in body_lines).lower()
            if re.match(r"^(none\.?|n/a\.?|no items\.?|nothing\.?)(\s|$)", body_text):
                continue

        if is_must_fix:
            sections["must_fix"].append(part_stripped)
        elif is_optional:
            sections["optional"].append(part_stripped)

    return sections


def classify_inline_comment(body: str) -> str | None:
    """Classify an inline comment as must_fix, optional, or None."""
    for pattern, severity in INLINE_SEVERITY_PATTERNS:
        if pattern.search(body[:500]):
            return severity
    return None


_STOP_WORDS = frozenset(
    "a an the in on at to of by for via from with is are was were and or not".split()
)


def _content_words(key: str) -> set[str]:
    """Extract significant words from a key, dropping stop words."""
    return {w for w in key.split() if w not in _STOP_WORDS and len(w) > 1}


def _keys_match(a: str, b: str) -> bool:
    """Check if two dedup keys refer to the same item using word overlap."""
    if a == b:
        return True
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return False
    overlap = len(wa & wb)
    smaller = min(len(wa), len(wb))
    return overlap / smaller >= 0.7 if smaller > 0 else False


def _deduplicate_actionable(items: list[dict]) -> list[dict]:
    """Keep only the latest version of each actionable item across review rounds.

    When the same reviewer posts multiple reviews, they often carry forward
    unresolved items verbatim. We deduplicate by extracting a key (the bold
    title) from each section and using word-overlap similarity to detect
    rephrased duplicates from the same user. Last occurrence wins (newest).
    """
    result: list[dict] = []
    seen: list[tuple[str, str]] = []  # (user, key) pairs

    for item in items:
        key = _extract_section_key(item["content"])
        user = item["source_user"]

        # Check if this matches an existing entry from the same user
        matched = False
        for idx, (seen_user, seen_key) in enumerate(seen):
            if seen_user == user and _keys_match(key, seen_key):
                # Replace with newer version
                result[idx] = item
                seen[idx] = (user, key)
                matched = True
                break

        if not matched:
            result.append(item)
            seen.append((user, key))

    return result


def build_result(pr_number: int, repo: str | None) -> dict:
    issue_comments = fetch_issue_comments(pr_number, repo)
    reviews = fetch_reviews(pr_number, repo)
    inline_comments = fetch_inline_comments(pr_number, repo)

    all_comments = issue_comments + reviews + inline_comments

    human_comments = [c for c in all_comments if not c["is_bot"]]
    bot_comments = [c for c in all_comments if c["is_bot"]]

    # Extract actionable items from review bodies and issue comments
    actionable = {"must_fix": [], "optional": []}
    for c in reviews + issue_comments:
        sections = extract_sections(c["body"])
        for item in sections["must_fix"]:
            actionable["must_fix"].append({
                "source_user": c["user"],
                "content": item,
                "source_type": c["type"],
            })
        for item in sections["optional"]:
            actionable["optional"].append({
                "source_user": c["user"],
                "content": item,
                "source_type": c["type"],
            })

    # Extract actionable items from inline comments
    for c in inline_comments:
        severity = classify_inline_comment(c["body"])
        if severity:
            location = f"`{c['path']}:{c.get('line', '?')}`"
            actionable[severity].append({
                "source_user": c["user"],
                "content": f"{location} — {c['body']}",
                "source_type": "inline_comment",
                "path": c["path"],
                "line": c.get("line"),
            })

    # Deduplicate across review rounds
    actionable["must_fix"] = _deduplicate_actionable(actionable["must_fix"])
    actionable["optional"] = _deduplicate_actionable(actionable["optional"])

    return {
        "pr_number": pr_number,
        "total_comments": len(all_comments),
        "human_count": len(human_comments),
        "bot_count": len(bot_comments),
        "human_comments": human_comments,
        "bot_comments": bot_comments,
        "inline_comments": inline_comments,
        "actionable": actionable,
    }


BOT_BODY_LIMIT = 300  # max chars for bot comment bodies


def _short_date(ts: str) -> str:
    """Extract date portion from ISO timestamp (2025-01-15T14:30:00Z -> 2025-01-15)."""
    return ts[:10] if ts and len(ts) >= 10 else ""


def _truncate(text: str, limit: int) -> str:
    """Truncate text to limit chars, appending ellipsis if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " [...]"


def _format_comment_header(comment: dict) -> str:
    """Compact one-line header: user | type | date | location."""
    parts = [comment["user"], comment["type"]]
    ts = _short_date(comment.get("created_at") or comment.get("submitted_at", ""))
    if ts:
        parts.append(ts)
    if comment["type"] == "inline_comment":
        parts.append(f'{comment.get("path", "?")}:{comment.get("line", "?")}')
    elif comment["type"] == "review" and comment.get("state"):
        parts.append(comment["state"])
    return " | ".join(parts)


def format_text(result: dict) -> str:
    lines = []
    pr = result["pr_number"]
    lines.append(f"PR #{pr}: {result['human_count']} human, "
                 f"{result['bot_count']} bot comments")

    # Actionable items first — most important for the consuming agent
    must_fix = result["actionable"]["must_fix"]
    optional = result["actionable"]["optional"]

    if must_fix:
        lines.append("\n== MUST FIX ==")
        for item in must_fix:
            lines.append(f"\n@{item['source_user']}:")
            lines.append(item["content"])

    if optional:
        lines.append("\n== OPTIONAL ==")
        for item in optional:
            lines.append(f"\n@{item['source_user']}:")
            lines.append(item["content"])

    # Human comments — full bodies, compact headers
    human = result["human_comments"]
    if human:
        lines.append("\n== HUMAN COMMENTS ==")
        for c in human:
            lines.append(f"\n> {_format_comment_header(c)}")
            lines.append(c["body"])

    # Bot comments — truncated bodies to save tokens
    bot = result["bot_comments"]
    if bot:
        lines.append("\n== BOT COMMENTS ==")
        for c in bot:
            lines.append(f"\n> {_format_comment_header(c)}")
            lines.append(_truncate(c["body"], BOT_BODY_LIMIT))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pr_number", type=int, help="PR number to fetch comments for")
    parser.add_argument("--repo", default=None,
                        help="Repository in OWNER/REPO format (default: inferred by gh)")
    parser.add_argument("--output", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    args = parser.parse_args()

    result = build_result(args.pr_number, args.repo)

    if args.output == "json":
        print(json.dumps(result, indent=2))
    else:
        print(format_text(result))

    sys.exit(0)


if __name__ == "__main__":
    main()
