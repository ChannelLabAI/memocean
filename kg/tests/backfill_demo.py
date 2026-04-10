#!/usr/bin/env python3
"""
backfill_demo.py — Backfill 5 demo facts into kg.db

Run directly:
    python3 ~/.claude-bots/shared/kg/tests/backfill_demo.py
"""
import sys
from pathlib import Path

# Ensure kg module is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from knowledge_graph import KnowledgeGraph

KG_DB = Path.home() / ".claude-bots" / "kg.db"


def backfill():
    kg = KnowledgeGraph(str(KG_DB))

    facts = [
        # (subject, predicate, obj, valid_from, valid_to, source_ref)
        ("<OWNER>",     "role",          "CEO",        "2020-01-01", None,         "team-config"),
        ("<PARTNER>",   "role",          "shareholder","2024-01-01", "2025-06-01", "wiki/example"),
        ("<PARTNER>",   "role",          "LP",         "2025-06-01", None,         "wiki/example"),
        ("<OWNER>",     "project_owner", "EXAMPLE_PROJECT", "2025-01-01", None,   "wiki"),
        ("ChannelLab",  "product",       "GEO服務",    "2026-01-01", None,         "wiki"),
    ]

    inserted = 0
    for subject, predicate, obj, valid_from, valid_to, source_ref in facts:
        tid = kg.add_triple(
            subject=subject,
            predicate=predicate,
            obj=obj,
            valid_from=valid_from,
            valid_to=valid_to,
            confidence=1.0,
            source_ref=source_ref,
        )
        print(f"  + {subject} --[{predicate}]--> {obj}  ({valid_from} ~ {valid_to or 'now'})  id={tid}")
        inserted += 1

    # For <PARTNER>/shareholder: if valid_to wasn't set during insert (dedup path),
    # explicitly invalidate to ensure the ended date is stored.
    # (add_triple with valid_to set handles it directly via the INSERT path)

    stats = kg.stats()
    print(f"\nKG stats after backfill: {stats}")
    print(f"Backfill complete: {inserted} facts processed.")


if __name__ == "__main__":
    print(f"Backfilling demo facts into {Path.home() / '.claude-bots' / 'kg.db'} ...")
    backfill()
