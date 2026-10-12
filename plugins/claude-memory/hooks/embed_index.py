#!/usr/bin/env python3
"""
Embed un-indexed conversation exchanges for opt-in semantic search.

Runs in the background: spawned by memory-setup.py on SessionStart to catch up
on any backlog, and called by sync_current.py after each Stop-hook sync so a new
exchange is searchable by meaning as soon as its turn ends. Embeds newest first
in batches, committing each, so search improves while it runs. Exits quietly when
Ollama is unreachable; the next sync or SessionStart retries.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import (
    DEFAULT_DB_PATH,
    acquire_lock,
    get_db_connection,
    load_settings,
    release_lock,
    setup_logging,
)
from memory_lib.embeddings import DOC_PROMPT, EmbeddingError, embed, pending_exchanges, store

BATCH_SIZE = 32
EMBED_LOCK_PATH = DEFAULT_DB_PATH.parent / "embed.lock"


def index_pending(conn, model: str, url: str) -> int:
    """Embed every pending exchange; returns how many got a vector."""
    embedded = 0
    while True:
        batch = pending_exchanges(conn, model, BATCH_SIZE)
        if not batch:
            return embedded
        texts = [(mid, text) for mid, text in batch if text]
        vectors = embed([DOC_PROMPT.format(t) for _, t in texts], model, url) if texts else []
        found = dict(zip((mid for mid, _ in texts), vectors))
        store(conn, model, [(mid, found.get(mid)) for mid, _ in batch])
        conn.commit()
        embedded += len(vectors)


def run(settings: dict, logger) -> int:
    """Embed everything pending when semantic search is on; returns how many got a vector.

    Returns 0 without waiting when another indexer holds the lock: it loops until
    nothing is pending, and whatever it misses the next sync picks up.
    """
    if not settings.get("semantic_search"):
        return 0
    if acquire_lock(EMBED_LOCK_PATH) is None:
        return 0
    try:
        conn = get_db_connection(settings)
        try:
            count = index_pending(conn, settings["embedding_model"], settings["ollama_url"])
            logger.info(f"Embedding index: {count} exchanges embedded")
            return count
        except EmbeddingError as e:
            logger.warning(f"Embedding index stopped, Ollama unavailable: {e}")
            return 0
        finally:
            conn.close()
    finally:
        release_lock(EMBED_LOCK_PATH)


def main():
    settings = load_settings()
    run(settings, setup_logging(settings))


if __name__ == "__main__":
    main()
