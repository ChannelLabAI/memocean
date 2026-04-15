"""
messages_hybrid_search.py — Hybrid BM25 + BGE-m3 KNN + RRF search over TG messages.

Pipeline:
  query → BM25 FTS5 top-50  ─┐
                              ├── RRF(k=60) → top-N
  query → BGE-m3 KNN top-50 ─┘

Fallback: KNN_ENABLED=false (or model unavailable) → pure BM25.
Reuses embed_texts and _rrf_merge from radar_search to avoid duplication.
"""
import logging
import os
import sqlite3
from typing import Optional

from ..config import FTS_DB

logger = logging.getLogger("memocean_mcp.messages_hybrid_search")


# ── BM25 path ──────────────────────────────────────────────────────────────

def _bm25_search(query: str, limit: int, bot: Optional[str]) -> list[dict]:
    """Run FTS5 BM25 search, return normalised dicts with slug field added."""
    from .fts_search import fts_search
    try:
        results = fts_search(query, limit=limit, bot=bot)
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.debug("_bm25_search failed: %s", e)
        return []
    for r in results:
        r["slug"] = f"{r['chat_id']}:{r['message_id']}"
    return results


# ── KNN path ───────────────────────────────────────────────────────────────

def _knn_search(query: str, limit: int, bot: Optional[str]) -> list[dict]:
    """
    Run BGE-m3 KNN over messages_vec, enrich with messages metadata.
    Returns normalised dicts (same schema as BM25 results, plus slug).
    """
    from .radar_search import _search_messages_semantic, _check_messages_vec_populated

    if not _check_messages_vec_populated():
        return []

    knn_raw = _search_messages_semantic(query, limit)
    if not knn_raw:
        return []

    msg_keys = [r["slug"] for r in knn_raw]
    knn_order = {mk: idx for idx, mk in enumerate(msg_keys)}

    try:
        conn = sqlite3.connect(str(FTS_DB))
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in msg_keys)
        rows = conn.execute(
            f"SELECT bot_name, ts, source, chat_id, user, message_id, text "
            f"FROM messages "
            f"WHERE (chat_id || ':' || message_id) IN ({placeholders})",
            msg_keys,
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.debug("_knn_search: metadata fetch failed: %s", e)
        return []

    # Build enriched results in KNN order
    meta: dict[str, dict] = {}
    for row in rows:
        chat_id = row["chat_id"]
        message_id = row["message_id"]
        mk = f"{chat_id}:{message_id}"
        meta[mk] = {
            "bot_name": row["bot_name"],
            "ts": row["ts"],
            "source": row["source"] or "",
            "chat_id": chat_id,
            "user": row["user"] or "",
            "message_id": message_id,
            "text": row["text"] or "",
        }

    enriched = []
    for mk in msg_keys:
        m = meta.get(mk)
        if not m:
            continue
        # Apply bot filter if requested
        if bot and m["bot_name"] != bot:
            continue
        enriched.append({
            "slug": mk,
            "bot_name": m["bot_name"],
            "ts": m["ts"],
            "source": m["source"],
            "chat_id": m["chat_id"],
            "user": m["user"],
            "message_id": m["message_id"],
            "snippet": m["text"][:200],
            "rank": 0.0,
            "retrieval": "knn",
        })

    return enriched


# ── RRF wrapper ────────────────────────────────────────────────────────────

def _rrf(lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Thin wrapper around radar_search._rrf_merge."""
    from .radar_search import _rrf_merge
    return _rrf_merge(lists, k=k)


# ── Public entry point ─────────────────────────────────────────────────────

def messages_hybrid_search(
    query: str,
    limit: int = 10,
    bot: Optional[str] = None,
) -> list[dict]:
    """
    Hybrid search over TG message history.

    BM25 always runs. BGE-m3 KNN runs when:
      - KNN_ENABLED != 'false'/'0'/'no' (env var)
      - reranker.is_available() returns True

    Results are RRF-merged (k=60) and top-N returned.
    Fallback to pure BM25 on any KNN failure.
    """
    if not query or not query.strip():
        return []

    knn_flag = os.environ.get("KNN_ENABLED", "false").lower()
    knn_enabled = knn_flag not in ("false", "0", "no")

    if knn_enabled:
        try:
            from .reranker import is_available as knn_available
            knn_enabled = knn_available()
        except Exception:
            knn_enabled = False

    # Keyword expansion: convert natural-language query → keyword OR query for BM25.
    # KNN keeps original query (natural language better for embedding).
    try:
        from .query_expand import query_expand, keywords_to_fts_or
        keywords = query_expand(query)
        bm25_query = keywords_to_fts_or(keywords) if len(keywords) > 1 else query
    except Exception:
        bm25_query = query

    # BM25 always runs
    bm25 = _bm25_search(bm25_query, 50, bot)

    if not knn_enabled:
        # Pure BM25 fallback — strip RRF slug before returning
        _strip_slug(bm25)
        return bm25[:limit]

    # KNN path
    try:
        knn = _knn_search(query, 50, bot)
    except Exception as e:
        logger.warning("messages_hybrid_search: KNN failed, falling back to BM25: %s", e)
        _strip_slug(bm25)
        return bm25[:limit]

    if not knn:
        _strip_slug(bm25)
        return bm25[:limit]

    # RRF merge
    try:
        merged = _rrf([bm25, knn], k=60)[:limit]
    except Exception as e:
        logger.warning("messages_hybrid_search: RRF merge failed, falling back to BM25: %s", e)
        _strip_slug(bm25)
        return bm25[:limit]

    _strip_slug(merged)
    return merged


def _strip_slug(results: list[dict]) -> None:
    """Remove internal slug field from results in-place."""
    for r in results:
        r.pop("slug", None)
