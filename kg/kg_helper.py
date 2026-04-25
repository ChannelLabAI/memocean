"""
kg_helper.py — Temporal Knowledge Graph convenience API.
Thin wrapper around knowledge_graph.py for common bot operations.

Usage:
    from kg_helper import kg_add, kg_query, kg_invalidate, kg_query_all

    # Add a fact
    kg_add("Alice", "role", "CEO", started="2020-01-01", source="team-config")

    # Query current facts about an entity
    kg_query("Alice")

    # Query facts as of a past date
    kg_query("Bob", as_of="2024-06-01")

    # Invalidate (mark ended, non-destructive)
    kg_invalidate("Bob", "role", "investor", ended="2025-06-01")
"""
from pathlib import Path
from datetime import date

import sys
sys.path.insert(0, str(Path(__file__).parent))
from knowledge_graph import KnowledgeGraph

# Default DB path — resolved via config, shared across all bots
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from memocean_mcp.config import KG_DB
except Exception:
    KG_DB = Path.home() / ".memocean" / "kg.db"


def _kg() -> KnowledgeGraph:
    return KnowledgeGraph(str(KG_DB))


def kg_add(
    subject: str,
    predicate: str,
    obj: str,
    started: str = None,
    ended: str = None,
    source: str = "manual",
    confidence: float = 1.0,
) -> str:
    """
    Add a temporal fact triple.
    started/ended: ISO date string (YYYY-MM-DD) or None.
    Returns triple ID.
    """
    if started is None:
        started = date.today().isoformat()
    kg = _kg()
    return kg.add_triple(
        subject=subject,
        predicate=predicate,
        obj=obj,
        valid_from=started,
        valid_to=ended,
        confidence=confidence,
        source_ref=source,
    )


def kg_query(entity: str, as_of: str = None, direction: str = "outgoing") -> list:
    """
    Query facts about an entity at a point in time.
    as_of: ISO date string (YYYY-MM-DD), defaults to today.
    direction: "outgoing" (default), "incoming", or "both".
    Returns list of fact dicts.
    """
    if as_of is None:
        as_of = date.today().isoformat()
    kg = _kg()
    return kg.query_entity(entity, as_of=as_of, direction=direction)


def kg_invalidate(
    subject: str,
    predicate: str,
    obj: str,
    ended: str = None,
) -> None:
    """
    Mark a fact as ended (non-destructive — original row preserved).
    ended: ISO date string, defaults to today.
    """
    if ended is None:
        ended = date.today().isoformat()
    kg = _kg()
    kg.invalidate(subject=subject, predicate=predicate, obj=obj, ended=ended)


def kg_query_all(as_of: str = None) -> list:
    """Return all active facts as of the given date."""
    if as_of is None:
        as_of = date.today().isoformat()
    kg = _kg()
    return kg.query_all(as_of=as_of)


def kg_stats() -> dict:
    """Return graph statistics (entity count, triple count, relationship types)."""
    return _kg().stats()


def kg_timeline(entity: str = None) -> list:
    """Return facts in chronological order, optionally filtered by entity."""
    return _kg().timeline(entity_name=entity)
