#!/usr/bin/env python3
"""
OpenClaw Skill Validator - Validates skill structure and frontmatter

Usage:
    validate_claw_skill.py <skill_directory> [--output json] [--strict]

Examples:
    validate_claw_skill.py ~/.openclaw/skills/my-skill
    validate_claw_skill.py ./skills/my-skill --output json
"""

import argparse
import json
import re
import sys
from pathlib import Path

MAX_SKILL_NAME_LENGTH = 64

# Valid OpenClaw frontmatter fields
ALLOWED_FRONTMATTER = {
    "name",
    "description",
    "user-invocable",
    "disable-model-invocation",
    "command-dispatch",
    "command-tool",
    "command-arg-mode",
    "metadata",
    "argument-hint",
    "homepage",  # valid alias for metadata.openclaw.homepage
}

# Claude Code-only fields that are invalid in OpenClaw
CLAUDE_CODE_FIELDS = {
    "model": "set at agent level in openclaw.json, not per-skill",
    "context": "context: fork is not supported; use sessions_spawn in skill body",
    "agent": "specialized agent routing is not available in OpenClaw",
    "allowed-tools": "tool policy is gateway config, not frontmatter",
    "hooks": "hooks are system-level, not per-skill in OpenClaw",
    "license": "not a valid OpenClaw skill field",
}

# Claude Code tool names that should not appear in OpenClaw skill bodies
CLAUDE_CODE_TOOLS = [
    "Bash", "WebSearch", "WebFetch", "Glob", "Grep",
    "Task", "Skill", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
    "NotebookEdit",
]


def parse_frontmatter(content):
    """Extract and parse YAML frontmatter from SKILL.md content without PyYAML."""
    if not content.startswith("---"):
        return None, "No YAML frontmatter found (must start with ---)", None

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None, "Invalid frontmatter format (missing closing ---)", None

    frontmatter_text = match.group(1)
    body = content[match.end():].strip()

    # Minimal YAML parser for flat key: value pairs (sufficient for skill frontmatter)
    frontmatter = {}
    lines = frontmatter_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue

        # Match key: value
        key_match = re.match(r"^([a-zA-Z][a-zA-Z0-9_-]*)\s*:\s*(.*)", line)
        if not key_match:
            i += 1
            continue

        key = key_match.group(1)
        value_raw = key_match.group(2).strip()

        # Folded/literal scalar (> or |)
        if value_raw in (">", "|", ">-", "|-"):
            scalar_lines = []
            i += 1
            while i < len(lines) and (lines[i].startswith("  ") or lines[i].strip() == ""):
                scalar_lines.append(lines[i].strip())
                i += 1
            if value_raw.startswith(">"):
                frontmatter[key] = " ".join(scalar_lines).strip()
            else:
                frontmatter[key] = "\n".join(scalar_lines).strip()
            continue

        # Quoted string
        if value_raw.startswith("'") and value_raw.endswith("'"):
            frontmatter[key] = value_raw[1:-1]
        elif value_raw.startswith('"') and value_raw.endswith('"'):
            frontmatter[key] = value_raw[1:-1]
        elif value_raw.lower() == "true":
            frontmatter[key] = True
        elif value_raw.lower() == "false":
            frontmatter[key] = False
        elif value_raw == "":
            frontmatter[key] = None
        else:
            frontmatter[key] = value_raw

        i += 1

    return frontmatter, None, body


