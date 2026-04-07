#!/usr/bin/env python3
"""
Skill Packager - Creates a distributable .skill file

Usage:
    package_skill.py <path/to/skill-folder> [output-directory]

Example:
    package_skill.py ~/.claude/skills/my-skill
    package_skill.py ~/.claude/skills/my-skill ./dist
"""

import sys
import zipfile
from pathlib import Path

# Add scripts directory to path for sibling import
sys.path.insert(0, str(Path(__file__).parent))
from validate_skill import validate_skill


def package_skill(skill_path, output_dir=None):
    """Package a skill folder into a .skill file."""
    skill_path = Path(skill_path).expanduser().resolve()

    if not skill_path.exists():
        print(f"[ERROR] Skill folder not found: {skill_path}")
        return None

    if not skill_path.is_dir():
        print(f"[ERROR] Path is not a directory: {skill_path}")
        return None

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        print(f"[ERROR] SKILL.md not found in {skill_path}")
        return None

    # Validate before packaging
    print("Validating skill...")
    valid, message = validate_skill(skill_path)
    if not valid:
        print(f"[ERROR] Validation failed: {message}")
        print("   Fix validation errors before packaging.")
        return None
    print(f"[OK] {message}\n")

    # Determine output location
    skill_name = skill_path.name
    if output_dir:
        output_path = Path(output_dir).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)
    else:
        output_path = Path.cwd()

    skill_filename = output_path / f"{skill_name}.skill"

    # Create the .skill file (zip format)
    try:
        with zipfile.ZipFile(skill_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path in skill_path.rglob("*"):
                if file_path.is_file():
                    # Skip __pycache__ and .pyc files
                    if "__pycache__" in str(file_path) or file_path.suffix == ".pyc":
                        continue
                    arcname = file_path.relative_to(skill_path.parent)
                    zipf.write(file_path, arcname)
                    print(f"  Added: {arcname}")

        print(f"\n[OK] Packaged to: {skill_filename}")
        return skill_filename

    except Exception as e:
        print(f"[ERROR] Error creating .skill file: {e}")
        return None


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: package_skill.py <path/to/skill-folder> [output-directory]")
        print("\nExample:")
        print("  package_skill.py ~/.claude/skills/my-skill")
        print("  package_skill.py ~/.claude/skills/my-skill ./dist")
        sys.exit(1)

    skill_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Packaging skill: {skill_path}")
    if output_dir:
        print(f"   Output: {output_dir}")
    print()

    result = package_skill(skill_path, output_dir)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
