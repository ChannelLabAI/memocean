"""
ocean_search.py — Full-text search over Ocean vault .md files.

Primary: Python os.walk + re.search (always available, CJK-safe).
Fast path: ripgrep if available on PATH (tried via shutil.which).

Key design choices:
  - Only searches Ocean/ (never OldRabbit/ or other personal vaults)
  - Uses query_expand() for keyword extraction, same as other search tools
  - Falls back to raw query split when expansion unavailable
  - Returns [] (not raises) when Ocean path doesn't exist

Result schema (each dict):
  title    — filename without .md extension
  wikilink — [[title]] format for Obsidian linking
  excerpt  — ~200 chars of matching text
  path     — relative path from Ocean root (e.g. "Chart/MemOcean/MemOcean.md")
  source   — always "ocean"
"""
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger("memocean_mcp.ocean_search")

OCEAN_PATH = os.path.expanduser("~/Documents/Obsidian Vault/Ocean/")

# Known vendor rg paths (Claude Code ships its own ripgrep)
_RG_CANDIDATES = [
    shutil.which("rg"),
    "/usr/local/lib/node_modules/@anthropic-ai/claude-code/vendor/ripgrep/x64-linux/rg",
    "/usr/bin/rg",
    "/usr/local/bin/rg",
]


def _find_rg() -> Optional[str]:
    """Return path to a working ripgrep binary, or None."""
    for p in _RG_CANDIDATES:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _search_via_rg(pattern: str, ocean_path: str, limit: int) -> list[dict]:
    """Fast path: use ripgrep to find matches."""
    rg = _find_rg()
    if not rg:
        return []

    try:
        proc = subprocess.run(
            [rg, "--json", "-i", "-m", "1", "-g", "*.md", pattern, ocean_path],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("ocean_search rg error: %s", e)
        return []

    results = []
    seen: set[str] = set()

    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue

        abs_path = obj.get("data", {}).get("path", {}).get("text", "")
        if not abs_path or abs_path in seen:
            continue
        seen.add(abs_path)

        basename = os.path.basename(abs_path)
        title = basename[:-3] if basename.endswith(".md") else basename
        try:
            rel_path = os.path.relpath(abs_path, ocean_path)
        except ValueError:
            rel_path = abs_path

        lines_text = obj.get("data", {}).get("lines", {}).get("text", "")
        submatches = obj.get("data", {}).get("submatches", [])
        if submatches and lines_text:
            # Use byte offset to center excerpt around the match
            match_byte_start = submatches[0].get("start", 0)
            line_bytes = lines_text.encode("utf-8")
            ctx_start = max(0, match_byte_start - 100)
            ctx_end = min(len(line_bytes), match_byte_start + 300)
            excerpt = line_bytes[ctx_start:ctx_end].decode("utf-8", errors="replace").strip()[:200]
        else:
            excerpt = lines_text.strip()[:200]

        results.append({
            "title": title,
            "wikilink": f"[[{title}]]",
            "excerpt": excerpt,
            "path": rel_path,
            "source": "ocean",
        })
        if len(results) >= limit:
            break

    return results


def _search_via_python(pattern_str: str, ocean_path: str, limit: int) -> list[dict]:
    """
    Pure Python fallback: os.walk + re.search.
    pattern_str is an alternation regex built from keywords.
    """
    try:
        rx = re.compile(pattern_str, re.IGNORECASE)
    except re.error:
        # If pattern is bad, match literally
        rx = re.compile(re.escape(pattern_str), re.IGNORECASE)

    results = []

    for dirpath, _dirs, filenames in os.walk(ocean_path):
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fname)
            try:
                with open(abs_path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue

            m = rx.search(content)
            if not m:
                continue

            # Build excerpt: up to 100 chars before + match + up to 100 chars after
            start = max(0, m.start() - 100)
            end = min(len(content), m.end() + 100)
            excerpt = content[start:end].strip()[:200]

            title = fname[:-3] if fname.endswith(".md") else fname
            try:
                rel_path = os.path.relpath(abs_path, ocean_path)
            except ValueError:
                rel_path = abs_path

            results.append({
                "title": title,
                "wikilink": f"[[{title}]]",
                "excerpt": excerpt,
                "path": rel_path,
                "source": "ocean",
            })
            if len(results) >= limit:
                return results

    return results


def ocean_search(query: str, limit: int = 10) -> list[dict]:
    """
    Search Ocean vault .md files for the given query.

    Returns up to `limit` dicts with title/wikilink/excerpt/path/source.
    Returns [] (no crash) when Ocean path missing or on any error.
    """
    if not query or not query.strip():
        return []

    if not os.path.isdir(OCEAN_PATH):
        logger.debug("ocean_search: Ocean vault not found at %s", OCEAN_PATH)
        return []

    # Build search terms via query_expand
    try:
        from .query_expand import query_expand
        terms = query_expand(query)
    except Exception:
        terms = [t for t in query.split() if t.strip()]
    if not terms:
        terms = [query.strip()]

    # Build regex pattern: any term (OR)
    pattern = "|".join(re.escape(t) for t in terms)

    # Try ripgrep fast path first
    try:
        results = _search_via_rg(pattern, OCEAN_PATH, limit)
        if results or _find_rg():
            # rg ran successfully (even if 0 results)
            logger.debug("ocean_search (rg): %r → %d results", query, len(results))
            return results
    except Exception as e:
        logger.debug("ocean_search rg path failed: %s", e)

    # Python fallback
    results = _search_via_python(pattern, OCEAN_PATH, limit)
    logger.debug("ocean_search (py): %r → %d results", query, len(results))
    return results
