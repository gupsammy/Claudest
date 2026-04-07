#!/usr/bin/env python3
"""
Skill Validator - Validates skill structure and frontmatter for Claude Code

Usage:
    validate_skill.py <skill_directory>

Example:
    validate_skill.py ~/.claude/skills/my-skill
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MAX_SKILL_NAME_LENGTH = 64

# Claude Code supported frontmatter fields
ALLOWED_FRONTMATTER = {
    "name",
    "description",
    "allowed-tools",
    "hooks",
    "user-invocable",
    "disable-model-invocation",
    "argument-hint",
    "license",
    "metadata",
}


def _parse_frontmatter(text):
    """Parse simple YAML frontmatter (key: value pairs) without PyYAML.

    Handles scalar values and multi-line folded scalars (>).
    List-valued fields (e.g. allowed-tools) are stored as joined strings,
    not Python lists — sufficient for key-presence validation.
    Returns a dict, or None if parsing fails.
    """
    result = {}
    current_key = None
    current_value_lines = []

    def _flush():
        if current_key is not None:
            val = " ".join(current_value_lines).strip()
            # Unquote if wrapped in matching quotes
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            # Convert YAML booleans
            if val.lower() in ("true", "yes"):
                val = True
            elif val.lower() in ("false", "no"):
                val = False
            result[current_key] = val

    for line in text.splitlines():
        # Continuation line (indented, part of multi-line scalar like >)
        if current_key and line and line[0] in (" ", "\t"):
            current_value_lines.append(line.strip())
            continue
        # New key: value pair
        m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(.*)", line)
        if m:
            _flush()
            current_key = m.group(1)
            val = m.group(2).strip()
            # Folded scalar indicator — value follows on next lines
            current_value_lines = [] if val in (">", "|") else [val]
            continue
        # Blank or unparseable line — flush and reset
        if not line.strip():
            _flush()
            current_key = None
            current_value_lines = []

    _flush()
    return result if result else None


def validate_skill(skill_path):
    """Validate a skill directory for Claude Code compatibility."""
    skill_path = Path(skill_path).expanduser().resolve()

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "No YAML frontmatter found (must start with ---)"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format (missing closing ---)"

    frontmatter_text = match.group(1)

    frontmatter = _parse_frontmatter(frontmatter_text)
    if frontmatter is None:
        return False, "Frontmatter must be a YAML dictionary with simple key: value pairs"

    # Check for unexpected keys
    unexpected = set(frontmatter.keys()) - ALLOWED_FRONTMATTER
    if unexpected:
        return False, f"Unexpected frontmatter key(s): {', '.join(sorted(unexpected))}"

    # Required fields
    if "name" not in frontmatter:
        return False, "Missing required 'name' field"
    if "description" not in frontmatter:
        return False, "Missing required 'description' field"

    # Validate name
    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"'name' must be a string, got {type(name).__name__}"
    name = name.strip()
    if name:
        if not re.match(r"^[a-z0-9-]+$", name):
            return False, f"Name '{name}' must be hyphen-case (lowercase, digits, hyphens only)"
        if name.startswith("-") or name.endswith("-") or "--" in name:
            return False, f"Name '{name}' cannot start/end with hyphen or have consecutive hyphens"
        if len(name) > MAX_SKILL_NAME_LENGTH:
            return False, f"Name too long ({len(name)} chars). Max: {MAX_SKILL_NAME_LENGTH}"

    # Validate description
    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"'description' must be a string, got {type(description).__name__}"
    description = description.strip()
    if description:
        if len(description) > 1024:
            return False, f"Description too long ({len(description)} chars). Max: 1024"
        if "[TODO" in description:
            return False, "Description contains TODO placeholder - please complete it"

    # Check body has content
    body = content[match.end():].strip()
    if not body:
        return False, "SKILL.md body is empty"
    if "[TODO" in body:
        return False, "SKILL.md contains TODO placeholders - please complete them"

    return True, "Skill is valid"


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: validate_skill.py <skill_directory>")
        print("Example: validate_skill.py ~/.claude/skills/my-skill")
        sys.exit(1)

    skill_path = sys.argv[1]
    print(f"Validating: {skill_path}")

    valid, message = validate_skill(skill_path)
    if valid:
        print(f"[OK] {message}")
    else:
        print(f"[ERROR] {message}")

    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
