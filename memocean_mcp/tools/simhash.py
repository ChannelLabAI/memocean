"""
simhash.py — Multi-probe LSH SimHash for MemOcean Phase 2.

Architecture: 8 independent projection tables × 512 bits each.
- Storage: 512 bytes/vector (vs 4096 bytes float32) — 8x compression
- Recall: ~80.8% mean overlap@10 vs float32 KNN on BGE-m3 corpus
- Usage: coarse-filter top-50, then float32 cosine re-score → top-10

Projection matrices R0..R7 (1024×512, seeds 42..49) are generated
deterministically and cached as .npz at SHARED_ROOT/simhash_projections.npz.

DB table: radar_simhash8
  slug TEXT PRIMARY KEY,
  fp0..fp7 BLOB NOT NULL  (64 bytes each = 512 bits each table)

Phase 1 PoC: simhash_poc.py
Phase 2 integration: this module + reranker.py + radar_search.py
"""
import logging
import os
import sqlite3
import struct
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("memocean_mcp.simhash")

# Config
N_TABLES = 8
K_BITS = 512            # bits per table
K_BYTES = K_BITS // 8   # 64 bytes per table
EMBED_DIM = 1024        # BGE-m3 output dimension
SEEDS = list(range(42, 42 + N_TABLES))  # seeds 42..49

TABLE_NAME = "radar_simhash8"
COARSE_LIMIT = 50       # top-N candidates from SimHash coarse search

# Cached projection matrices
_projections: Optional[np.ndarray] = None  # shape (N_TABLES, EMBED_DIM, K_BITS)


def _get_projections_path() -> Path:
    """Return path to .npz file containing projection matrices."""
    try:
        from ..config import SHARED_ROOT
        return SHARED_ROOT / "simhash_projections.npz"
    except Exception:
        return Path.home() / ".memocean" / "shared" / "simhash_projections.npz"


def get_projections() -> np.ndarray:
    """
    Load or generate the 8 projection matrices.
    Returns shape (N_TABLES, EMBED_DIM, K_BITS) float32.
    Matrices are persisted to .npz for reproducibility.
    """
    global _projections
    if _projections is not None:
        return _projections

    npz_path = _get_projections_path()

    # Try loading from disk
    if npz_path.exists():
        try:
            data = np.load(str(npz_path))
            mats = np.stack([data[f"R{i}"] for i in range(N_TABLES)], axis=0)
            _projections = mats.astype(np.float32)
            logger.info("simhash: loaded projections from %s", npz_path)
            return _projections
        except Exception as e:
            logger.warning("simhash: failed to load projections from %s: %s", npz_path, e)

    # Generate deterministically
    logger.info("simhash: generating %d projection matrices (seeds %d..%d)",
                N_TABLES, SEEDS[0], SEEDS[-1])
    mats = []
    for seed in SEEDS:
        rng = np.random.RandomState(seed)
        R = rng.randn(EMBED_DIM, K_BITS).astype(np.float32)
        norms = np.linalg.norm(R, axis=0, keepdims=True)
        R /= norms
        mats.append(R)
    _projections = np.stack(mats, axis=0)  # (N_TABLES, EMBED_DIM, K_BITS)

    # Persist to disk
    try:
        npz_path.parent.mkdir(parents=True, exist_ok=True)
        save_dict = {f"R{i}": _projections[i] for i in range(N_TABLES)}
        np.savez(str(npz_path), **save_dict)
        logger.info("simhash: saved projections to %s", npz_path)
    except Exception as e:
        logger.warning("simhash: could not save projections: %s", e)

    return _projections


def compute_fingerprints(vecs: np.ndarray) -> np.ndarray:
    """
    Compute multi-probe SimHash fingerprints for a batch of vectors.

    Args:
        vecs: (N, EMBED_DIM) float32 normalized vectors

    Returns:
        fingerprints: (N, N_TABLES, K_BYTES) uint8 packed bit arrays
    """
    projs = get_projections()  # (N_TABLES, EMBED_DIM, K_BITS)
    N = vecs.shape[0]

    # projections for all tables: (N_TABLES, N, K_BITS)
    all_projs = np.tensordot(projs, vecs.T, axes=([1], [0]))  # (N_TABLES, K_BITS, N) — wait
    # Actually: projs[t] is (EMBED_DIM, K_BITS), vecs is (N, EMBED_DIM)
    # projections[t] = vecs @ projs[t] → (N, K_BITS)
    result = np.zeros((N, N_TABLES, K_BYTES), dtype=np.uint8)
    for t in range(N_TABLES):
        proj = vecs @ projs[t]  # (N, K_BITS)
        bits = (proj > 0).astype(np.uint8)  # (N, K_BITS)
        packed = np.packbits(bits, axis=1)  # (N, K_BYTES)
        result[:, t, :] = packed

    return result


