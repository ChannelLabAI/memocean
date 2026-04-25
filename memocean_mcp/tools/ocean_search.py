"""
ocean_search.py — Full-text search over Ocean vault .md files.

Primary backend: GBrain hybrid (BM25 + Gemini vector + RRF) when
  MEMOCEAN_USE_GBRAIN=true and health probe passes at startup.
Fallback: Python os.walk + re.search (always available, CJK-safe).
Fast path (fallback): ripgrep if available on PATH.

Feature flag: MEMOCEAN_USE_GBRAIN=true|false (default: false)
  False  → legacy BM25/ripgrep path (unchanged behaviour)
  True   → GBrain subprocess delegate; any failure → silent fallback

Key design choices:
  - Only searches Ocean/ (never OldRabbit/ or other personal vaults)
  - GBrain subprocess timeout = 3s (BLOCKER-1)
  - Startup health probe: gbrain --version (1s timeout); fails → flag forced OFF
  - Response includes backward-compat fields (title, wikilink, excerpt) alongside
    new fields (slug, content, score) so existing LLM prompts stay stable (ARCH-2)
  - spec v1 used --json flag which gbrain 0.14.1 doesn't support; implementation
    parses plain-text CLI output [score] slug -- content instead (ARCH-1 fix)
"""
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Optional

from ..config import MEMOCEAN_VAULT_PATH

logger = logging.getLogger("memocean_mcp.ocean_search")

OCEAN_PATH = str(MEMOCEAN_VAULT_PATH)

# Known vendor rg paths (Claude Code ships its own ripgrep)
_RG_CANDIDATES = [
    shutil.which("rg"),
    "/usr/local/lib/node_modules/@anthropic-ai/claude-code/vendor/ripgrep/x64-linux/rg",
    "/usr/bin/rg",
    "/usr/local/bin/rg",
]

GBRAIN_SUBPROCESS_TIMEOUT_S = 3.0
GBRAIN_EXPECTED_EXIT_CODE = 0
GBRAIN_BIN = shutil.which("gbrain") or os.path.expanduser("~/.bun/bin/gbrain")

# Module-level flag: set to False if health probe fails at import time
_gbrain_healthy: bool = False


class GBrainUnhealthy(Exception):
    pass


# ---- Startup health probe (called once at module load) ----

