"""
radar_search.py — Search the radar table (CLSC sonar index) in memory.db.

Multi-term OR search: splits query on whitespace, matches rows containing at
least one term in the clsc column using instr() for Unicode safety. Results ranked by number of matching terms
(most matches first). Handles hyphenated slugs like 'Knowledge-Infra-ADR' that
LIKE exact-phrase would miss.

Reranker pipeline:
  - Hybrid recall: FTS5 BM25 top-20 + embedding KNN top-20 → merge dedup
  - Haiku LLM rerank → top-10 (primary)
  - MiniLM embedding rerank → top-10 (fallback if Haiku unavailable)

Returns list of dicts: slug, clsc, tokens, drawer_path, savings_pct (vs verbatim).
"""
import datetime
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

from ..config import FTS_DB

logger = logging.getLogger("memocean_mcp.radar_search")

_LOG_PATH = os.path.expanduser('~/.claude-bots/logs/clsc-usage.jsonl')

_EXPANSION_CACHE: dict[str, list[str]] = {}


def _has_cjk(text: str) -> bool:
    """Detect CJK characters (Chinese/Japanese/Korean) in text."""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\u20000-\u2a6df]', text))


def _update_last_accessed(slugs: list[str]) -> None:
    """Update last_accessed timestamp for slugs that appeared in search results."""
    if not slugs or not FTS_DB.exists():
        return
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        conn = sqlite3.connect(str(FTS_DB))
        # Check if column exists first (migration may not have run yet)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(radar)")}
        if "last_accessed" not in cols:
            conn.close()
            return
        conn.executemany(
            "UPDATE radar SET last_accessed=? WHERE slug=?",
            [(now, slug) for slug in slugs]
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _log_search(query: str, results: list[dict]) -> None:
    """Append one JSON line to the usage log. Swallows all exceptions."""
    try:
        bot = os.path.basename(os.environ.get('TELEGRAM_STATE_DIR', '')) or 'unknown'
        ts = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + \
             f"{datetime.datetime.now(datetime.timezone.utc).microsecond // 1000:03d}Z"

        sonar_tokens = sum(len(row.get('clsc', '') or '') // 4 for row in results)

        estimated_verbatim_tokens = 0
        for row in results:
            dp = row.get('drawer_path')
            if dp:
                try:
                    estimated_verbatim_tokens += os.path.getsize(dp) // 4
                except OSError:
                    pass  # file doesn't exist or unreadable — skip

        saved_tokens = estimated_verbatim_tokens - sonar_tokens
        saving_pct = round(saved_tokens / estimated_verbatim_tokens * 100, 1) \
            if estimated_verbatim_tokens > 0 else None

        entry = {
            'ts': ts,
            'event': 'radar_search',
            'bot': bot,
            'query': query,
            'hits': len(results),
            'sonar_tokens': sonar_tokens,
            'estimated_verbatim_tokens': estimated_verbatim_tokens,
            'saved_tokens': saved_tokens,
            'saving_pct': saving_pct,
        }

        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _escape_fts5_query(terms: list[str]) -> str:
    """Build an FTS5 OR query from terms, quoting each term for safety."""
    # Quote each term to avoid FTS5 syntax errors from special chars
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    return ' OR '.join(quoted)


def _search_fts5(conn: sqlite3.Connection, terms: list[str], limit: int) -> list[dict]:
    """Primary search via radar_fts with BM25 ranking."""
    fts_query = _escape_fts5_query(terms)
    sql = (
        "SELECT f.slug, c.clsc, c.tokens, c.drawer_path "
        "FROM radar_fts f "
        "JOIN radar c ON c.slug = f.slug "
        "WHERE radar_fts MATCH ? "
        "ORDER BY bm25(radar_fts) "
        "LIMIT ?"
    )
    rows = conn.execute(sql, (fts_query, limit)).fetchall()
    return [dict(r) for r in rows]


def _search_instr_fallback(conn: sqlite3.Connection, terms: list[str], limit: int) -> list[dict]:
    """Fallback: multi-term OR via instr() + match_count ranking."""
    case_exprs = " + ".join(
        "CASE WHEN instr(clsc, ?) > 0 THEN 1 ELSE 0 END" for _ in terms
    )
    sql = (
        f"SELECT slug, clsc, tokens, drawer_path, "
        f"({case_exprs}) AS match_count "
        f"FROM radar WHERE match_count >= 1 "
        f"ORDER BY match_count DESC LIMIT ?"
    )
    params = terms + [limit]
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _search_semantic(query: str, limit: int) -> list[dict]:
    """
    Pure semantic search via radar_vec (sqlite-vec KNN).
    Used when FTS5 and instr both return 0 results — the key path for
    Chinese queries where keywords don't overlap but meaning does.
    """
    try:
        from .reranker import (
            _embed_texts, _float_vec_to_blob, _load_sqlite_vec, _VEC_TABLE,
        )
    except ImportError:
        return []

    q_emb = _embed_texts([query])
    if q_emb is None:
        return []

    try:
        conn = sqlite3.connect(str(FTS_DB))
        conn.row_factory = sqlite3.Row

        if not _load_sqlite_vec(conn):
            conn.close()
            return []

        q_blob = _float_vec_to_blob(q_emb[0])

        # KNN search over pre-computed embeddings
        rows = conn.execute(
            f"SELECT slug, distance FROM {_VEC_TABLE} "
            f"WHERE embedding MATCH ? AND k = ?",
            (q_blob, limit),
        ).fetchall()

        if not rows:
            conn.close()
            return []

        # Fetch full radar data for matched slugs
        slugs = [r["slug"] if hasattr(r, "keys") else r[0] for r in rows]
        slug_dist = {
            (r["slug"] if hasattr(r, "keys") else r[0]): (
                r["distance"] if hasattr(r, "keys") else r[1]
            )
            for r in rows
        }
        placeholders = ",".join("?" for _ in slugs)
        radar_rows = conn.execute(
            f"SELECT slug, clsc, tokens, drawer_path FROM radar "
            f"WHERE slug IN ({placeholders})",
            slugs,
        ).fetchall()
        conn.close()

        # Sort by embedding distance (ascending = most similar first)
        results = [dict(r) for r in radar_rows]
        results.sort(key=lambda r: slug_dist.get(r["slug"], 999))
        return results

    except Exception:
        return []


_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_HAIKU_RECALL_LIMIT = 20  # candidates per source (FTS5 + embedding)
_anthropic_client = None  # lazy singleton


def _expand_query(query: str) -> list[str]:
    """
    Use Haiku to generate 3 semantically similar alternative queries.
    Returns list of expanded queries (including original). Cached per session.
    Falls back to [query] if Haiku unavailable.
    """
    if query in _EXPANSION_CACHE:
        return _EXPANSION_CACHE[query]
    if len(_EXPANSION_CACHE) > 500:
        _EXPANSION_CACHE.clear()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return [query]

    try:
        import anthropic  # noqa: F401
    except ImportError:
        return [query]

    prompt = f"""你是一個搜尋查詢擴展助手。給定一個查詢，生成 3 個語意相近的替代查詢（繁體中文），幫助找到相關文件。

原始查詢：{query}

輸出格式：每行一個替代查詢，只輸出查詢本身，不要編號或解釋。輸出 3 行。"""

    try:
        client = _get_anthropic_client()
        response = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=100,
            temperature=0.3,
            timeout=5.0,
            messages=[{"role": "user", "content": prompt}],
        )
        lines = [l.strip() for l in response.content[0].text.strip().splitlines() if l.strip()]
        expanded = [query] + lines[:3]
        _EXPANSION_CACHE[query] = expanded
        logger.debug("_expand_query: %r → %d variants", query, len(expanded))
        return expanded
    except Exception as e:
        logger.debug("_expand_query failed: %s", e)
        _EXPANSION_CACHE[query] = [query]
        return [query]


def _rrf_merge(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """
    Reciprocal Rank Fusion: merge multiple ranked lists by RRF score.
    score(d) = sum(1 / (k + rank_i(d))) across all lists where d appears.
    Returns merged list sorted by RRF score descending.
    """
    scores: dict[str, float] = {}
    by_slug: dict[str, dict] = {}

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            slug = item.get("slug", "")
            if not slug:
                continue
            scores[slug] = scores.get(slug, 0.0) + 1.0 / (k + rank)
            if slug not in by_slug:
                by_slug[slug] = item

    merged = sorted(by_slug.values(), key=lambda x: scores.get(x.get("slug", ""), 0), reverse=True)
    return merged


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _anthropic_client

_HAIKU_RERANK_PROMPT = """You are ranking entries from a compressed knowledge base (CLSC sonar format).
Each entry has a slug (hierarchical file path) and a compressed summary of the original document.

Slug structure hints:
- BOT-bots-{{name}}-CLAUDE: bot persona / role definition
- Chart-ADR-*: architectural decision records (why a decision was made)
- Chart-CLSC-*: CLSC technical specs and tests
- Ocean-Research-*: research reports and proposals
- Ocean-Currents-*: ongoing projects and status updates
- Wiki-Cards-*: reference cards and howtos

Rank by how well each entry answers the query's INTENT — not just keyword overlap.
Prefer entries that directly address what the query is asking about, even if exact words differ.

Query: {query}

Candidates (numbered 1-{n}):
{candidates}

Output only the ranked numbers separated by commas, most relevant first. Example: 3,1,5,2,4
Output numbers only, no other text."""


def _format_haiku_candidates(candidates: list[dict]) -> str:
    """Format candidate list for Haiku prompt."""
    lines = []
    for i, c in enumerate(candidates, 1):
        slug = c.get("slug", "")
        clsc = (c.get("clsc") or "")[:200]  # truncate long sonar entries
        lines.append(f"{i}. [{slug}] {clsc}")
    return "\n".join(lines)


def _parse_haiku_response(response_text: str, n_candidates: int) -> list[int]:
    """
    Parse Haiku's comma-separated response into 0-based indices.
    Expected: "3,1,5,2,4" (1-based). Returns 0-based indices.
    Appends any missing indices at the end (original order).
    """
    numbers = re.findall(r'\d+', response_text)
    seen: set[int] = set()
    indices: list[int] = []
    for num_str in numbers:
        num = int(num_str)
        if 1 <= num <= n_candidates and num not in seen:
            seen.add(num)
            indices.append(num - 1)
    # Append missing indices in original order
    for i in range(n_candidates):
        if i not in seen:
            indices.append(i)
    return indices


def _haiku_rerank(query: str, candidates: list[dict], top_k: int) -> list[dict] | None:
    """
    Call Haiku to rerank candidates by relevance to the query.
    Returns reranked+truncated list, or None if Haiku unavailable.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.debug("haiku_rerank: ANTHROPIC_API_KEY not set, skipping")
        return None

    if not candidates:
        return candidates

    try:
        import anthropic  # noqa: F401 — ensure package is available
    except ImportError:
        logger.debug("haiku_rerank: anthropic package not installed, skipping")
        return None

    n = len(candidates)
    prompt = _HAIKU_RERANK_PROMPT.format(
        n=n,
        query=query,
        candidates=_format_haiku_candidates(candidates),
    )

    try:
        client = _get_anthropic_client()
        response = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=300,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = response.content[0].text.strip()
        reranked_indices = _parse_haiku_response(response_text, n)
        result = [candidates[i] for i in reranked_indices[:top_k]]
        logger.info(
            "haiku_rerank: %d→%d candidates (input_tokens=%d output_tokens=%d)",
            n, len(result),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return result
    except Exception as e:
        logger.warning("haiku_rerank: API call failed: %s", e)
        return None


def _merge_candidates(fts_results: list[dict], sem_results: list[dict]) -> list[dict]:
    """
    Merge FTS5 and semantic candidates using RRF (Reciprocal Rank Fusion).
    score(d) = sum(1 / (k + rank_i(d))), k=60 (standard constant).
    Adds 'sources' field: list of retrieval paths that returned each doc
    (e.g. ["fts"], ["sem"], or ["fts", "sem"] for cross-path hits).
    """
    if not fts_results and not sem_results:
        return []

    # Track which retrieval paths each slug appeared in (before RRF modifies dicts)
    sources_map: dict[str, list[str]] = {}
    for row in fts_results:
        slug = row.get("slug", "")
        if slug:
            sources_map.setdefault(slug, []).append("fts")
    for row in sem_results:
        slug = row.get("slug", "")
        if slug:
            sources_map.setdefault(slug, []).append("sem")

    # RRF merge across both ranked lists
    lists = [lst for lst in [fts_results, sem_results] if lst]
    merged = _rrf_merge(lists)

    # Attach sources metadata to each result
    for item in merged:
        item["sources"] = sources_map.get(item.get("slug", ""), [])

    return merged


def radar_search(query: str, limit: int = 10) -> list[dict]:
    """
    Search CLSC sonar index in the radar table.

    Recall pipeline (hybrid):
      - Chinese query: FTS5 BM25 instr-OR top-20 + embedding KNN top-20 → merge
      - English query: FTS5 BM25 top-20 + embedding KNN top-20 → merge

    Rerank (in priority order):
      1. Haiku LLM reranker (if ANTHROPIC_API_KEY available) → top-K
      2. MiniLM embedding reranker (fallback) → top-K
      3. Raw BM25 order (final fallback) → truncate to limit

    Returns list of dicts with keys: slug, clsc, tokens, drawer_path.
    """
    if not query or not query.strip():
        return []

    if not FTS_DB.exists():
        return []

    terms = [t.strip() for t in query.split() if t.strip()]
    if not terms:
        return []

    cjk_query = _has_cjk(query)

    # --- Multi-Query Expansion (if API available and not disabled) ---
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    expansion_disabled = bool(os.environ.get("DISABLE_QUERY_EXPANSION"))
    if api_key and _has_cjk(query) and not expansion_disabled:
        # Only expand CJK queries — English FTS5 already handles variations well
        expanded_queries = _expand_query(query)
    else:
        expanded_queries = [query]

    # --- Keyword recall (multi-query) ---
    all_keyword_results: list[list[dict]] = []
    try:
        conn = sqlite3.connect(str(FTS_DB))
        conn.row_factory = sqlite3.Row

        for eq in expanded_queries:
            eq_terms = [t.strip() for t in eq.split() if t.strip()]
            if not eq_terms:
                continue
            if cjk_query:
                eq_results = _search_instr_fallback(conn, eq_terms, _HAIKU_RECALL_LIMIT)
            else:
                try:
                    eq_results = _search_fts5(conn, eq_terms, _HAIKU_RECALL_LIMIT)
                except sqlite3.OperationalError:
                    eq_results = []
                if not eq_results:
                    eq_results = _search_instr_fallback(conn, eq_terms, _HAIKU_RECALL_LIMIT)
            if eq_results:
                all_keyword_results.append(eq_results)

        conn.close()
    except sqlite3.OperationalError:
        return []

    # RRF merge if multiple query results, otherwise use single list
    if len(all_keyword_results) > 1:
        keyword_results = _rrf_merge(all_keyword_results)[:_HAIKU_RECALL_LIMIT]
    elif all_keyword_results:
        keyword_results = all_keyword_results[0]
    else:
        keyword_results = []

    # --- Semantic (embedding KNN) recall ---
    sem_results: list[dict] = []
    try:
        from .reranker import is_available as reranker_available
        use_reranker = reranker_available()
    except Exception:
        use_reranker = False

    if use_reranker:
        try:
            sem_results = _search_semantic(query, _HAIKU_RECALL_LIMIT)
        except Exception:
            sem_results = []

    # --- Merge hybrid candidates ---
    if keyword_results or sem_results:
        merged = _merge_candidates(keyword_results, sem_results)
    else:
        # Both recall paths returned nothing — return empty
        _log_search(query, [])
        return []

    if not merged:
        _log_search(query, [])
        return []

    # --- Rerank: Haiku first, MiniLM fallback ---
    # Both rerankers are disabled by default — benchmarks show BM25 RRF ordering
    # outperforms both on this corpus. Enable with ENABLE_HAIKU_RERANKER=1 or
    # ENABLE_MINIML_RERANKER=1.
    if len(merged) > 1:
        # Attempt Haiku LLM rerank (only if explicitly enabled)
        haiku_result = _haiku_rerank(query, merged, top_k=limit) \
            if os.environ.get("ENABLE_HAIKU_RERANKER") else None
        if haiku_result is not None:
            results = haiku_result
        elif use_reranker and os.environ.get("ENABLE_MINIML_RERANKER"):
            # MiniLM embedding reranker (only if explicitly enabled)
            try:
                from .reranker import rerank
                results = rerank(query, merged, top_k=limit)
            except Exception:
                results = merged[:limit]
        else:
            results = merged[:limit]
    else:
        results = merged[:limit]

    _log_search(query, results)
    # Update last_accessed for returned slugs
    if results:
        try:
            _update_last_accessed([r["slug"] for r in results if r.get("slug")])
        except Exception:
            pass
    return results
