#!/usr/bin/env python3
"""
backfill_embeddings.py — Generate embeddings for all closet entries and store
them in the closet_vec table (sqlite-vec) inside memory.db.

Usage:
    python3 backfill_embeddings.py [--db PATH] [--batch-size N] [--force]

Options:
    --db PATH       Path to memory.db (default: ~/.claude-bots/memory.db)
    --batch-size N  Embedding batch size (default: 32)
    --force         Re-embed all entries, even if already in closet_vec
"""
import argparse
import os
import sqlite3
import sys
import time

# Add parent paths so we can import the reranker
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memocean-mcp"))

def main():
    parser = argparse.ArgumentParser(description="Backfill closet embeddings")
    parser.add_argument(
        "--db",
        default=os.path.expanduser("~/.claude-bots/memory.db"),
        help="Path to memory.db",
    )
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size")
    parser.add_argument("--force", action="store_true", help="Re-embed all entries")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"ERROR: {args.db} not found")
        sys.exit(1)

    # Import reranker functions
    try:
        from memocean_mcp.tools.reranker import (
            embed_and_store_batch,
            _load_sqlite_vec,
            _ensure_vec_table,
            _VEC_TABLE,
        )
    except ImportError:
        # Direct import fallback
        sys.path.insert(
            0,
            os.path.join(
                os.path.dirname(__file__), "..", "memocean-mcp"
            ),
        )
        from memocean_mcp.tools.reranker import (
            embed_and_store_batch,
            _load_sqlite_vec,
            _ensure_vec_table,
            _VEC_TABLE,
        )

    conn = sqlite3.connect(args.db)

    # Load sqlite-vec
    if not _load_sqlite_vec(conn):
        print("ERROR: cannot load sqlite-vec extension")
        sys.exit(1)

    _ensure_vec_table(conn)

    # Get all closet entries
    rows = conn.execute("SELECT slug, clsc FROM closet").fetchall()
    total = len(rows)
    print(f"Total closet entries: {total}")

    if not args.force:
        # Check which slugs already have embeddings
        try:
            existing = set(
                r[0]
                for r in conn.execute(f"SELECT slug FROM {_VEC_TABLE}").fetchall()
            )
            print(f"Already embedded: {len(existing)}")
            rows = [(s, a) for s, a in rows if s not in existing]
            print(f"To embed: {len(rows)}")
        except Exception:
            pass

    if not rows:
        print("Nothing to do.")
        conn.close()
        return

    t0 = time.monotonic()
    stored = embed_and_store_batch(conn, rows, batch_size=args.batch_size)
    elapsed = time.monotonic() - t0

    print(f"Embedded {stored}/{len(rows)} entries in {elapsed:.1f}s")
    print(f"Speed: {stored / elapsed:.1f} entries/sec")

    # Verify
    count = conn.execute(f"SELECT count(*) FROM {_VEC_TABLE}").fetchone()[0]
    print(f"Total embeddings in closet_vec: {count}")
    conn.close()


if __name__ == "__main__":
    main()
