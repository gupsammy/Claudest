#!/usr/bin/env python3
"""
Backfill context summaries for existing branches.

Runs as a background process spawned by memory-setup.py on SessionStart.
Processes branches in batches, commits between batches, and marks errors
with summary_version = -1 to avoid infinite retry.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "skills" / "recall-conversations" / "scripts"))

from memory_lib.db import get_db_connection, load_settings, setup_logging
from memory_lib.summarizer import compute_context_summary

BATCH_SIZE = 50


def main() -> None:
    settings = load_settings()
    logger = setup_logging(settings)

    try:
        conn = get_db_connection(settings)
    except Exception as e:
        logger.error(f"Backfill: failed to connect to DB: {e}")
        return

    cursor = conn.cursor()
    total_updated = 0

    while True:
        cursor.execute("""
            SELECT id FROM branches
            WHERE summary_version IS NULL OR summary_version < 2
            LIMIT ?
        """, (BATCH_SIZE,))
        rows = cursor.fetchall()

        if not rows:
            break

        for (branch_id,) in rows:
            try:
                summary_md, summary_json = compute_context_summary(cursor, branch_id)
                cursor.execute("""
                    UPDATE branches SET context_summary = ?, context_summary_json = ?, summary_version = 2
                    WHERE id = ?
                """, (summary_md, summary_json, branch_id))
                total_updated += 1
            except Exception as e:
                # Mark as errored to avoid infinite retry
                cursor.execute(
                    "UPDATE branches SET summary_version = -1 WHERE id = ?",
                    (branch_id,)
                )
                logger.error(f"Backfill: branch {branch_id} failed: {e}")

        conn.commit()

    conn.close()
    logger.info(f"Backfill complete: {total_updated} branches summarized")


if __name__ == "__main__":
    main()
