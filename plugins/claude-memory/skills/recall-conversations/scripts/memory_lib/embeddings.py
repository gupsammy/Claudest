"""
Opt-in semantic search: embed conversation exchanges with a local Ollama server.

The unit is an exchange — one human turn plus the start of the reply — because
that is what a person recalls. Vectors are truncated to DIMS (Matryoshka) and
re-normalized, so a dot product is cosine similarity. Stdlib only.
"""

from __future__ import annotations

import json
import math
import operator
import sqlite3
import urllib.error
import urllib.request
from array import array
from typing import Optional

from memory_lib.cli_common import ScopeFilter
from memory_lib.content import strip_system_reminders

DIMS = 256
USER_CHARS = 1200
ASSISTANT_CHARS = 800
# Prompt formats from the EmbeddingGemma model card.
QUERY_PROMPT = "task: search result | query: {}"
DOC_PROMPT = "title: none | text: {}"


class EmbeddingError(Exception):
    """Ollama unreachable or returned something unusable."""


def embed(texts: list[str], model: str, url: str, timeout: float = 60.0) -> list[list[float]]:
    """Embed texts in one /api/embed call; returns DIMS-long unit vectors."""
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/embed", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            vectors = json.loads(resp.read().decode("utf-8"))["embeddings"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as e:
        raise EmbeddingError(str(e)) from e
    if len(vectors) != len(texts):
        raise EmbeddingError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
    return [_unit(v[:DIMS]) for v in vectors]


