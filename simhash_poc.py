"""
SimHash Phase 1 PoC for MemOcean
=================================
Goal: Verify that SimHash (1-bit quantization, k=384 bits) achieves
overlap@10 >= 80% vs float32 BGE-m3 KNN on the existing radar_vec corpus.

Usage:
    python3 simhash_poc.py

Output:
    - Prints per-query overlap stats
    - Writes summary to ~/.claude-bots/state/anya/simhash-phase1-result.md
"""

import sqlite3
import struct
import sys
import time
from pathlib import Path

import numpy as np

# ── Config ──────────────────────────────────────────────────────────────────
# Resolve paths via config when available; fall back to defaults.
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from memocean_mcp.config import FTS_DB as DB_PATH, MEMOCEAN_DATA_DIR as _DATA_DIR
    RESULT_PATH = _DATA_DIR / "state" / "simhash-phase1-result.md"  # example only
except Exception:
    DB_PATH = Path.home() / ".memocean" / "memory.db"
    RESULT_PATH = Path.home() / ".memocean" / "state" / "simhash-phase1-result.md"  # example only
DIM = 1024          # BGE-m3 embedding dimension
K_BITS = 384        # SimHash bits (48 bytes)
SEED = 42           # fixed seed for reproducibility
TOP_K = 10          # overlap@10
N_QUERIES = 100     # number of query vectors to evaluate (random sample)
STORE_TABLE = "radar_simhash"


# ── Step 1: Load float32 vectors from sqlite-vec storage ──────────────────
def load_vectors_from_db(db_path: Path) -> tuple[list[str], np.ndarray]:
    """
    Load all slug→vector pairs from the radar_vec tables.
    Returns (slugs, matrix) where matrix is (N, DIM) float32.
    """
    conn = sqlite3.connect(str(db_path))

    # Get slug + chunk_offset, ordered by chunk_offset
    rowids = conn.execute(
        "SELECT id, chunk_id, chunk_offset FROM radar_vec_rowids ORDER BY rowid"
    ).fetchall()

    # Load the chunk blob (all vectors are in chunk 1)
    chunk_blob = conn.execute(
        "SELECT vectors FROM radar_vec_vector_chunks00 WHERE rowid=1"
    ).fetchone()[0]
    conn.close()

    n_total = len(rowids)
    all_vecs = np.frombuffer(chunk_blob, dtype=np.float32).reshape(-1, DIM)

    slugs = []
    vecs = []
    for slug_id, chunk_id, chunk_offset in rowids:
        if chunk_offset >= len(all_vecs):
            print(f"  WARNING: chunk_offset {chunk_offset} out of range for {slug_id}, skipping")
            continue
        slugs.append(slug_id)
        vecs.append(all_vecs[chunk_offset])

    vecs_matrix = np.stack(vecs, axis=0)  # (N, DIM)
    print(f"[load] Loaded {len(slugs)} vectors, shape={vecs_matrix.shape}")
    return slugs, vecs_matrix


# ── Step 2: Generate SimHash projection matrix ────────────────────────────
def make_projection_matrix(dim: int, k_bits: int, seed: int) -> np.ndarray:
    """
    Random hyperplane matrix R: shape (dim, k_bits), each column is a unit normal.
    SimHash(v) = sign(R^T @ v) → k_bits binary values.
    """
    rng = np.random.RandomState(seed)
    R = rng.randn(dim, k_bits).astype(np.float32)
    # Normalize each hyperplane (optional for SimHash, but makes it cleaner)
    norms = np.linalg.norm(R, axis=0, keepdims=True)
    R /= norms
    return R


# ── Step 3: Compute SimHash fingerprints ──────────────────────────────────
def compute_simhash(vecs: np.ndarray, R: np.ndarray) -> np.ndarray:
    """
    vecs: (N, DIM) float32
    R: (DIM, K_BITS) float32
    Returns: (N, K_BITS//8) uint8 — packed bit arrays
    """
    # Projections: (N, K_BITS)
    projections = vecs @ R  # (N, K_BITS)
    # Sign binarization: 1 if > 0, 0 if <= 0
    bits = (projections > 0).astype(np.uint8)  # (N, K_BITS)
    # Pack bits into bytes: (N, K_BITS//8) uint8
    n = bits.shape[0]
    k_bytes = K_BITS // 8
    packed = np.packbits(bits, axis=1)  # (N, K_BITS//8)
    return packed


