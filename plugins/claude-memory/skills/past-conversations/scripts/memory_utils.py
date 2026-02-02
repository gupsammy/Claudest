#!/usr/bin/env python3
"""
Shared utilities for claude-memory plugin.

Consolidates common code used across memory scripts:
- Path constants and settings
- Database connection and schema
- Time formatting
- Logging setup
"""

import json
import logging
import re
import sqlite3
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Default paths
DEFAULT_DB_PATH = Path.home() / ".claude-memory" / "conversations.db"
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_SETTINGS_PATH = Path.home() / ".claude-memory" / "settings.local.md"
DEFAULT_LOG_PATH = Path.home() / ".claude-memory" / "memory.log"

# Default settings
DEFAULT_SETTINGS = {
    "db_path": str(DEFAULT_DB_PATH),
    "auto_inject_context": True,
    "max_context_sessions": 2,
    "exclude_projects": [],
    "context_truncation_limit": 2000,
    "logging_enabled": False,
    "sync_on_stop": True,
}

# Database schema
SCHEMA = """
-- Projects table (derived from directory structure)
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  key TEXT UNIQUE NOT NULL,
  name TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_projects_key ON projects(key);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  project_id INTEGER REFERENCES projects(id),
  parent_session_id INTEGER REFERENCES sessions(id),
  started_at DATETIME,
  ended_at DATETIME,
  git_branch TEXT,
  cwd TEXT,
  message_count INTEGER DEFAULT 0,
  exchange_count INTEGER DEFAULT 0,
  files_modified TEXT,
  commits TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_exchange ON sessions(exchange_count);

-- Messages table (core content)
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  uuid TEXT,
  parent_uuid TEXT,
  timestamp DATETIME,
  role TEXT CHECK(role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  has_tool_use INTEGER DEFAULT 0,
  has_thinking INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);

-- FTS5 full-text search (auto-synced via triggers)
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  content=messages,
  content_rowid=id,
  tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

-- Import tracking
CREATE TABLE IF NOT EXISTS import_log (
  id INTEGER PRIMARY KEY,
  file_path TEXT UNIQUE NOT NULL,
  file_hash TEXT,
  imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  messages_imported INTEGER DEFAULT 0
);

-- Views
CREATE VIEW IF NOT EXISTS search_results AS
SELECT m.id, m.timestamp, m.role, m.content, s.uuid as session_uuid, p.name as project_name, p.path as project_path
FROM messages m JOIN sessions s ON m.session_id = s.id JOIN projects p ON s.project_id = p.id;

CREATE VIEW IF NOT EXISTS recent_conversations AS
SELECT s.uuid as session_uuid, p.name as project, s.started_at, s.ended_at,
       s.message_count, s.exchange_count, s.files_modified, s.commits, s.git_branch
FROM sessions s JOIN projects p ON s.project_id = p.id ORDER BY s.started_at DESC;
"""


def load_settings(settings_path: Optional[Path] = None) -> dict:
    """
    Load settings from YAML frontmatter in settings file.
    Returns default settings if file doesn't exist or parsing fails.
    """
    path = settings_path or DEFAULT_SETTINGS_PATH
    settings = DEFAULT_SETTINGS.copy()

    if not path.exists():
        return settings

    if not HAS_YAML:
        return settings

    try:
        content = path.read_text()
        # Parse YAML frontmatter (between --- markers)
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1])  # type: ignore[possibly-undefined]
                if isinstance(frontmatter, dict):
                    settings.update(frontmatter)
    except Exception:
        pass

    return settings


def get_db_path(settings: Optional[dict] = None) -> Path:
    """Get database path from settings or default."""
    if settings and "db_path" in settings:
        return Path(settings["db_path"]).expanduser()
    return DEFAULT_DB_PATH


def get_db_connection(settings: Optional[dict] = None) -> sqlite3.Connection:
    """
    Get database connection, initializing schema if needed.
    Uses settings-based path if provided.
    """
    db_path = get_db_path(settings)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def setup_logging(settings: Optional[dict] = None) -> logging.Logger:
    """
    Set up logging with rotation.
    Returns a null logger if logging is disabled.
    """
    logger = logging.getLogger("claude-memory")
    logger.handlers = []  # Clear existing handlers

    if not settings or not settings.get("logging_enabled", False):
        logger.addHandler(logging.NullHandler())
        return logger

    log_path = DEFAULT_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,  # 1MB
        backupCount=2
    )
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    return logger


