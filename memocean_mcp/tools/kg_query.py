"""
kg_query.py — Temporal Knowledge Graph query wrapper.
Delegates to ~/.claude-bots/shared/kg/kg_helper.py.
"""
import sys
from pathlib import Path
from typing import Optional

from ..config import SHARED_ROOT

_KG_DIR = SHARED_ROOT / "kg"


def _import_kg_helper():
    """Lazy import kg_helper module."""
    kg_str = str(_KG_DIR)
    if kg_str not in sys.path:
        sys.path.insert(0, kg_str)
    import importlib
    return importlib.import_module("kg_helper")


def kg_query(entity: str, as_of: Optional[str] = None, direction: str = "outgoing") -> list[dict]:
    """
    Query facts about an entity at a point in time.
    entity: e.g. '老兔', 'Wes', 'anna'
    as_of: ISO date string YYYY-MM-DD (defaults to today)
    direction: 'outgoing' (default), 'incoming', or 'both'
    Returns list of fact dicts.
    """
    if not _KG_DIR.exists():
        raise FileNotFoundError(f"kg module directory not found at {_KG_DIR}")
    mod = _import_kg_helper()
    return mod.kg_query(entity, as_of=as_of, direction=direction)


def kg_query_all(as_of: Optional[str] = None) -> list[dict]:
    """Return all active facts as of the given date."""
    if not _KG_DIR.exists():
        raise FileNotFoundError(f"kg module directory not found at {_KG_DIR}")
    mod = _import_kg_helper()
    return mod.kg_query_all(as_of=as_of)


def kg_stats() -> dict:
    """Return graph statistics."""
    if not _KG_DIR.exists():
        raise FileNotFoundError(f"kg module directory not found at {_KG_DIR}")
    mod = _import_kg_helper()
    return mod.kg_stats()