# ── Step 4: Store SimHash fingerprints in SQLite ──────────────────────────
def store_simhash(db_path: Path, slugs: list[str], fingerprints: np.ndarray) -> None:
    """
    Create/replace radar_simhash table and store (slug, fingerprint) rows.
    fingerprints: (N, K_BITS//8) uint8
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"DROP TABLE IF EXISTS {STORE_TABLE}")
    conn.execute(
        f"CREATE TABLE {STORE_TABLE} ("
        f"  slug TEXT PRIMARY KEY, "
        f"  fingerprint BLOB NOT NULL"
        f")"
    )
    for slug, fp in zip(slugs, fingerprints):
        conn.execute(
            f"INSERT INTO {STORE_TABLE}(slug, fingerprint) VALUES (?, ?)",
            (slug, fp.tobytes()),
        )
    conn.commit()
    conn.close()
    print(f"[store] Stored {len(slugs)} SimHash fingerprints in {STORE_TABLE}")


# ── Step 5: Hamming distance KNN ──────────────────────────────────────────
def hamming_distance_batch(query_fp: np.ndarray, all_fps: np.ndarray) -> np.ndarray:
    """
    Compute hamming distances from query_fp to all rows in all_fps.
    query_fp: (K_BITS//8,) uint8
    all_fps: (N, K_BITS//8) uint8
    Returns: (N,) int hamming distances
    """
    # XOR then popcount via unpackbits
    xored = np.bitwise_xor(query_fp[np.newaxis, :], all_fps)  # (N, K_BITS//8)
    bits = np.unpackbits(xored, axis=1)  # (N, K_BITS)
    return bits.sum(axis=1)  # (N,) int


def simhash_knn(query_fp: np.ndarray, all_fps: np.ndarray, k: int) -> np.ndarray:
    """Returns indices of top-k nearest by hamming distance (ascending)."""
    dists = hamming_distance_batch(query_fp, all_fps)
    return np.argsort(dists)[:k]


# ── Step 6: Float32 cosine KNN ────────────────────────────────────────────
def cosine_knn(query_vec: np.ndarray, all_vecs: np.ndarray, k: int) -> np.ndarray:
    """Returns indices of top-k nearest by cosine similarity (descending)."""
    # Normalize
    q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    norms = np.linalg.norm(all_vecs, axis=1, keepdims=True) + 1e-10
    sims = all_vecs @ q_norm / norms[:, 0]
    return np.argsort(-sims)[:k]


# ── Step 7: Evaluate overlap@10 ───────────────────────────────────────────
def evaluate_overlap(
    slugs: list[str],
    vecs: np.ndarray,
    fingerprints: np.ndarray,
    n_queries: int,
    k: int,
    seed: int,
) -> dict:
    """
    Sample n_queries vectors as queries, compute overlap@k between
    float32 KNN and SimHash KNN. Excludes the query itself.
    """
    n = len(slugs)
    rng = np.random.RandomState(seed + 1)
    query_indices = rng.choice(n, size=min(n_queries, n), replace=False)

    overlaps = []
    times_float = []
    times_simhash = []

    for qi in query_indices:
        q_vec = vecs[qi]
        q_fp = fingerprints[qi]

        # Float32 KNN (exclude self)
        t0 = time.perf_counter()
        f32_top = cosine_knn(q_vec, vecs, k + 1)
        f32_top = f32_top[f32_top != qi][:k]
        times_float.append(time.perf_counter() - t0)

        # SimHash KNN (exclude self)
        t0 = time.perf_counter()
        sh_top = simhash_knn(q_fp, fingerprints, k + 1)
        sh_top = sh_top[sh_top != qi][:k]
        times_simhash.append(time.perf_counter() - t0)

        # Overlap
        overlap = len(set(f32_top.tolist()) & set(sh_top.tolist())) / k
        overlaps.append(overlap)

    return {
        "n_queries": len(query_indices),
        "k": k,
        "mean_overlap": float(np.mean(overlaps)),
        "median_overlap": float(np.median(overlaps)),
        "min_overlap": float(np.min(overlaps)),
        "max_overlap": float(np.max(overlaps)),
        "pct_pass": float(np.mean([o >= 0.8 for o in overlaps])),
        "mean_time_float_ms": float(np.mean(times_float) * 1000),
        "mean_time_simhash_ms": float(np.mean(times_simhash) * 1000),
        "storage_float_bytes_per_vec": DIM * 4,
        "storage_simhash_bytes_per_vec": K_BITS // 8,
        "compression_ratio": (DIM * 4) / (K_BITS // 8),
    }


# ── Step 8: Write result markdown ─────────────────────────────────────────
def write_result(result: dict, slugs: list[str]) -> None:
    passed = result["mean_overlap"] >= 0.80
    status = "PASSED" if passed else "FAILED"

    md = f"""# SimHash Phase 1 PoC — Result

**Date:** 2026-04-13
**Status:** {status}

## Configuration

| Parameter | Value |
|---|---|
| Embedding model | BGE-m3 |
| Embedding dim | {DIM} |
| SimHash bits (k) | {K_BITS} |
| Random seed | {SEED} |
| Corpus size | {len(slugs)} vectors |
| Query sample | {result['n_queries']} |
| Top-K | {result['k']} |

## Results

| Metric | Value |
|---|---|
| **Mean overlap@{result['k']}** | **{result['mean_overlap']:.1%}** |
| Median overlap@{result['k']} | {result['median_overlap']:.1%} |
| Min overlap | {result['min_overlap']:.1%} |
| Max overlap | {result['max_overlap']:.1%} |
| % queries with overlap ≥ 80% | {result['pct_pass']:.1%} |
| Pass threshold | ≥ 80% mean overlap |

## Performance

| Metric | Float32 KNN | SimHash Hamming |
|---|---|---|
| Mean query time | {result['mean_time_float_ms']:.3f} ms | {result['mean_time_simhash_ms']:.3f} ms |

## Storage

| Format | Bytes/vector | Compression |
|---|---|---|
| Float32 ({DIM}d) | {result['storage_float_bytes_per_vec']} | 1x |
| SimHash ({K_BITS}b) | {result['storage_simhash_bytes_per_vec']} | {result['compression_ratio']:.0f}x |

## Conclusion

{"Phase 1 PASSED. SimHash achieves ≥80% overlap with float32 KNN top-10 on the existing BGE-m3 corpus. Proceed to Phase 2 integration." if passed else "Phase 1 FAILED. SimHash overlap < 80% target. Investigate: (1) increase K_BITS, (2) use multi-probe LSH, (3) consider different quantization approach."}

## SQLite

SimHash fingerprints stored in `radar_simhash` table in `memory.db`.
- Schema: `slug TEXT PRIMARY KEY, fingerprint BLOB` ({K_BITS // 8} bytes each)
- Total size: {len(slugs) * (K_BITS // 8):,} bytes ({len(slugs) * (K_BITS // 8) / 1024:.1f} KB)
- Float32 baseline: {len(slugs) * DIM * 4:,} bytes ({len(slugs) * DIM * 4 / 1024:.1f} KB)

## Notes

- Projection matrix R (1024×384, seed={SEED}) must be stored/regenerated consistently for production use
- Phase 2 will integrate SimHash into reranker.py / radar_search.py pipeline
- For Phase 2: consider using SimHash as pre-filter (coarse) + float32 as precise re-scorer (two-layer)
"""

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(md, encoding="utf-8")
    print(f"[result] Written to {RESULT_PATH}")


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("SimHash Phase 1 PoC")
    print("=" * 60)

    # 1. Load vectors
    print("\n[1/5] Loading float32 vectors from DB...")
    slugs, vecs = load_vectors_from_db(DB_PATH)
    n = len(slugs)
    print(f"      {n} vectors of dim {DIM}")

    # 2. Generate projection matrix
    print(f"\n[2/5] Generating projection matrix ({DIM}×{K_BITS}, seed={SEED})...")
    t0 = time.perf_counter()
    R = make_projection_matrix(DIM, K_BITS, SEED)
    print(f"      Done in {(time.perf_counter()-t0)*1000:.1f}ms, shape={R.shape}")

    # 3. Compute SimHash fingerprints
    print(f"\n[3/5] Computing SimHash fingerprints...")
    t0 = time.perf_counter()
    fingerprints = compute_simhash(vecs, R)
    print(f"      Done in {(time.perf_counter()-t0)*1000:.1f}ms, shape={fingerprints.shape}")
    print(f"      Storage: {fingerprints.nbytes} bytes total ({K_BITS//8} bytes/vec)")

    # 4. Store in SQLite
    print(f"\n[4/5] Storing SimHash fingerprints in SQLite...")
    store_simhash(DB_PATH, slugs, fingerprints)

    # 5. Evaluate overlap
    print(f"\n[5/5] Evaluating overlap@{TOP_K} ({N_QUERIES} query samples)...")
    result = evaluate_overlap(slugs, vecs, fingerprints, N_QUERIES, TOP_K, SEED)

    print("\n" + "=" * 60)
    print(f"RESULT: mean overlap@{TOP_K} = {result['mean_overlap']:.1%}")
    print(f"RESULT: median overlap@{TOP_K} = {result['median_overlap']:.1%}")
    print(f"RESULT: min/max = {result['min_overlap']:.1%} / {result['max_overlap']:.1%}")
    print(f"RESULT: % queries passing ≥80% threshold = {result['pct_pass']:.1%}")
    print(f"RESULT: float32 query time = {result['mean_time_float_ms']:.3f}ms")
    print(f"RESULT: simhash query time = {result['mean_time_simhash_ms']:.3f}ms")
    print(f"RESULT: compression = {result['compression_ratio']:.0f}x")
    passed = result["mean_overlap"] >= 0.80
    print(f"\nPhase 1 {'PASSED ✓' if passed else 'FAILED ✗'}")
    print("=" * 60)

    # Write result
    write_result(result, slugs)


if __name__ == "__main__":
    main()
