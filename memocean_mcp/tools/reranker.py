"""
reranker.py — Embedding-based reranker for closet_search results.

Architecture: FTS5 BM25 provides recall (top-N candidates) → embedding cosine
similarity provides precision (rerank to top-K).

Uses fastembed (ONNX) with paraphrase-multilingual-MiniLM-L12-v2 for
Chinese/English multilingual embeddings, and sqlite-vec for persistent
vector storage in memory.db.

Graceful degradation: if fastembed or sqlite-vec unavailable, returns
candidates unchanged.
"""
import logging
import struct
import time
from typing import Optional

import numpy as np

logger = logging.getLogger("memocean_mcp.reranker")

# Lazy-loaded singletons
_embed_model = None
_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_EMBED_DIM = 384
_VEC_TABLE = "closet_vec"

# Track availability
_fastembed_available: Optional[bool] = None
_sqlite_vec_available: Optional[bool] = None


def _get_embed_model():
    """Lazy-load the embedding model (first call takes ~2s)."""
    global _embed_model, _fastembed_available
    if _embed_model is not None:
        return _embed_model
    try:
        from fastembed import TextEmbedding
        _embed_model = TextEmbedding(_MODEL_NAME)
        _fastembed_available = True
        logger.info("reranker: embedding model loaded")
        return _embed_model
    except Exception as e:
        _fastembed_available = False
        logger.warning("reranker: fastembed unavailable: %s", e)
        return None


def _load_sqlite_vec(conn):
    """Load sqlite-vec extension into a connection. Returns True on success."""
    global _sqlite_vec_available
    if _sqlite_vec_available is False:
        return False
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        _sqlite_vec_available = True
        return True
    except Exception as e:
        _sqlite_vec_available = False
        logger.warning("reranker: sqlite-vec unavailable: %s", e)
        return False


def _ensure_vec_table(conn):
    """Create the closet_vec virtual table if it doesn't exist."""
    try:
        conn.execute(f"SELECT count(*) FROM {_VEC_TABLE}").fetchone()
    except Exception:
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_VEC_TABLE} "
            f"USING vec0(slug TEXT PRIMARY KEY, embedding float[{_EMBED_DIM}])"
        )
        conn.commit()


def _float_vec_to_blob(vec) -> bytes:
    """Convert a numpy array or list of floats to a binary blob for sqlite-vec."""
    arr = np.asarray(vec, dtype=np.float32)
    return struct.pack(f"{len(arr)}f", *arr)


def _embed_texts(texts: list[str]) -> Optional[list[np.ndarray]]:
    """Embed a list of texts. Returns None if model unavailable."""
    model = _get_embed_model()
    if model is None:
        return None
    try:
        return list(model.embed(texts))
    except Exception as e:
        logger.warning("reranker: embedding failed: %s", e)
        return None


def embed_and_store(conn, slug: str, text: str) -> bool:
    """Embed a single closet entry and store in closet_vec. Returns success."""
    if not _load_sqlite_vec(conn):
        return False
    _ensure_vec_table(conn)

    embeddings = _embed_texts([text])
    if embeddings is None:
        return False

    blob = _float_vec_to_blob(embeddings[0])
    conn.execute(
        f"INSERT OR REPLACE INTO {_VEC_TABLE}(slug, embedding) VALUES (?, ?)",
        (slug, blob),
    )
    conn.commit()
    return True


def embed_and_store_batch(conn, items: list[tuple[str, str]], batch_size: int = 32) -> int:
    """Embed and store a batch of (slug, text) pairs. Returns count stored."""
    if not _load_sqlite_vec(conn):
        return 0
    _ensure_vec_table(conn)

    stored = 0
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        slugs = [s for s, _ in batch]
        texts = [t for _, t in batch]

        embeddings = _embed_texts(texts)
        if embeddings is None:
            break

        for slug, emb in zip(slugs, embeddings):
            blob = _float_vec_to_blob(emb)
            conn.execute(
                f"INSERT OR REPLACE INTO {_VEC_TABLE}(slug, embedding) VALUES (?, ?)",
                (slug, blob),
            )
        conn.commit()
        stored += len(batch)
        logger.info("reranker: backfill progress %d/%d", stored, len(items))

    return stored


