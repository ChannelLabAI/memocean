"""
slug_mapper.py — Bidirectional Ocean vault path ↔ GBrain slug mapping.

Slug convention (Phase 1 spec section 6):
  Ocean/技術海圖/Bot System/MemOcean.md  ->  chart/bot-system/memocean
  Ocean/珍珠卡/old-notes.md            ->  pearl/old-notes
  Ocean/珍珠卡/2026-04-20 v1.2.md      ->  pearl/2026-04-20-v1.2

Rules:
  1. Strip "Ocean/" prefix
  2. Lowercase + spaces->hyphens (per segment)
  3. Strip known extensions (.md .canvas .base) - only last extension
  4. Preserve directory separators as "/"
  5. Shell-unsafe chars (/ ?) -> "-" ; other unicode preserved

Alias table (slug_alias_map.json) persisted for CLAUDE.md symlink slugs.
"""
import json
import os
import re
from pathlib import Path

from .config import MEMOCEAN_VAULT_PATH

OCEAN_VAULT_ROOT = MEMOCEAN_VAULT_PATH
ALIAS_MAP_PATH = Path(__file__).parent.parent / "slug_alias_map.json"

_KNOWN_EXTS = {".md", ".canvas", ".base"}
_SHELL_UNSAFE = re.compile(r'[/\\?]')


def path_to_slug(abs_path: str | Path) -> str:
    """Convert an absolute Ocean vault path to a GBrain slug."""
    p = Path(abs_path)
    try:
        rel = p.relative_to(OCEAN_VAULT_ROOT)
    except ValueError:
        # Not under Ocean vault — return a safe fallback slug
        return re.sub(r'[^a-z0-9/_\-\u4e00-\u9fff]', '-', str(p).lower())

    parts = list(rel.parts)
    # Strip known extension from last part
    last = parts[-1] if parts else ""
    stem, ext = os.path.splitext(last)
    if ext.lower() in _KNOWN_EXTS:
        parts[-1] = stem

    segments = []
    for part in parts:
        # spaces → hyphens, shell-unsafe → hyphens, lowercase ASCII
        seg = part.replace(" ", "-")
        seg = _SHELL_UNSAFE.sub("-", seg)
        seg = seg.lower()
        # collapse multiple hyphens
        seg = re.sub(r'-{2,}', '-', seg)
        segments.append(seg)

    return "/".join(segments)


def slug_to_path(slug: str) -> str | None:
    """
    Best-effort reverse mapping: slug → absolute filesystem path.
    Checks alias table first, then attempts filesystem walk.
    Returns None if not found.
    """
    # Check alias table
    alias = _load_alias_map()
    if slug in alias:
        return alias[slug]

    # Attempt direct reconstruction: slug segments → mixed-case walk
    return _walk_slug(slug)


def slug_to_display_name(slug: str) -> str:
    """Return a human-friendly display name from a slug (for wikilink generation)."""
    last = slug.split("/")[-1] if "/" in slug else slug
    # Restore spaces in ASCII-only segments (hyphens back to spaces)
    # Don't touch CJK segments — they were never converted
    if re.search(r'[\u4e00-\u9fff]', last):
        return last
    return last.replace("-", " ").title()


def _walk_slug(slug: str) -> str | None:
    """Walk Ocean vault to find a file matching the slug."""
    if not OCEAN_VAULT_ROOT.exists():
        return None

    target_slug = slug.lower()
    for root, _dirs, files in os.walk(OCEAN_VAULT_ROOT):
        for fname in files:
            abs_path = os.path.join(root, fname)
            candidate = path_to_slug(abs_path)
            if candidate == target_slug:
                return abs_path
    return None


def register_alias(slug: str, abs_path: str) -> None:
    """Persist a slug→path alias (for CLAUDE.md symlink slugs, etc.)."""
    alias = _load_alias_map()
    alias[slug] = abs_path
    ALIAS_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ALIAS_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(alias, f, ensure_ascii=False, indent=2)


def _load_alias_map() -> dict[str, str]:
    if not ALIAS_MAP_PATH.exists():
        return {}
    try:
        with open(ALIAS_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
