#!/usr/bin/env python3
"""
Skill Initializer - Creates a new skill from template

Usage:
    init_skill.py <skill-name> --path <path> [--resources scripts,references,assets] [--examples]

Examples:
    init_skill.py my-new-skill --path ~/.claude/skills
    init_skill.py my-new-skill --path ~/.claude/skills --resources scripts,references
    init_skill.py my-api-helper --path .claude/skills --resources scripts --examples
"""

import argparse
import re
import sys
from pathlib import Path

MAX_SKILL_NAME_LENGTH = 64
ALLOWED_RESOURCES = {"scripts", "references", "assets"}

SKILL_TEMPLATE = """---
name: {skill_name}
description: >
  [TODO: Write trigger-rich description. Include 3-5 varied trigger phrases.
  Example: "Use when user asks to 'do X', 'handle Y', or mentions Z."]
---

# {skill_title}

[TODO: 1-2 sentences explaining what this skill enables]

## Process

[TODO: Choose structure that fits this skill's purpose:

**Workflow-Based** (sequential processes)
- Step-by-step procedures with clear ordering
- Example: ## Overview -> ## Step 1 -> ## Step 2...

**Task-Based** (tool collections)
- Different operations/capabilities grouped by function
- Example: ## Overview -> ## Task Category 1 -> ## Task Category 2...

**Reference/Guidelines** (standards or specifications)
- Brand guidelines, coding standards, requirements
- Example: ## Overview -> ## Guidelines -> ## Specifications...

Delete this section when done.]

## [First Section]

[TODO: Add content. Use imperative voice ("Analyze", "Generate").
No first-person ("I will"). Include code samples for technical skills.]

## Resources

[TODO: Delete this section if no resources needed. Otherwise, document what's in each directory:]

### scripts/
Executable code run directly to perform operations.

### references/
Documentation loaded into context as needed. Keep SKILL.md lean; put detailed info here.

### assets/
Files used in output (templates, images, fonts) - not loaded into context.
"""

EXAMPLE_SCRIPT = '''#!/usr/bin/env python3
"""
Example helper script for {skill_name}

Replace with actual implementation or delete if not needed.
"""

def main():
    print("Example script for {skill_name}")
    # TODO: Add actual script logic

if __name__ == "__main__":
    main()
'''

EXAMPLE_REFERENCE = """# Reference Documentation for {skill_title}

Replace with actual reference content or delete if not needed.

## When Reference Docs Are Useful

- Comprehensive API documentation
- Detailed workflow guides
- Complex multi-step processes
- Information too lengthy for SKILL.md
- Content only needed for specific use cases
"""

EXAMPLE_ASSET = """# Example Asset

This placeholder represents where asset files would be stored.
Replace with actual files (templates, images, fonts) or delete if not needed.

Asset files are NOT loaded into context - they're used in output.
"""


def normalize_skill_name(skill_name):
    """Normalize a skill name to lowercase hyphen-case."""
    normalized = skill_name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized


def title_case_skill_name(skill_name):
    """Convert hyphenated skill name to Title Case."""
    return " ".join(word.capitalize() for word in skill_name.split("-"))


def parse_resources(raw_resources):
    if not raw_resources:
        return []
    resources = [item.strip() for item in raw_resources.split(",") if item.strip()]
    invalid = sorted({item for item in resources if item not in ALLOWED_RESOURCES})
    if invalid:
        allowed = ", ".join(sorted(ALLOWED_RESOURCES))
        print(f"[ERROR] Unknown resource type(s): {', '.join(invalid)}")
        print(f"   Allowed: {allowed}")
        sys.exit(1)
    return list(dict.fromkeys(resources))  # dedupe preserving order


def create_resource_dirs(skill_dir, skill_name, skill_title, resources, include_examples) -> None:
    for resource in resources:
        resource_dir = skill_dir / resource
        resource_dir.mkdir(exist_ok=True)
        if resource == "scripts":
            if include_examples:
                example_script = resource_dir / "example.py"
                example_script.write_text(EXAMPLE_SCRIPT.format(skill_name=skill_name))
                example_script.chmod(0o755)
                print("[OK] Created scripts/example.py")
            else:
                print("[OK] Created scripts/")
        elif resource == "references":
            if include_examples:
                example_ref = resource_dir / "reference.md"
                example_ref.write_text(EXAMPLE_REFERENCE.format(skill_title=skill_title))
                print("[OK] Created references/reference.md")
            else:
                print("[OK] Created references/")
        elif resource == "assets":
            if include_examples:
                example_asset = resource_dir / "example_asset.txt"
                example_asset.write_text(EXAMPLE_ASSET)
                print("[OK] Created assets/example_asset.txt")
            else:
                print("[OK] Created assets/")


def init_skill(skill_name, path, resources, include_examples):
    skill_dir = Path(path).expanduser().resolve() / skill_name

    if skill_dir.exists():
        print(f"[ERROR] Skill directory already exists: {skill_dir}")
        return None

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        print(f"[OK] Created skill directory: {skill_dir}")
    except Exception as e:
        print(f"[ERROR] Error creating directory: {e}")
        return None

    skill_title = title_case_skill_name(skill_name)
    skill_content = SKILL_TEMPLATE.format(skill_name=skill_name, skill_title=skill_title)

    skill_md_path = skill_dir / "SKILL.md"
    try:
        skill_md_path.write_text(skill_content)
        print("[OK] Created SKILL.md")
    except Exception as e:
        print(f"[ERROR] Error creating SKILL.md: {e}")
        return None

    if resources:
        try:
            create_resource_dirs(skill_dir, skill_name, skill_title, resources, include_examples)
        except Exception as e:
            print(f"[ERROR] Error creating resource directories: {e}")
            return None

    print(f"\n[OK] Skill '{skill_name}' initialized at {skill_dir}")
    print("\nNext steps:")
    print("1. Edit SKILL.md - complete TODOs and update description")
    if resources:
        print("2. Add/customize resources in scripts/, references/, assets/")
    print("3. Test the skill, then package with package_skill.py")

    return skill_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new skill directory with template SKILL.md")
    parser.add_argument("skill_name", help="Skill name (normalized to hyphen-case)")
    parser.add_argument("--path", required=True, help="Output directory (e.g., ~/.claude/skills)")
    parser.add_argument("--resources", default="", help="Comma-separated: scripts,references,assets")
    parser.add_argument("--examples", action="store_true", help="Create example files in resource dirs")
    args = parser.parse_args()

    skill_name = normalize_skill_name(args.skill_name)
    if not skill_name:
        print("[ERROR] Skill name must include at least one letter or digit.")
        sys.exit(1)
    if len(skill_name) > MAX_SKILL_NAME_LENGTH:
        print(f"[ERROR] Skill name too long ({len(skill_name)} chars). Max: {MAX_SKILL_NAME_LENGTH}")
        sys.exit(1)
    if skill_name != args.skill_name:
        print(f"Note: Normalized '{args.skill_name}' to '{skill_name}'")

    resources = parse_resources(args.resources)
    if args.examples and not resources:
        print("[ERROR] --examples requires --resources")
        sys.exit(1)

    print(f"Initializing skill: {skill_name}")
    print(f"   Location: {args.path}")
    if resources:
        print(f"   Resources: {', '.join(resources)}")
    print()

    result = init_skill(skill_name, args.path, resources, args.examples)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
