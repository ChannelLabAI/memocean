#!/usr/bin/env python3
"""Backfill closet_fts — FTS5 virtual table for BM25-ranked closet search.

Creates the closet_fts table (trigram tokenizer, CJK-friendly) and copies
all rows from the closet table into it.

Usage:
    python3 ~/.claude-bots/shared/fts5/backfill_closet_fts.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import DB_PATH, open_db  # noqa: E402

CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS closet_fts USING fts5(
  slug,
  clsc,
  tokenize = 'trigram case_sensitive 0'
);
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='create + populate then rollback')
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f'ERROR: {DB_PATH} not found', file=sys.stderr)
        return 1

    conn = open_db()
    t0 = time.time()

    # 1. Create FTS5 table
    conn.executescript(CREATE_FTS)
    print(f'closet_fts table ensured in {DB_PATH}')

    # 2. Clear existing FTS data (idempotent re-run)
    conn.execute("DELETE FROM closet_fts")

    # 3. Backfill from closet
    cur = conn.execute("SELECT slug, clsc FROM closet")
    rows = cur.fetchall()
    inserted = 0
    for slug, clsc in rows:
        conn.execute("INSERT INTO closet_fts(slug, clsc) VALUES (?, ?)", (slug, clsc))
        inserted += 1

    if args.dry_run:
        conn.rollback()
        print(f'(dry-run: rolled back) would insert {inserted} rows')
    else:
        conn.commit()
        print(f'Inserted {inserted} rows into closet_fts ({time.time() - t0:.2f}s)')

    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
