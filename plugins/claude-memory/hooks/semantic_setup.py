#!/usr/bin/env python3
"""Check for or turn on semantic search, which needs Ollama and an embedding model.

Called by Claude during onboarding after the user agrees to semantic search.
  --check   print JSON: whether Ollama is installed and running, whether the model
            is present, and this OS's install command
  --enable  pull the model, embed a test sentence, then save semantic_search=true
            and stamp onboarding as done. Exits 1 with a reason if a step fails.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import load_settings
from memory_lib.embeddings import EmbeddingError, embed

WRITE_CONFIG_PATH = SCRIPT_DIR / "write_config.py"


def install_command() -> str:
    """The one command that installs Ollama as a service that starts at login."""
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return "brew install ollama && brew services start ollama"
        return "download the app from https://ollama.com/download/mac and open it once"
    if system == "Windows":
        return "winget install --id Ollama.Ollama -e"
    return "curl -fsSL https://ollama.com/install.sh | sh"


def ollama_models(url: str) -> list[str] | None:
    """Model names the Ollama server holds, or None when it is not reachable."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as resp:
            return [m.get("name", "") for m in json.loads(resp.read().decode("utf-8")).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return None


def check(settings: dict) -> dict:
    models = ollama_models(settings["ollama_url"])
    return {
        "ollama_installed": shutil.which("ollama") is not None,
        "ollama_running": models is not None,
        "model": settings["embedding_model"],
        "model_present": models is not None and settings["embedding_model"] in models,
        "install_command": install_command(),
    }


def enable(settings: dict) -> str | None:
    """Pull the model and prove it embeds; returns an error message, or None on success."""
    status = check(settings)
    if not status["ollama_running"]:
        if not status["ollama_installed"]:
            return f"Ollama is not installed. Install it with: {status['install_command']}"
        return "Ollama is installed but not running. Start it (open the app, or `ollama serve`) and retry."
    model = settings["embedding_model"]
    if not status["model_present"] and subprocess.run(["ollama", "pull", model]).returncode != 0:
        return f"`ollama pull {model}` failed."
    try:
        embed(["semantic search check"], model, settings["ollama_url"])
    except EmbeddingError as e:
        return f"The model did not embed a test sentence: {e}"
    subprocess.run([sys.executable, str(WRITE_CONFIG_PATH), "--semantic-search", "true"], check=True)
    return None


def main():
    parser = argparse.ArgumentParser(description="Set up semantic search for claude-memory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report Ollama and model status as JSON")
    mode.add_argument("--enable", action="store_true", help="Pull the model and turn semantic search on")
    args = parser.parse_args()

    settings = load_settings()
    if args.check:
        print(json.dumps(check(settings), indent=2))
        return
    error = enable(settings)
    if error:
        print(error, file=sys.stderr)
        sys.exit(1)
    print("Semantic search is on. The index builds in the background from the next session start.")


if __name__ == "__main__":
    main()
