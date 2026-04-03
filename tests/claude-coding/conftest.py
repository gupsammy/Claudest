"""Configure sys.path so tests can import fetch_pr_comments directly."""

from __future__ import annotations

import sys
from pathlib import Path

# Add the script directory to sys.path
SCRIPT_DIR = Path(__file__).resolve().parents[2] / "plugins" / "claude-coding" / "skills" / "get-pr-comments" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