def _run_health_probe() -> bool:
    """
    BLOCKER-1: Called at module import. Returns False → GBrain flag forced OFF.
    Uses a short timeout (1s) to avoid blocking MCP startup.
    """
    if not GBRAIN_BIN or not os.path.isfile(GBRAIN_BIN):
        logger.warning("GBrain binary not found at %s — health probe FAIL", GBRAIN_BIN)
        return False
    try:
        r = subprocess.run(
            [GBRAIN_BIN, "--version"],
            capture_output=True, timeout=1.0,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


_gbrain_healthy = _run_health_probe()
if not _gbrain_healthy:
    logger.warning("GBrain health probe failed — MEMOCEAN_USE_GBRAIN will be ignored")


# ---- GBrain plain-text output parser ----

def _parse_gbrain_output(stdout: str) -> list[dict]:
    """
    Parse gbrain query plain-text output format:
      [score] slug -- content (may be multi-line until next [score] slug --)

    spec v1 assumed --json flag; gbrain 0.14.1 has no such flag and silently
    ignores it, returning plain text. This parser handles the actual format.
    """
    # Split on each new result header: [float] slug --
    parts = re.split(r'\[(\d+(?:\.\d+)?)\]\s+(\S+)\s+--\s*', stdout)
    # parts = ['', score1, slug1, content1, score2, slug2, content2, ...]
    results = []
    i = 1
    while i + 2 <= len(parts):
        score_str = parts[i]
        slug = parts[i + 1]
        content = parts[i + 2].strip()[:2000]
        try:
            score = float(score_str)
        except ValueError:
            score = 0.0
        results.append({"_score": score, "_slug": slug, "_content": content})
        i += 3
    return results


def _normalize_gbrain(raw: list[dict]) -> list[dict]:
    """Convert parsed GBrain results to the response schema."""
    from ..slug_mapper import slug_to_path, slug_to_display_name
    out = []
    for r in raw:
        slug = r["_slug"]
        content = r["_content"]
        score = r["_score"]

        abs_path = slug_to_path(slug)
        rel_path = abs_path or slug  # fallback: use slug as path indicator

        # Backward-compat fields (ARCH-2: keeps LLM prompts stable)
        first_line = content.splitlines()[0] if content else ""
        title = re.sub(r'^#+\s*', '', first_line).strip() or slug.split("/")[-1]
        display = slug_to_display_name(slug)

        out.append({
            # New schema fields
            "slug": slug,
            "content": content,
            "score": score,
            "path": rel_path,
            "source": "gbrain",
            # Backward-compat fields (Phase 2: deprecate)
            "title": title,
            "wikilink": f"[[{display}]]",
            "excerpt": content[:200],
        })
    return out


# ---- GBrain delegate ----

def _gbrain_search(query: str, limit: int) -> list[dict]:
    """
    Subprocess call to gbrain CLI. Must complete in ≤ GBRAIN_SUBPROCESS_TIMEOUT_S.

    Raises:
      GBrainUnhealthy  — exit code != 0 or empty output
      subprocess.TimeoutExpired — exceeded timeout
      (json.JSONDecodeError is no longer raised — we parse plain text)
    """
    result = subprocess.run(
        [GBRAIN_BIN, "query", query, "--limit", str(limit)],
        capture_output=True,
        timeout=GBRAIN_SUBPROCESS_TIMEOUT_S,
        text=True,
        check=False,
    )
    if result.returncode != GBRAIN_EXPECTED_EXIT_CODE:
        raise GBrainUnhealthy(
            f"exit={result.returncode} stderr={result.stderr[:500]}"
        )
    parsed = _parse_gbrain_output(result.stdout)
    if not result.stdout.strip():
        # Empty output is not an error — just no results
        return []
    return _normalize_gbrain(parsed)


# ---- Legacy BM25 / ripgrep path (unchanged) ----

def _find_rg() -> Optional[str]:
    for p in _RG_CANDIDATES:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _search_via_rg(pattern: str, ocean_path: str, limit: int) -> list[dict]:
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
            match_byte_start = submatches[0].get("start", 0)
            line_bytes = lines_text.encode("utf-8")
            ctx_start = max(0, match_byte_start - 100)
            ctx_end = min(len(line_bytes), match_byte_start + 300)
            excerpt = line_bytes[ctx_start:ctx_end].decode("utf-8", errors="replace").strip()[:200]
        else:
            excerpt = lines_text.strip()[:200]

        results.append({
            "slug": rel_path.replace(os.sep, "/").lower().removesuffix(".md"),
            "content": excerpt,
            "score": 0.0,
            "path": rel_path,
            "source": "bm25",
            "title": title,
            "wikilink": f"[[{title}]]",
            "excerpt": excerpt,
        })
        if len(results) >= limit:
            break

    return results


def _search_via_python(pattern_str: str, ocean_path: str, limit: int) -> list[dict]:
    try:
        rx = re.compile(pattern_str, re.IGNORECASE)
    except re.error:
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

            start = max(0, m.start() - 100)
            end = min(len(content), m.end() + 100)
            excerpt = content[start:end].strip()[:200]

            title = fname[:-3] if fname.endswith(".md") else fname
            try:
                rel_path = os.path.relpath(abs_path, ocean_path)
            except ValueError:
                rel_path = abs_path

            results.append({
                "slug": rel_path.replace(os.sep, "/").lower().removesuffix(".md"),
                "content": excerpt,
                "score": 0.0,
                "path": rel_path,
                "source": "bm25",
                "title": title,
                "wikilink": f"[[{title}]]",
                "excerpt": excerpt,
            })
            if len(results) >= limit:
                return results

    return results


def _legacy_bm25_search(query: str, limit: int) -> list[dict]:
    """BM25/ripgrep fallback — identical to original ocean_search behaviour."""
    if not os.path.isdir(OCEAN_PATH):
        return []
    terms = [t for t in query.split() if t.strip()] or [query.strip()]
    pattern = "|".join(re.escape(t) for t in terms)

    try:
        results = _search_via_rg(pattern, OCEAN_PATH, limit)
        if results or _find_rg():
            return results
    except Exception as e:
        logger.debug("ocean_search rg path failed: %s", e)

    return _search_via_python(pattern, OCEAN_PATH, limit)


# ---- Public API ----

def ocean_search(query: str, limit: int = 10) -> list[dict]:
    """
    Search Ocean vault .md files.

    Routes to GBrain hybrid search when MEMOCEAN_USE_GBRAIN=true and
    health probe passed. Falls back to BM25 on any GBrain failure.

    Returns up to `limit` dicts:
      slug, content, score, path, source  (new schema)
      title, wikilink, excerpt            (backward-compat, Phase 2 deprecate)
    """
    if not query or not query.strip():
        return []

    use_gbrain = (
        os.getenv("MEMOCEAN_USE_GBRAIN", "false").lower() == "true"
        and _gbrain_healthy
    )

    if use_gbrain:
        try:
            results = _gbrain_search(query, limit)
            logger.debug("ocean_search (gbrain): %r → %d results", query, len(results))
            return results
        except (subprocess.TimeoutExpired, GBrainUnhealthy) as e:
            logger.warning("GBrain delegate failed (%s): %s — falling back", type(e).__name__, e)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            logger.warning("GBrain subprocess error (%s): %s — falling back", type(e).__name__, e)
        except Exception as e:
            logger.exception("GBrain unexpected failure: %s — falling back", e)

    results = _legacy_bm25_search(query, limit)
    logger.debug("ocean_search (bm25): %r → %d results", query, len(results))
    return results