def compute_fingerprints_single(vec: np.ndarray) -> np.ndarray:
    """
    Compute fingerprints for a single vector.

    Args:
        vec: (EMBED_DIM,) float32

    Returns:
        fingerprints: (N_TABLES, K_BYTES) uint8
    """
    return compute_fingerprints(vec[np.newaxis, :])[0]


def hamming_distance_batch(query_fp: np.ndarray, all_fps: np.ndarray) -> np.ndarray:
    """
    Compute per-table hamming distances and aggregate by min (multi-probe).

    Args:
        query_fp: (N_TABLES, K_BYTES) uint8
        all_fps: (N, N_TABLES, K_BYTES) uint8

    Returns:
        scores: (N,) float — min hamming distance across tables (lower = more similar)
    """
    # XOR: (N, N_TABLES, K_BYTES)
    xored = np.bitwise_xor(query_fp[np.newaxis, :, :], all_fps)
    # Popcount per table: (N, N_TABLES)
    bits = np.unpackbits(xored, axis=2)  # (N, N_TABLES, K_BITS)
    hamming = bits.sum(axis=2)  # (N, N_TABLES)
    # Multi-probe: take minimum hamming across all tables
    return hamming.min(axis=1).astype(np.float32)  # (N,)


def ensure_simhash8_table(conn: sqlite3.Connection) -> None:
    """Create radar_simhash8 table if it doesn't exist."""
    cols = ", ".join(f"fp{i} BLOB NOT NULL" for i in range(N_TABLES))
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} ("
        f"  slug TEXT PRIMARY KEY, {cols}"
        f")"
    )
    conn.commit()


def is_simhash8_populated(conn: sqlite3.Connection) -> bool:
    """Check if radar_simhash8 table exists and has data."""
    try:
        count = conn.execute(f"SELECT count(*) FROM {TABLE_NAME}").fetchone()[0]
        return count > 0
    except Exception:
        return False


def store_fingerprints_batch(
    conn: sqlite3.Connection,
    slugs: list[str],
    fps: np.ndarray,
) -> None:
    """
    Store multi-probe fingerprints in radar_simhash8.

    Args:
        conn: SQLite connection
        slugs: list of slug strings (length N)
        fps: (N, N_TABLES, K_BYTES) uint8
    """
    ensure_simhash8_table(conn)
    cols = ", ".join(f"fp{i}" for i in range(N_TABLES))
    placeholders = ", ".join("?" for _ in range(N_TABLES + 1))  # slug + 8 fps
    sql = f"INSERT OR REPLACE INTO {TABLE_NAME}(slug, {cols}) VALUES ({placeholders})"

    rows = []
    for i, slug in enumerate(slugs):
        fp_blobs = tuple(fps[i, t, :].tobytes() for t in range(N_TABLES))
        rows.append((slug,) + fp_blobs)

    conn.executemany(sql, rows)
    conn.commit()


def store_fingerprints_single(
    conn: sqlite3.Connection,
    slug: str,
    fps: np.ndarray,
) -> None:
    """
    Store fingerprints for a single slug.

    Args:
        fps: (N_TABLES, K_BYTES) uint8
    """
    ensure_simhash8_table(conn)
    cols = ", ".join(f"fp{i}" for i in range(N_TABLES))
    placeholders = ", ".join("?" for _ in range(N_TABLES + 1))
    sql = f"INSERT OR REPLACE INTO {TABLE_NAME}(slug, {cols}) VALUES ({placeholders})"
    fp_blobs = tuple(fps[t, :].tobytes() for t in range(N_TABLES))
    conn.execute(sql, (slug,) + fp_blobs)
    conn.commit()


