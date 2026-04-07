#!/usr/bin/env python3
"""
Agent Initializer - Creates a new Claude Code agent file from template.

Usage:
    init_agent.py <name> --path <agents-dir> [--output json]

Examples:
    init_agent.py code-reviewer --path ~/.claude/agents
    init_agent.py test-generator --path .claude/agents --output json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAX_AGENT_NAME_LENGTH = 50

AGENT_TEMPLATE = """\
---
name: {name}
description: >
  Use this agent when [TODO: describe trigger conditions].
  [TODO: "Recommended PROACTIVELY after..." if applicable.]
  Not for [TODO: out-of-scope tasks] — use [TODO: correct agent].
model: inherit
color: blue
# memory: project
# effort: medium
---

You are [TODO: expert role] specializing in [TODO: domain].

**Your Core Responsibilities:**
1. [TODO: primary responsibility]
2. [TODO: secondary responsibility]

**Process:**
1. [TODO: first step — imperative voice]
2. [TODO: second step]
3. [TODO: third step]

**Quality Standards:**
- [TODO: standard 1]
- [TODO: standard 2]

**Output Format:**
[TODO: describe what the agent returns and how it structures results]

**Edge Cases:**
- [TODO: edge case]: [TODO: how to handle]
"""


def normalize_name(name):
    """Normalize agent name to lowercase hyphen-case."""
    normalized = name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized


def validate_name(name):
    """Return (valid, error_message) for an agent name."""
    if len(name) < 3:
        return False, f"Name too short ({len(name)} chars). Min: 3"
    if len(name) > MAX_AGENT_NAME_LENGTH:
        return False, f"Name too long ({len(name)} chars). Max: {MAX_AGENT_NAME_LENGTH}"
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name):
        return False, (
            "Name must be lowercase letters/numbers/hyphens, "
            "starting and ending with alphanumeric"
        )
    return True, None


def init_agent(name, path, output_format) -> bool:
    agents_dir = Path(path).expanduser().resolve()
    agent_file = agents_dir / f"{name}.md"
    result = {"name": name, "path": str(agent_file), "success": False, "message": ""}

    if agent_file.exists():
        result["message"] = f"Agent file already exists: {agent_file}"
        if output_format == "json":
            print(json.dumps(result))
        else:
            print(f"[ERROR] {result['message']}")
        return False

    try:
        agents_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        result["message"] = f"Cannot create directory: {e}"
        if output_format == "json":
            print(json.dumps(result))
        else:
            print(f"[ERROR] {result['message']}")
        return False

    content = AGENT_TEMPLATE.format(name=name)
    try:
        agent_file.write_text(content)
    except Exception as e:
        result["message"] = f"Cannot write file: {e}"
        if output_format == "json":
            print(json.dumps(result))
        else:
            print(f"[ERROR] {result['message']}")
        return False

    result["success"] = True
    result["message"] = f"Agent '{name}' created at {agent_file}"
    if output_format == "json":
        print(json.dumps(result))
    else:
        print(f"[OK] {result['message']}")
        print("\nNext steps:")
        print(f"  1. Edit {agent_file}")
        print("     - Complete all [TODO] placeholders")
        print("     - Keep description to 50-70 tokens (no <example> blocks)")
        print("     - Write system prompt in second person ('You are...')")
        print(f"  2. Validate: validate_agent.py {agent_file} --output json")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a new agent file with template frontmatter and system prompt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("name", help="Agent name (normalized to hyphen-case)")
    parser.add_argument("--path", required=True, help="Output directory (e.g., ~/.claude/agents)")
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    name = normalize_name(args.name)
    if not name:
        msg = "Agent name must include at least one letter or digit."
        if args.output == "json":
            print(json.dumps({"success": False, "message": msg}))
        else:
            print(f"[ERROR] {msg}")
        sys.exit(1)

    valid, error = validate_name(name)
    if not valid:
        if args.output == "json":
            print(json.dumps({"success": False, "message": error}))
        else:
            print(f"[ERROR] {error}")
        sys.exit(1)

    if name != args.name and args.output != "json":
        print(f"Note: Normalized '{args.name}' to '{name}'")

    success = init_agent(name, args.path, args.output)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