def validate_skill(skill_path, strict: bool=False):
    """
    Validate an OpenClaw skill directory.

    Returns a dict: {"valid": bool, "errors": [{"field": str, "message": str, "severity": str}]}
    Severity levels: "critical", "major", "minor"
    """
    skill_path = Path(skill_path).expanduser().resolve()
    errors = []

    def add(field, message, severity="major"):
        errors.append({"field": field, "message": message, "severity": severity})

    # Check SKILL.md exists
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        add("SKILL.md", "SKILL.md not found", "critical")
        return {"valid": False, "errors": errors}

    content = skill_md.read_text()

    # Parse frontmatter
    frontmatter, parse_error, body = parse_frontmatter(content)
    if parse_error:
        add("frontmatter", parse_error, "critical")
        return {"valid": False, "errors": errors}

    if not isinstance(frontmatter, dict):
        add("frontmatter", "Frontmatter must be a YAML dictionary", "critical")
        return {"valid": False, "errors": errors}

    # Check for Claude Code-only fields (invalid in OpenClaw)
    for field, reason in CLAUDE_CODE_FIELDS.items():
        if field in frontmatter:
            add(
                field,
                f"'{field}' is a Claude Code-only field and is not valid in OpenClaw ({reason})",
                "critical",
            )

    # Check for unexpected fields
    unexpected = set(frontmatter.keys()) - ALLOWED_FRONTMATTER - set(CLAUDE_CODE_FIELDS.keys())
    if unexpected:
        add(
            "frontmatter",
            f"Unrecognized frontmatter field(s): {', '.join(sorted(unexpected))}",
            "major",
        )

    # Required fields
    if "name" not in frontmatter:
        add("name", "Missing required 'name' field", "critical")
    if "description" not in frontmatter:
        add("description", "Missing required 'description' field", "critical")

    # Validate name
    name = frontmatter.get("name", "")
    if isinstance(name, str):
        name = name.strip()
        if name:
            if not re.match(r"^[a-z0-9-]+$", name):
                add("name", f"Name '{name}' must be hyphen-case (lowercase, digits, hyphens only)", "critical")
            elif name.startswith("-") or name.endswith("-") or "--" in name:
                add("name", f"Name '{name}' cannot start/end with hyphen or have consecutive hyphens", "major")
            elif len(name) > MAX_SKILL_NAME_LENGTH:
                add("name", f"Name too long ({len(name)} chars). Max: {MAX_SKILL_NAME_LENGTH}", "major")
    elif name is not None:
        add("name", f"'name' must be a string, got {type(name).__name__}", "critical")

    # Validate description
    description = frontmatter.get("description", "")
    if isinstance(description, str):
        description = description.strip()
        if description:
            if len(description) > 1024:
                add("description", f"Description too long ({len(description)} chars). Max: 1024", "major")
            if "[TODO" in description:
                add("description", "Description contains TODO placeholder — please complete it", "major")
    elif description is not None:
        add("description", f"'description' must be a string, got {type(description).__name__}", "major")

    # Validate metadata: must be parseable as JSON (not multi-line YAML mapping)
    metadata_raw = frontmatter.get("metadata")
    if metadata_raw is not None:
        if isinstance(metadata_raw, str):
            try:
                json.loads(metadata_raw)
            except json.JSONDecodeError as e:
                add(
                    "metadata",
                    f"'metadata' must be a single-line JSON string parseable as JSON. Error: {e}. "
                    "Multi-line YAML mappings under metadata are not valid in OpenClaw.",
                    "critical",
                )
        elif isinstance(metadata_raw, dict):
            # Parsed as YAML dict — this means it was written as multi-line YAML, which is invalid
            add(
                "metadata",
                "'metadata' was parsed as a YAML mapping (multi-line). "
                "OpenClaw requires metadata to be a single-line JSON string: "
                "metadata: '{\"openclaw\": {\"emoji\": \"🔧\"}}'",
                "critical",
            )
        else:
            add("metadata", f"'metadata' must be a JSON string, got {type(metadata_raw).__name__}", "major")

    # Validate command-dispatch and command-tool consistency
    has_dispatch = "command-dispatch" in frontmatter
    has_tool = "command-tool" in frontmatter
    if has_dispatch and not has_tool:
        add("command-tool", "'command-dispatch' is set but 'command-tool' is missing", "major")
    if has_tool and not has_dispatch:
        add("command-dispatch", "'command-tool' is set but 'command-dispatch' is missing", "minor")

    # Check body
    if not body:
        add("body", "SKILL.md body is empty", "major")
    elif "[TODO" in body:
        add("body", "SKILL.md body contains TODO placeholders — please complete them", "major")
    else:
        # Strip inline code spans (content between backtick pairs) before scanning —
        # a checklist item saying "do not use `Bash`" should not trigger a Bash violation.
        body_no_code = re.sub(r"`[^`\n]+`", "", body)

        # Warn if Claude Code tool names appear in body outside of code spans
        for tool_name in CLAUDE_CODE_TOOLS:
            pattern = rf'\b{re.escape(tool_name)}\b'
            if re.search(pattern, body_no_code):
                add(
                    "body",
                    f"Body references Claude Code tool '{tool_name}'. "
                    f"Use the OpenClaw equivalent instead (see references/claw-patterns.md).",
                    "minor",
                )

        # Check for $CLAUDE_PLUGIN_ROOT usage outside of code spans
        if "$CLAUDE_PLUGIN_ROOT" in body_no_code:
            add(
                "body",
                "Body uses '$CLAUDE_PLUGIN_ROOT' (Claude Code path convention). "
                "Use '{baseDir}' instead for OpenClaw skill-relative paths.",
                "major",
            )

    valid = not any(e["severity"] in ("critical", "major") for e in errors)
    return {"valid": valid, "errors": errors}


def format_text_output(result, skill_path):
    lines = [f"Validating: {skill_path}"]
    if result["valid"]:
        lines.append("[OK] Skill is valid")
    else:
        lines.append("[FAIL] Skill has validation errors")

    for e in result["errors"]:
        prefix = "[ERROR]" if e["severity"] in ("critical", "major") else "[WARN]"
        lines.append(f"  {prefix} [{e['severity'].upper()}] {e['field']}: {e['message']}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate an OpenClaw skill directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("skill_directory", help="Path to skill directory containing SKILL.md")
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat minor issues as errors (affects exit code)",
    )
    args = parser.parse_args()

    result = validate_skill(args.skill_directory, strict=args.strict)

    if args.output == "json":
        print(json.dumps(result, indent=2))
    else:
        print(format_text_output(result, args.skill_directory))

    if args.strict:
        success = len(result["errors"]) == 0
    else:
        success = result["valid"]

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