def load_all_fingerprints(conn: sqlite3.Connection) -> tuple[list[str], np.ndarray]:
    """
    Load all fingerprints from radar_simhash8.

    Returns:
        slugs: list of slug strings
        fps: (N, N_TABLES, K_BYTES) uint8
    """
    cols = ", ".join(f"fp{i}" for i in range(N_TABLES))
    rows = conn.execute(f"SELECT slug, {cols} FROM {TABLE_NAME}").fetchall()

    if not rows:
        return [], np.zeros((0, N_TABLES, K_BYTES), dtype=np.uint8)

    slugs = [r[0] for r in rows]
    fps = np.zeros((len(rows), N_TABLES, K_BYTES), dtype=np.uint8)
    for i, row in enumerate(rows):
        for t in range(N_TABLES):
            blob = row[t + 1]
            fps[i, t, :] = np.frombuffer(blob, dtype=np.uint8)

    return slugs, fps


def simhash_coarse_search(
    query_fps: np.ndarray,
    all_slugs: list[str],
    all_fps: np.ndarray,
    top_n: int = COARSE_LIMIT,
) -> list[str]:
    """
    Multi-probe coarse search: find top_n candidates by minimum hamming distance.

    Args:
        query_fps: (N_TABLES, K_BYTES) uint8 — query fingerprints
        all_slugs: list of N slug strings
        all_fps: (N, N_TABLES, K_BYTES) uint8
        top_n: number of candidates to return

    Returns:
        List of top_n slugs (ordered by similarity, best first)
    """
    if len(all_slugs) == 0:
        return []

    scores = hamming_distance_batch(query_fps, all_fps)  # (N,) lower = better
    top_n = min(top_n, len(all_slugs))
    top_indices = np.argpartition(scores, top_n)[:top_n]
    # Sort the top_n by actual score
    top_indices = top_indices[np.argsort(scores[top_indices])]
    return [all_slugs[i] for i in top_indices]


def load_vectors_from_db(conn: sqlite3.Connection) -> tuple[list[str], np.ndarray]:
    """
    Load all float32 vectors from radar_vec storage.

    Returns:
        slugs: list of slug strings
        vecs: (N, EMBED_DIM) float32
    """
    rowids = conn.execute(
        "SELECT id, chunk_id, chunk_offset FROM radar_vec_rowids ORDER BY rowid"
    ).fetchall()

    if not rowids:
        return [], np.zeros((0, EMBED_DIM), dtype=np.float32)

    # Load the chunk blob (all vectors in chunk 1)
    chunk_blob = conn.execute(
        "SELECT vectors FROM radar_vec_vector_chunks00 WHERE rowid=1"
    ).fetchone()

    if chunk_blob is None:
        return [], np.zeros((0, EMBED_DIM), dtype=np.float32)

    all_vecs = np.frombuffer(chunk_blob[0], dtype=np.float32).reshape(-1, EMBED_DIM)

    slugs = []
    vecs = []
    for slug_id, chunk_id, chunk_offset in rowids:
        if chunk_offset >= len(all_vecs):
            logger.warning("simhash: chunk_offset %d out of range for %s, skipping",
                           chunk_offset, slug_id)
            continue
        slugs.append(slug_id)
        vecs.append(all_vecs[chunk_offset])

    if not vecs:
        return [], np.zeros((0, EMBED_DIM), dtype=np.float32)

    return slugs, np.stack(vecs, axis=0)


def backfill_simhash8(db_path: Path, batch_size: int = 128) -> int:
    """
    Compute and store SimHash fingerprints for all vectors in radar_vec.
    Creates/replaces radar_simhash8 table.

    Returns count of stored fingerprints.
    """
    conn = sqlite3.connect(str(db_path))

    try:
        slugs, vecs = load_vectors_from_db(conn)
        if not slugs:
            logger.warning("simhash: no vectors found in radar_vec, nothing to backfill")
            conn.close()
            return 0

        logger.info("simhash: backfilling %d vectors in batches of %d", len(slugs), batch_size)
        ensure_simhash8_table(conn)

        stored = 0
        for i in range(0, len(slugs), batch_size):
            batch_slugs = slugs[i:i + batch_size]
            batch_vecs = vecs[i:i + batch_size]
            fps = compute_fingerprints(batch_vecs)  # (batch, N_TABLES, K_BYTES)
            store_fingerprints_batch(conn, batch_slugs, fps)
            stored += len(batch_slugs)
            logger.info("simhash: backfill progress %d/%d", stored, len(slugs))

        logger.info("simhash: backfill complete, stored %d fingerprints", stored)
        conn.close()
        return stored

    except Exception as e:
        logger.error("simhash: backfill failed: %s", e)
        conn.close()
        raise
