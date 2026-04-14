"""
query_expand.py — Haiku-powered keyword extraction for MemOcean search.

Converts natural language queries into 3-6 search keywords:
  "CHL 現在在推什麼" → ["CHL", "ChannelLab", "GEO", "服務", "推廣"]

Used by both seabed_search and messages_hybrid_search as a BM25 pre-processing
step. KNN (embedding) paths still receive the original natural-language query.

Failure modes:
  - ANTHROPIC_API_KEY not set → returns whitespace-split terms
  - Haiku call fails / timeout → returns whitespace-split terms (no crash)
  - Response not valid JSON → returns whitespace-split terms

Cache: in-memory per process, max 500 entries.
"""
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("memocean_mcp.query_expand")

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_KEYWORD_CACHE: dict[str, list[str]] = {}
_anthropic_client = None


def _get_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "")
        )
    return _anthropic_client


_EXPAND_PROMPT = """把以下問句改寫成 3-6 個搜尋關鍵字（繁中/英混合），以 JSON array 回傳。
只輸出 JSON array，不要任何說明文字。

範例輸入：CHL 現在在推什麼業務？
範例輸出：["ChannelLab","CHL","GEO","服務","推廣"]

輸入：{query}
輸出："""


def query_expand(query: str) -> list[str]:
    """
    Extract 3-6 search keywords from a natural language query via Haiku.

    Returns list[str] of keywords. Falls back to whitespace-split terms
    if Haiku unavailable or call fails. Never raises.
    """
    if not query or not query.strip():
        return []

    query = query.strip()
    fallback = [t for t in query.split() if t.strip()] or [query]

    # Cache hit
    if query in _KEYWORD_CACHE:
        return _KEYWORD_CACHE[query]
    if len(_KEYWORD_CACHE) >= 500:
        _KEYWORD_CACHE.clear()

    # No API key → skip expansion
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return fallback

    try:
        import anthropic  # noqa: F401 — availability check
    except ImportError:
        return fallback

    # Env override: ENABLE_KEYWORD_EXPANSION=false to disable
    expansion_flag = os.environ.get("ENABLE_KEYWORD_EXPANSION", "true").lower()
    if expansion_flag in ("false", "0", "no"):
        return fallback

    try:
        client = _get_client()
        response = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=80,
            temperature=0.1,
            timeout=4.0,
            messages=[{
                "role": "user",
                "content": _EXPAND_PROMPT.format(query=query),
            }],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if Haiku wraps in ```json ... ```
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(
                l for l in lines
                if not l.strip().startswith("```")
            ).strip()
        keywords = json.loads(raw)
        if not isinstance(keywords, list):
            raise ValueError("not a list")
        keywords = [str(k).strip() for k in keywords if str(k).strip()]
        if not keywords:
            raise ValueError("empty list")
        # Cap at 8 keywords
        keywords = keywords[:8]
        _KEYWORD_CACHE[query] = keywords
        logger.debug("query_expand: %r → %s", query, keywords)
        return keywords
    except Exception as e:
        logger.debug("query_expand: failed for %r: %s — using fallback", query, e)
        _KEYWORD_CACHE[query] = fallback
        return fallback


def keywords_to_fts_or(keywords: list[str]) -> str:
    """
    Build an FTS5 OR query string from a keyword list.

    Each keyword is quoted to handle special chars.
    Example: ["ChannelLab", "GEO", "服務"] → '"ChannelLab" OR "GEO" OR "服務"'
    """
    if not keywords:
        return ""
    quoted = ['"' + k.replace('"', '""') + '"' for k in keywords]
    return " OR ".join(quoted)
