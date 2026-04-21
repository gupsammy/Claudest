#!/usr/bin/env python3
"""Run project validation before committing.

Detects project type from the root directory and runs the appropriate
linter or build check scoped to the user's staged changes — not the
whole project. Pre-existing issues in untouched files never block a
commit whose diff is clean.

Exit codes:
  0 — validation passed
  1 — validation failed (see output for details)
  2 — no validator applies (no marker file, or no relevant staged files)

Usage:
  validate.py <project-root> [--output text|json]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


# scope semantics:
#   "files"  — pass filtered staged file list after cmd_prefix
#   "dirs"   — pass unique parent dirs of staged files (go-style packages)
#   "gated"  — run project-scope cmd only if any staged file matches extensions
VALIDATORS = [
    {
        "marker": "Cargo.toml",
        "tool": "cargo",
        "scope": "files",
        "extensions": [".rs"],
        "cmd_prefix": ["cargo", "fmt", "--check", "--"],
    },
    {
        "marker": "package.json",
        "tool": "npm",
        "scope": "gated",
        "extensions": [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"],
        "cmd": ["npm", "run", "lint", "--if-present"],
    },
    {
        "marker": "pyproject.toml",
        "tool": "ruff",
        "scope": "files",
        "extensions": [".py"],
        "cmd_prefix": ["ruff", "check"],
    },
    {
        "marker": "go.mod",
        "tool": "go",
        "scope": "dirs",
        "extensions": [".go"],
        "cmd_prefix": ["go", "vet"],
    },
    {
        "marker": "Gemfile",
        "tool": "rubocop",
        "scope": "files",
        "extensions": [".rb"],
        "cmd_prefix": ["bundle", "exec", "rubocop", "--no-color"],
        "fallback_prefix": ["rubocop", "--no-color"],
    },
    {
        "marker": "pom.xml",
        "tool": "maven",
        "scope": "gated",
        "extensions": [".java"],
        "cmd": ["mvn", "validate", "-q"],
    },
    {
        "marker": "mix.exs",
        "tool": "mix",
        "scope": "files",
        "extensions": [".ex", ".exs"],
        "cmd_prefix": ["mix", "format", "--check-formatted"],
    },
    {
        "marker": "composer.json",
        "tool": "composer",
        "scope": "gated",
        "extensions": ["composer.json", "composer.lock"],
        "cmd": ["composer", "validate", "--strict"],
    },
]


def get_staged_files(root: Path) -> list[str]:
    """Return staged file paths relative to root. Empty list if git unavailable."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def match_extension(path: str, extensions: list[str]) -> bool:
    """True if path ends with any of the given suffixes (handles both '.py' and 'composer.json' forms)."""
    return any(path.endswith(ext) for ext in extensions)


def filter_by_extensions(files: list[str], extensions: list[str]) -> list[str]:
    return [f for f in files if match_extension(f, extensions)]


def unique_package_dirs(files: list[str]) -> list[str]:
    """Collapse a file list into './dir/' paths, one per unique parent directory."""
    dirs = set()
    for f in files:
        parent = os.path.dirname(f)
        dirs.add(f"./{parent}/" if parent else "./")
    return sorted(dirs)


def detect_validator(root: Path) -> dict | None:
    for v in VALIDATORS:
        if (root / v["marker"]).exists():
            return v
    return None


def build_command(validator: dict, staged: list[str]) -> list[str] | None:
    """Build the command to run. Returns None if validator's scope criteria aren't met."""
    scope = validator["scope"]
    exts = validator.get("extensions", [])

    if scope == "files":
        matched = filter_by_extensions(staged, exts)
        if not matched:
            return None
        return validator["cmd_prefix"] + matched

    if scope == "dirs":
        matched = filter_by_extensions(staged, exts)
        if not matched:
            return None
        return validator["cmd_prefix"] + unique_package_dirs(matched)

    if scope == "gated":
        if not filter_by_extensions(staged, exts):
            return None
        return validator["cmd"]

    return None


def build_fallback(validator: dict, staged: list[str]) -> list[str] | None:
    """Some validators have a fallback tool (e.g. rubocop without bundler). Same scope rules."""
    if "fallback_prefix" in validator:
        matched = filter_by_extensions(staged, validator.get("extensions", []))
        if not matched:
            return None
        return validator["fallback_prefix"] + matched
    if "fallback_cmd" in validator and validator["fallback_cmd"]:
        return validator["fallback_cmd"]
    return None


def run_command(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except FileNotFoundError:
        return False, f"Command not found: {cmd[0]}"


def emit(args, payload: dict, *, is_error: bool = False) -> None:
    if args.output == "json":
        print(json.dumps(payload))
    else:
        msg = payload.get("output", "")
        status = payload.get("status")
        if status:
            print(f"Validation {status} ({payload.get('tool', '?')})")
        if msg:
            stream = sys.stderr if is_error else sys.stdout
            print(msg, file=stream)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_root", help="Path to project root directory")
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    if not root.is_dir():
        emit(args, {"valid": False, "tool": None, "output": f"Not a directory: {root}"}, is_error=True)
        sys.exit(1)

    validator = detect_validator(root)
    if validator is None:
        emit(args, {
            "valid": False,
            "tool": None,
            "output": "No validator found (no known marker file in project root)",
        })
        sys.exit(2)

    staged = get_staged_files(root)
    cmd = build_command(validator, staged)
    if cmd is None:
        emit(args, {
            "valid": False,
            "tool": validator["tool"],
            "output": f"{validator['tool']}: no staged files match {validator.get('extensions', [])} — skipping",
        })
        sys.exit(2)

    tool = validator["tool"]
    valid, output = run_command(cmd, root)

    if not valid and "not found" in output:
        fallback = build_fallback(validator, staged)
        if fallback is not None:
            valid, output = run_command(fallback, root)
        if not valid and "not found" in output:
            emit(args, {
                "valid": False,
                "tool": tool,
                "output": f"{tool} not installed; skipping validation",
            })
            sys.exit(2)

    emit(args, {
        "valid": valid,
        "tool": tool,
        "output": output,
        "status": "passed" if valid else "failed",
    })
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
