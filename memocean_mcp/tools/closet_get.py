"""
closet_get.py — Closet content retrieval wrapper.

Modes:
  verbatim — fetch original drawer content by slug (no LLM)
              Looks up drawer_path from the closet DB table directly,
              so slugs returned by closet_search() always work.
  skeleton — read raw CLSC skeleton text from the closet DB table.
"""
import datetime
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Literal

from ..config import CLOSET_ROOT, FTS_DB

_LOG_PATH = os.path.expanduser('~/.claude-bots/logs/clsc-usage.jsonl')


def _log_verbatim_get(slug: str, content: str) -> None:
    """Append one JSON line to the usage log for verbatim fetch. Swallows all exceptions."""
    try:
        bot = os.path.basename(os.environ.get('TELEGRAM_STATE_DIR', '')) or 'unknown'
        ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + \
             f"{datetime.datetime.now(datetime.timezone.utc).microsecond // 1000:03d}Z"

        entry = {
            'ts': ts,
            'event': 'closet_get_verbatim',
            'bot': bot,
            'slug': slug,
            'mode': 'verbatim',
            'verbatim_tokens': len(content) // 4,
            'reason': 'skeleton不夠需要原文',
        }

        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass

_SAFE_SLUG_RE = re.compile(r'^[A-Za-z0-9_\-]{1,100}$')


def _validate_slug(slug: str) -> None:
    """Reject slugs that could escape the sandbox via path traversal."""
    if not _SAFE_SLUG_RE.match(slug):
        raise ValueError(f"Invalid slug '{slug}': must match [A-Za-z0-9_-]{{1,100}}")


def _db_row(slug: str) -> dict | None:
    """Look up the closet row for slug directly in the DB. Returns None if not found."""
    if not FTS_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(FTS_DB))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT slug, clsc, tokens, drawer_path FROM closet WHERE slug = ?",
            (slug,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def verbatim_fetch(slug: str) -> str:
    """
    Mode (i): fetch original drawer content by slug.
    Uses drawer_path stored in the closet DB — no slug re-normalization needed.
    Returns file content or an error message.
    """
    _validate_slug(slug)
    row = _db_row(slug)
    if row is None:
        return f"[drawer not found for slug: {slug}]"
    drawer_path = row.get("drawer_path")
    if not drawer_path:
        return f"[drawer_path missing in DB for slug: {slug}]"
    path = Path(drawer_path)
    if not path.exists():
        return f"[drawer file not on disk: {drawer_path}]"
    content = path.read_text(encoding="utf-8")
    _log_verbatim_get(slug, content)
    return content


def skeleton_read(slug: str) -> str:
    """
    Mode (ii): read raw CLSC skeleton text for a slug.
    Reads the clsc column from the closet DB first (covers all indexed slugs).
    Falls back to CLOSET_ROOT file lookup for any legacy bundle files.
    """
    _validate_slug(slug)
    # Primary: read from DB
    row = _db_row(slug)
    if row is not None:
        clsc = row.get("clsc")
        if clsc:
            return clsc
    # Fallback: try legacy CLOSET_ROOT files
    if CLOSET_ROOT.exists():
        for ext in (".json", ".md", ".txt", ""):
            path = CLOSET_ROOT / f"{slug}{ext}"
            if path.resolve().is_relative_to(CLOSET_ROOT.resolve()) and path.exists():
                return path.read_text(encoding="utf-8")
    return f"[closet bundle not found for slug: {slug}]"


def closet_get(slug: str, mode: Literal["verbatim", "skeleton"] = "verbatim") -> str:
    """
    Unified entry point.
    mode='verbatim' → verbatim_fetch(slug)
    mode='skeleton' → skeleton_read(slug)
    """
    if mode == "verbatim":
        return verbatim_fetch(slug)
    elif mode == "skeleton":
        return skeleton_read(slug)
    else:
        return f"[unknown mode: {mode}. Use 'verbatim' or 'skeleton']"
