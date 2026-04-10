"""
fts_search.py — FTS5 cross-bot message search wrapper.
Delegates to ~/.claude-bots/shared/fts5/search.py.
"""
import sys
from pathlib import Path
from typing import Optional

from ..config import SHARED_ROOT

_FTS5_DIR = SHARED_ROOT / "fts5"


def _import_search():
    """Lazy import search module from fts5 directory."""
    fts5_str = str(_FTS5_DIR)
    if fts5_str not in sys.path:
        sys.path.insert(0, fts5_str)
    import importlib
    return importlib.import_module("search")


def fts_search(query: str, limit: int = 10, bot: Optional[str] = None) -> list[dict]:
    """
    Run FTS5 search over memory.db.
    Returns list of result dicts with keys: bot_name, ts, source, chat_id,
    user, message_id, snippet, rank.
    Raises RuntimeError if memory.db does not exist.
    """
    from ..config import FTS_DB
    if not FTS_DB.exists():
        raise FileNotFoundError(f"memory.db not found at {FTS_DB}")
    if not _FTS5_DIR.exists():
        raise FileNotFoundError(f"fts5 module directory not found at {_FTS5_DIR}")

    search_mod = _import_search()
    results = search_mod.search(query, limit=limit, bot=bot)
    return results