def format_time(ts_str: Optional[str], fmt: str = "%H:%M") -> str:
    """
    Format ISO timestamp to specified format.
    Default: HH:MM
    """
    if not ts_str:
        return "??:??"
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.strftime(fmt)
    except Exception:
        return ts_str[:16] if ts_str else "??:??"


def format_time_full(ts_str: Optional[str]) -> str:
    """Format ISO timestamp to YYYY-MM-DD HH:MM."""
    return format_time(ts_str, "%Y-%m-%d %H:%M")


def get_project_key(cwd: str) -> str:
    """Convert working directory to project key format."""
    return cwd.replace("/", "-").replace(".", "-")


def parse_project_key(key: str) -> str:
    """Convert directory key back to original path."""
    return "/" + key.replace("-", "/").lstrip("/")


def extract_project_name(path: str) -> str:
    """Extract short project name from path."""
    return Path(path).name


def format_markdown_session(session: dict, verbose: bool = False) -> str:
    """Format a single session as markdown."""
    lines = []

    started = format_time_full(session.get("started_at"))
    project = session.get("project", "Unknown")
    lines.append(f"## {project} | {started}")
    lines.append(f"Session: {session.get('uuid', 'unknown')[:8]}")

    if session.get("git_branch"):
        lines.append(f"Branch: {session['git_branch']}")

    if verbose:
        files = session.get("files_modified", [])
        if files:
            lines.append("\n### Files Modified")
            for f in files[-10:]:
                lines.append(f"- `{f}`")
            if len(files) > 10:
                lines.append(f"- ...and {len(files) - 10} more")

        commits = session.get("commits", [])
        if commits:
            lines.append("\n### Commits")
            for c in commits:
                lines.append(f"- {c}")

    lines.append("\n### Conversation\n")

    for msg in session.get("messages", []):
        role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"**{role}:** {msg['content']}\n")

    lines.append("---\n")
    return "\n".join(lines)


def format_json_sessions(sessions: list[dict], extra: Optional[dict] = None) -> str:
    """Format sessions as JSON with metadata."""
    total_messages = sum(len(s.get("messages", [])) for s in sessions)
    output = {
        "sessions": sessions,
        "total_sessions": len(sessions),
        "total_messages": total_messages
    }
    if extra:
        output.update(extra)
    return json.dumps(output, indent=2)


# Content extraction utilities (from import_conversations.py)

def extract_text_content(content) -> tuple[str, bool, bool]:
    """
    Extract text from message content.
    Returns: (text, has_tool_use, has_thinking)
    """
    has_tool_use = False
    has_thinking = False

    if isinstance(content, str):
        # Clean up command artifacts
        text = re.sub(r'<command-name>.*?</command-name>', '', content, flags=re.DOTALL)
        text = re.sub(r'<command-message>.*?</command-message>', '', text, flags=re.DOTALL)
        text = re.sub(r'<command-args>.*?</command-args>', '', text, flags=re.DOTALL)
        text = re.sub(r'<local-command-stdout>.*?</local-command-stdout>', '', text, flags=re.DOTALL)
        return text.strip(), False, False

    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type", "")
                if item_type == "text":
                    texts.append(item.get("text", ""))
                elif item_type == "tool_use":
                    has_tool_use = True
                    tool_name = item.get("name", "")
                    if tool_name:
                        texts.append(f"[Tool: {tool_name}]")
                elif item_type == "thinking":
                    has_thinking = True
        return "\n".join(texts).strip(), has_tool_use, has_thinking

    return "", False, False


def is_tool_result(content) -> bool:
    """Check if content is a tool result (not a real user message)."""
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "tool_result":
            return True
    return False


def extract_files_modified(content) -> list[str]:
    """Extract file paths from Edit/Write/MultiEdit tool uses."""
    files = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                name = item.get("name", "")
                inp = item.get("input", {})
                if name in ("Edit", "Write", "MultiEdit") and "file_path" in inp:
                    files.append(inp["file_path"])
    return files


def extract_commits(content) -> list[str]:
    """Extract git commit messages from Bash tool uses."""
    commits = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                if item.get("name") == "Bash":
                    cmd = item.get("input", {}).get("command", "")
                    if "git commit" in cmd:
                        m = re.search(r'-m\s+["\']([^"\']+)["\']', cmd)
                        if m:
                            commits.append(m.group(1)[:100])
    return commits
