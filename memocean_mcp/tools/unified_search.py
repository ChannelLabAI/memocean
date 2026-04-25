"""
unified_search.py — Radar-First unified search facade (MEMO-012).

Default search order: Radar (CLSC sonar) → Messages.
Ocean vault full-text is opt-in only (source='ocean').

Source priority for result ranking: ocean=3 > radar=2 > messages=1.

Result schema (normalized across all sources):
  title      — display name (filename for Ocean, slug for Radar, msg_key for Messages)
  excerpt    — ~200 chars of matching/summary text
  source     — "ocean" | "radar" | "messages"
  ref        — stable reference key for dedup (path for ocean, slug for radar, msg_key for msgs)
  score_rank — integer tie-break rank within source (1 = best match, lower = better)
  wikilink   — [[title]] (ocean only, empty string for others)
  path       — relative path (ocean only)
  drawer_path — drawer path (radar/messages only)
"""
from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger("memocean_mcp.unified_search")

SourceType = Literal["ocean", "radar", "messages", "all"]


def _normalize_ocean(results: list[dict]) -> list[dict]:
    """Normalize ocean_search results to unified schema."""
    out = []
    for i, r in enumerate(results, 1):
        out.append({
            "title": r.get("title", ""),
            "excerpt": r.get("excerpt", ""),
            "source": "ocean",
            "ref": r.get("path", r.get("title", "")),
            "score_rank": i,
            "wikilink": r.get("wikilink", ""),
            "path": r.get("path", ""),
            "drawer_path": "",
        })
    return out


def _normalize_radar(results: list[dict]) -> list[dict]:
    """Normalize radar_search results to unified schema."""
    out = []
    for i, r in enumerate(results, 1):
        slug = r.get("slug", "")
        clsc = r.get("clsc", "")
        out.append({
            "title": slug,
            "excerpt": clsc[:200] if clsc else "",
            "source": "radar",
            "ref": slug,
            "score_rank": i,
            "wikilink": "",
            "path": "",
            "drawer_path": r.get("drawer_path", ""),
        })
    return out


def _normalize_messages(results: list[dict]) -> list[dict]:
    """Normalize messages_hybrid_search results to unified schema."""
    out = []
    for i, r in enumerate(results, 1):
        msg_key = r.get("slug", r.get("message_id", ""))
        text = r.get("clsc", r.get("text", ""))
        out.append({
            "title": msg_key,
            "excerpt": text[:200] if text else "",
            "source": "messages",
            "ref": msg_key,
            "score_rank": i,
            "wikilink": "",
            "path": "",
            "drawer_path": r.get("drawer_path", ""),
        })
    return out


_SOURCE_PRIORITY = {"ocean": 3, "radar": 2, "messages": 1}


def _merge_and_rank(ocean: list[dict], radar: list[dict], messages: list[dict], limit: int) -> list[dict]:
    """
    Merge results from all sources, dedup by (source, ref), sort by:
      1. source priority (ocean > radar > messages)
      2. score_rank within source (lower is better)
    """
    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []

    for result in ocean + radar + messages:
        key = (result["source"], result["ref"])
        if key in seen or not result.get("ref"):
            continue
        seen.add(key)
        merged.append(result)

    merged.sort(key=lambda r: (-_SOURCE_PRIORITY.get(r["source"], 0), r["score_rank"]))
    return merged[:limit]


def memocean_search(
    query: str,
    source: SourceType = "all",
    limit: int = 10,
) -> list[dict]:
    """
    Radar-First unified search facade.

    Default (source='all'): Radar sonar index + Messages. Ocean vault is opt-in.
    Results merged and ranked by source priority (ocean > radar > messages).

    Args:
        query:  3-5 keywords separated by spaces (not a question sentence).
                Example: 'project documentation' not 'what is the project about?'
        source: Which layer(s) to search:
                "all"      — Radar + Messages (default, no Ocean full-text scan)
                "ocean"    — Ocean vault .md files only (full-text ripgrep/walk)
                "radar"    — Radar sonar index only
                "messages" — Message history only
        limit:  Max results to return (default: 10).

    Returns:
        List of normalized result dicts with keys:
        title, excerpt, source, ref, score_rank, wikilink, path, drawer_path.
    """
    if not query or not query.strip():
        return []

    # Split keywords directly — caller (bot) is expected to pass keywords, not sentences.
    keywords = [t for t in query.split() if t.strip()] or [query.strip()]

    # Shared keyword string for ocean (regex OR pattern built inside ocean_search)
    keyword_query = " ".join(keywords)

    ocean_results: list[dict] = []
    radar_results: list[dict] = []
    messages_results: list[dict] = []

    if source == "ocean":
        try:
            from .ocean_search import ocean_search
            raw = ocean_search(keyword_query, limit=limit)
            ocean_results = _normalize_ocean(raw)
        except Exception as e:
            logger.debug("unified_search: ocean_search failed: %s", e)

    if source in ("radar", "all"):  # "all" = Radar-First (no Ocean full-text)
        try:
            from .radar_search import radar_search
            raw = radar_search(query, limit=limit)
            radar_results = _normalize_radar(raw)
        except Exception as e:
            logger.debug("unified_search: radar_search failed: %s", e)

    if source in ("messages", "all"):
        try:
            from .messages_hybrid_search import messages_hybrid_search
            raw = messages_hybrid_search(query, limit=limit)
            messages_results = _normalize_messages(raw)
        except Exception as e:
            logger.debug("unified_search: messages_hybrid_search failed: %s", e)

    return _merge_and_rank(ocean_results, radar_results, messages_results, limit)