def _unit(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def pack(v: list[float]) -> bytes:
    return array("f", v).tobytes()


def unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


def exchange_text(user: str, assistant: str) -> str:
    """The text embedded for one exchange; '' when the human turn has no typed text.

    Interrupt markers and tag-wrapped turns (shell output, delegations, command
    caveats) are not something a person typed, and embedding them makes every
    session that holds one look alike.
    """
    user = strip_system_reminders(user or "").strip()[:USER_CHARS]
    if not user or user.startswith(("[Request interrupted", "<")):
        return ""
    assistant = (assistant or "").strip()[:ASSISTANT_CHARS]
    return f"{user}\n\n{assistant}" if assistant else user


_PENDING_SQL = """
    SELECT m.id, m.session_id, m.timestamp, m.content
    FROM messages m
    LEFT JOIN exchange_embeddings e ON e.message_id = m.id AND e.model = ?
    WHERE m.role = 'user' AND COALESCE(m.is_notification, 0) = 0 AND e.message_id IS NULL
"""


def has_pending(conn: sqlite3.Connection, model: str) -> bool:
    return conn.execute(_PENDING_SQL + " LIMIT 1", (model,)).fetchone() is not None


def pending_exchanges(conn: sqlite3.Connection, model: str, limit: int) -> list[tuple[int, str]]:
    """Human turns without a vector for `model`, newest first, as (message_id, text)."""
    cursor = conn.cursor()
    rows = cursor.execute(_PENDING_SQL + " ORDER BY m.id DESC LIMIT ?", (model, limit)).fetchall()
    out = []
    for message_id, session_id, timestamp, content in rows:
        # The reply is every assistant message up to the next human turn.
        following = cursor.execute("""
            SELECT role, content, COALESCE(is_notification, 0) FROM messages
            WHERE session_id = ? AND timestamp > ? ORDER BY timestamp LIMIT 8
        """, (session_id, timestamp)).fetchall()
        reply = []
        for role, text, is_notification in following:
            if role == "user" and not is_notification:
                break
            if role == "assistant" and text:
                reply.append(text)
        out.append((message_id, exchange_text(content, "\n".join(reply))))
    return out


def store(conn: sqlite3.Connection, model: str, items: list[tuple[int, Optional[list[float]]]]) -> None:
    """Save vectors; None stores an empty marker so the turn is not retried."""
    conn.executemany(
        "INSERT OR REPLACE INTO exchange_embeddings (message_id, model, vec) VALUES (?, ?, ?)",
        [(mid, model, pack(v) if v else b"") for mid, v in items],
    )


def index_size(conn: sqlite3.Connection, model: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM exchange_embeddings WHERE model = ? AND length(vec) > 0", (model,)
    ).fetchone()[0]


def vector_hits(
    conn: sqlite3.Connection,
    query_vec: list[float],
    model: str,
    scope: Optional[ScopeFilter],
    include_notifications: bool,
    limit: int,
    min_score: float = -1.0,
) -> list[tuple[int, float, int]]:
    """Rank active branches by their closest exchange to the query.

    Returns up to `limit` (branch_id, similarity, message_id) tuples, best first;
    message_id is the exchange that matched. Branches whose best exchange scores
    below min_score are dropped, so a query with no real match returns nothing.
    """
    sql = """
        SELECT bm.branch_id, e.message_id, e.vec
        FROM exchange_embeddings e
        JOIN messages m ON m.id = e.message_id
        JOIN branch_messages bm ON bm.message_id = m.id
        JOIN branches b ON b.id = bm.branch_id AND b.is_active = 1
        JOIN sessions s ON b.session_id = s.id
        JOIN projects p ON s.project_id = p.id
        WHERE e.model = ? AND length(e.vec) > 0
    """
    params: list = [model]
    if not include_notifications:
        sql += " AND COALESCE(m.is_notification, 0) = 0"
    if scope and scope.values:
        sql += f" AND p.{scope.column} IN ({','.join('?' * len(scope.values))})"
        params.extend(scope.values)

    best: dict[int, tuple[float, int]] = {}
    for branch_id, message_id, blob in conn.execute(sql, params):
        score = sum(map(operator.mul, query_vec, unpack(blob)))
        if branch_id not in best or score > best[branch_id][0]:
            best[branch_id] = (score, message_id)
    ranked = sorted(((bid, hit) for bid, hit in best.items() if hit[0] >= min_score),
                    key=lambda kv: kv[1][0], reverse=True)[:limit]
    return [(bid, score, mid) for bid, (score, mid) in ranked]


RRF_K = 60


def fuse(lexical: list[int], semantic: list[int], limit: int) -> list[int]:
    """Reciprocal rank fusion of two best-first branch id lists."""
    scores: dict[int, float] = {}
    for ranking in (lexical, semantic):
        for rank, bid in enumerate(ranking):
            scores[bid] = scores.get(bid, 0.0) + 1.0 / (RRF_K + rank + 1)
    # Ties keep lexical order first, then semantic order.
    order = {bid: i for i, bid in enumerate(dict.fromkeys(lexical + semantic))}
    return sorted(scores, key=lambda b: (-scores[b], order[b]))[:limit]


# Centered cosine floor for a related session, calibrated for embeddinggemma-2 at
# 256 dims: about half of sessions have a neighbour above it.
RELATED_MIN_SCORE = 0.55
# A one-exchange session is too thin to stand for a topic.
RELATED_MIN_EXCHANGES = 2


def refresh_session_vectors(conn: sqlite3.Connection, model: str) -> int:
    """Recompute the mean vector of every session whose exchange count changed.

    Returns how many sessions were refreshed. A row is current iff its n equals the
    session's count of non-empty exchange vectors, so re-running is always safe.
    """
    stale = conn.execute("""
        SELECT c.session_id FROM (
            SELECT m.session_id, COUNT(*) AS n
            FROM exchange_embeddings e JOIN messages m ON m.id = e.message_id
            WHERE e.model = ? AND length(e.vec) > 0
            GROUP BY m.session_id
        ) c
        LEFT JOIN session_embeddings se ON se.session_id = c.session_id AND se.model = ?
        WHERE se.n IS NULL OR se.n != c.n
    """, (model, model)).fetchall()
    for (session_id,) in stale:
        total: list[float] = []
        n = 0
        for (blob,) in conn.execute("""
            SELECT e.vec FROM exchange_embeddings e JOIN messages m ON m.id = e.message_id
            WHERE m.session_id = ? AND e.model = ? AND length(e.vec) > 0
        """, (session_id, model)):
            v = unpack(blob)
            total = list(map(operator.add, total, v)) if total else list(v)
            n += 1
        conn.execute(
            "INSERT OR REPLACE INTO session_embeddings (session_id, model, vec, n) VALUES (?, ?, ?, ?)",
            (session_id, model, pack([x / n for x in total]), n),
        )
    return len(stale)


def related_sessions(
    conn: sqlite3.Connection,
    model: str,
    session_id: int,
    limit: int = 3,
    min_score: float = RELATED_MIN_SCORE,
    exclude: tuple = (),
) -> list[tuple[int, float]]:
    """Top-level sessions closest in meaning to session_id, as (session_id, score).

    Every session vector shares one direction ("a coding conversation") that puts
    any two of them near 0.9; subtracting the corpus mean before comparing removes
    it, so the score reflects topic.
    """
    rows = conn.execute("""
        SELECT se.session_id, se.vec, se.n FROM session_embeddings se
        JOIN sessions s ON s.id = se.session_id
        WHERE se.model = ? AND se.n >= ? AND s.parent_session_id IS NULL
    """, (model, RELATED_MIN_EXCHANGES)).fetchall()
    vecs = {sid: (unpack(blob), n) for sid, blob, n in rows}
    if session_id not in vecs or len(vecs) < 2:
        return []
    total_n = sum(n for _, n in vecs.values())
    mean = [0.0] * len(vecs[session_id][0])
    for v, n in vecs.values():
        mean = list(map(operator.add, mean, (x * n / total_n for x in v)))

    def centered(v) -> list[float]:
        return _unit(list(map(operator.sub, v, mean)))

    target = centered(vecs[session_id][0])
    skip = set(exclude) | {session_id}
    scored = [(sid, sum(map(operator.mul, target, centered(v))))
              for sid, (v, _) in vecs.items() if sid not in skip]
    scored = [hit for hit in scored if hit[1] >= min_score]
    return sorted(scored, key=lambda hit: hit[1], reverse=True)[:limit]
