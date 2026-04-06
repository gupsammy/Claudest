#!/usr/bin/env python3
"""
Agent Validator - Validates Claude Code agent file structure and frontmatter.

Usage:
    validate_agent.py <agent-file> [--strict] [--output json]

Examples:
    validate_agent.py ~/.claude/agents/code-reviewer.md
    validate_agent.py agents/test-generator.md --output json
    validate_agent.py agents/my-agent.md --strict --output json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

MAX_AGENT_NAME_LENGTH = 50
ALLOWED_MODEL_ALIASES = {"inherit", "haiku", "sonnet", "opus"}
ALLOWED_COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
ALLOWED_PERMISSION_MODES = {
    "default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan",
}
ALLOWED_ISOLATION = {"worktree"}
ALLOWED_EFFORT = {"low", "medium", "high", "max"}
ALLOWED_MEMORY = {"user", "project", "local"}
ALLOWED_FRONTMATTER = {
    "name", "description", "model", "color", "tools", "disallowedTools",
    "permissionMode", "maxTurns", "skills", "mcpServers", "hooks",
    "memory", "background", "isolation", "effort", "initialPrompt",
    "version", "license",
}


def build_error(field, message, severity="critical"):
    return {"field": field, "message": message, "severity": severity}


def parse_frontmatter(content):
    """Extract YAML frontmatter. Returns (frontmatter_text, body, error)."""
    if not content.startswith("---"):
        return None, content, "No YAML frontmatter found (file must start with ---)"
    match = re.match(r"^---\n(.*?)\n---\n?", content, re.DOTALL)
    if not match:
        return None, content, "Invalid frontmatter format (missing closing ---)"
    return match.group(1), content[match.end():], None


def validate_agent(agent_path, strict=False):
    """Validate an agent file. Returns list of error dicts."""
    errors = []
    path = Path(agent_path).expanduser().resolve()

    if not path.exists():
        return [build_error("file", f"File not found: {path}")]
    if not path.is_file():
        return [build_error("file", f"Not a file: {path}")]
    if path.suffix != ".md":
        errors.append(build_error("file", "Agent file should have .md extension", "major"))

    content = path.read_text()
    frontmatter_text, body, parse_error = parse_frontmatter(content)

    if parse_error or frontmatter_text is None:
        return [build_error("frontmatter", parse_error or "Could not extract frontmatter")]

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return [build_error("frontmatter", "Frontmatter must be a YAML dictionary")]
    except yaml.YAMLError as e:
        return [build_error("frontmatter", f"Invalid YAML: {e}")]

    # Unexpected fields
    unexpected = set(frontmatter.keys()) - ALLOWED_FRONTMATTER
    if unexpected:
        severity = "major" if strict else "minor"
        errors.append(build_error(
            "frontmatter",
            f"Unexpected field(s): {', '.join(sorted(unexpected))}",
            severity,
        ))

    # name
    if "name" not in frontmatter:
        errors.append(build_error("name", "Missing required 'name' field"))
    else:
        name = str(frontmatter["name"]).strip()
        if len(name) < 3:
            errors.append(build_error("name", f"Name too short ({len(name)} chars). Min: 3"))
        elif len(name) > MAX_AGENT_NAME_LENGTH:
            errors.append(build_error(
                "name", f"Name too long ({len(name)} chars). Max: {MAX_AGENT_NAME_LENGTH}"
            ))
        elif not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name):
            errors.append(build_error(
                "name",
                "Name must be lowercase letters/numbers/hyphens, starting and ending with alphanumeric",
            ))

    # description
    if "description" not in frontmatter:
        errors.append(build_error("description", "Missing required 'description' field"))
    else:
        desc = str(frontmatter["description"]).strip()
        if not desc:
            errors.append(build_error("description", "Description is empty"))
        elif "[TODO" in desc:
            errors.append(build_error("description", "Description contains TODO placeholder"))
        else:
            if not desc.startswith("Use this agent when"):
                errors.append(build_error(
                    "description",
                    "Description should start with 'Use this agent when...'",
                    "major",
                ))
            token_estimate = len(desc.split())
            if token_estimate > 80:
                errors.append(build_error(
                    "description",
                    f"Description is ~{token_estimate} tokens — target 50-70 for context budget",
                    "minor",
                ))
            if "<example>" in desc:
                errors.append(build_error(
                    "description",
                    "<example> blocks waste context without improving routing — use concise prose instead",
                    "major",
                ))

    # model — accepts aliases or full model IDs (e.g., claude-sonnet-4-6)
    model = frontmatter.get("model")
    if model:
        model_str = str(model)
        if model_str not in ALLOWED_MODEL_ALIASES and not re.match(
            r"^claude-[a-z0-9-]+$", model_str
        ):
            errors.append(build_error(
                "model",
                f"Invalid model '{model}'. Must be an alias "
                f"({', '.join(sorted(ALLOWED_MODEL_ALIASES))}) or a full model ID (claude-*)",
            ))

    # color
    color = frontmatter.get("color")
    if color and str(color) not in ALLOWED_COLORS:
        errors.append(build_error(
            "color",
            f"Invalid color '{color}'. Must be one of: {', '.join(sorted(ALLOWED_COLORS))}",
            "minor",
        ))

    # permissionMode
    pm = frontmatter.get("permissionMode")
    if pm and str(pm) not in ALLOWED_PERMISSION_MODES:
        errors.append(build_error(
            "permissionMode",
            f"Invalid permissionMode '{pm}'. Must be one of: {', '.join(sorted(ALLOWED_PERMISSION_MODES))}",
        ))

    # isolation
    iso = frontmatter.get("isolation")
    if iso and str(iso) not in ALLOWED_ISOLATION:
        errors.append(build_error(
            "isolation",
            f"Invalid isolation '{iso}'. Only 'worktree' is supported",
        ))

    # maxTurns
    max_turns = frontmatter.get("maxTurns")
    if max_turns is not None:
        try:
            n = int(max_turns)
            if n < 1:
                errors.append(build_error("maxTurns", "maxTurns must be a positive integer"))
        except (ValueError, TypeError):
            errors.append(build_error("maxTurns", "maxTurns must be an integer"))

    # effort
    effort = frontmatter.get("effort")
    if effort and str(effort) not in ALLOWED_EFFORT:
        errors.append(build_error(
            "effort",
            f"Invalid effort '{effort}'. Must be one of: {', '.join(sorted(ALLOWED_EFFORT))}",
        ))

    # memory
    memory = frontmatter.get("memory")
    if memory and str(memory) not in ALLOWED_MEMORY:
        errors.append(build_error(
            "memory",
            f"Invalid memory '{memory}'. Must be one of: {', '.join(sorted(ALLOWED_MEMORY))}",
        ))

    # body
    body_text = body.strip() if body else ""
    if not body_text:
        errors.append(build_error("body", "Agent body (system prompt) is empty"))
    elif "[TODO" in body_text:
        errors.append(build_error(
            "body",
            "Body contains TODO placeholder — complete the system prompt before delivering",
            "major",
        ))
    elif strict and not (body_text.startswith("You are") or body_text.startswith("You're")):
        errors.append(build_error(
            "body",
            "System prompt should start with 'You are...' (second-person)",
            "minor",
        ))

    return errors


def main():
    parser = argparse.ArgumentParser(
        description="Validate a Claude Code agent .md file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("agent_file", help="Path to the agent .md file")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enable stricter checks (second-person body, unexpected field severity)",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    errors = validate_agent(args.agent_file, strict=args.strict)
    valid = len(errors) == 0

    if args.output == "json":
        result = {
            "valid": valid,
            "errors": errors,
            "path": str(Path(args.agent_file).expanduser().resolve()),
        }
        print(json.dumps(result, indent=2))
    else:
        if valid:
            print(f"[OK] Agent is valid: {args.agent_file}")
        else:
            print(f"[INVALID] {args.agent_file} — {len(errors)} issue(s):")
            for e in errors:
                severity = e["severity"].upper()
                print(f"  [{severity}] {e['field']}: {e['message']}")

    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