def rerank(query: str, candidates: list[dict], top_k: int = 10) -> list[dict]:
    """
    Rerank FTS5 candidates by embedding similarity to the query.

    Strategy:
    1. Try sqlite-vec KNN lookup (if embeddings pre-computed in closet_vec)
    2. Fall back to in-memory cosine similarity
    3. If embedding unavailable, return candidates unchanged (graceful degradation)

    Args:
        query: The search query string
        candidates: List of dicts from FTS5 search (must have 'slug' and 'clsc' keys)
        top_k: Number of results to return after reranking

    Returns:
        Reranked list of dicts, truncated to top_k
    """
    if not candidates or len(candidates) <= 1:
        return candidates[:top_k]

    t0 = time.monotonic()

    # Try pre-computed vec lookup first
    result = _rerank_via_vec(query, candidates, top_k)
    if result is not None:
        elapsed = time.monotonic() - t0
        logger.info("reranker: vec rerank %d→%d in %.3fs", len(candidates), len(result), elapsed)
        return result

    # Fall back to in-memory cosine
    result = _rerank_in_memory(query, candidates, top_k)
    if result is not None:
        elapsed = time.monotonic() - t0
        logger.info("reranker: memory rerank %d→%d in %.3fs", len(candidates), len(result), elapsed)
        return result

    # Graceful degradation
    logger.info("reranker: unavailable, returning candidates unchanged")
    return candidates[:top_k]


def _rerank_via_vec(query: str, candidates: list[dict], top_k: int) -> Optional[list[dict]]:
    """Rerank using pre-computed embeddings in closet_vec via sqlite-vec KNN."""
    from ..config import FTS_DB

    try:
        import sqlite3
        conn = sqlite3.connect(str(FTS_DB))
        conn.row_factory = sqlite3.Row

        if not _load_sqlite_vec(conn):
            conn.close()
            return None

        # Check if vec table exists and has data
        try:
            count = conn.execute(f"SELECT count(*) FROM {_VEC_TABLE}").fetchone()[0]
        except Exception:
            conn.close()
            return None

        if count == 0:
            conn.close()
            return None

        # Embed the query
        q_emb = _embed_texts([query])
        if q_emb is None:
            conn.close()
            return None

        q_blob = _float_vec_to_blob(q_emb[0])

        # Get candidate slugs for filtering
        candidate_slugs = {c["slug"] for c in candidates}

        # KNN search over all stored vectors, then filter to candidates
        # We ask for more than top_k since some may not be in candidates
        rows = conn.execute(
            f"SELECT slug, distance FROM {_VEC_TABLE} "
            f"WHERE embedding MATCH ? AND k = ?",
            (q_blob, min(count, len(candidates) * 2)),
        ).fetchall()
        conn.close()

        # Build slug→distance map, filtered to our candidates
        slug_dist = {}
        for row in rows:
            slug = row[0] if isinstance(row, tuple) else row["slug"]
            dist = row[1] if isinstance(row, tuple) else row["distance"]
            if slug in candidate_slugs:
                slug_dist[slug] = dist

        # If we found less than half the candidates in vec, fall back
        if len(slug_dist) < len(candidates) // 2:
            return None

        # Sort candidates by distance (lower = more similar)
        # Candidates not in vec get worst distance
        max_dist = max(slug_dist.values(), default=999) + 1
        ranked = sorted(candidates, key=lambda c: slug_dist.get(c["slug"], max_dist))
        return ranked[:top_k]

    except Exception as e:
        logger.warning("reranker: vec rerank failed: %s", e)
        return None


def _rerank_in_memory(query: str, candidates: list[dict], top_k: int) -> Optional[list[dict]]:
    """Rerank by computing embeddings on-the-fly and using cosine similarity."""
    # Embed query + all candidate texts together
    texts = [query] + [c.get("clsc", c.get("slug", "")) for c in candidates]
    embeddings = _embed_texts(texts)
    if embeddings is None:
        return None

    q_emb = embeddings[0]
    q_norm = np.linalg.norm(q_emb)
    if q_norm == 0:
        return candidates[:top_k]

    # Compute cosine similarities
    scored = []
    for i, cand in enumerate(candidates):
        c_emb = embeddings[i + 1]
        c_norm = np.linalg.norm(c_emb)
        if c_norm == 0:
            sim = 0.0
        else:
            sim = float(np.dot(q_emb, c_emb) / (q_norm * c_norm))
        scored.append((sim, cand))

    # Sort by similarity descending
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]


def is_available() -> bool:
    """Check if the reranker can function (model loadable)."""
    global _fastembed_available
    if _fastembed_available is not None:
        return _fastembed_available
    return _get_embed_model() is not None
