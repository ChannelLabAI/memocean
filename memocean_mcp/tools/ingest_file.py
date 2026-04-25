"""
ingest_file.py — MemOcean file ingest via MarkItDown.

Converts local files (PDF/PPT/Word/Excel/HTML/CSV/JSON) to markdown
and stores in MemOcean radar (group='files').

Error codes:
  FILE_NOT_FOUND    — path does not exist
  FILE_TOO_LARGE    — file > 50 MB
  MARKITDOWN_FAIL   — MarkItDown conversion raised an exception
  EMPTY_CONTENT     — converted content < 100 chars
"""
import hashlib
import logging
import re
import sqlite3
from pathlib import Path

from ..config import FTS_DB

logger = logging.getLogger("memocean_mcp.ingest_file")

_MAX_FILE_BYTES = 50 * 1024 * 1024   # 50 MB
_MAX_CONTENT_CHARS = 50_000
_MIN_CONTENT_CHARS = 100
_GROUP = "files"  # fallback / legacy; new ingests use _classify_group()

_EXT_TO_GROUP: dict[str, str] = {
    ".pdf": "docs-pdf",
    ".md": "docs-spec",
    ".txt": "docs-spec",
    ".docx": "docs-spec",
    ".doc": "docs-spec",
}
_RELEASE_NOTE_KEYWORDS = ("release", "changelog", "relnote", "release-note")
_SPEC_KEYWORDS = ("spec", "design", "rfc", "proposal", "plan", "sop")


def _classify_group(path: Path) -> str:
    """Classify file into Seabed radar group based on extension and filename keywords."""
    stem_lower = path.stem.lower()
    ext = path.suffix.lower()
    if any(kw in stem_lower for kw in _RELEASE_NOTE_KEYWORDS):
        return "docs-release-note"
    if ext == ".pdf":
        return "docs-pdf"
    if ext in (".md", ".txt", ".docx", ".doc"):
        if any(kw in stem_lower for kw in _SPEC_KEYWORDS):
            return "docs-spec"
        return "docs-spec"
    return "raw"


# ── slug helper ──────────────────────────────────────────────────────────────

def _make_slug(path: Path) -> str:
    """Build slug: file:{stem}-{hash6}, stem ≤40 chars, lowercase, spaces→-.

    Uses last 6 hex chars of MD5(abs path) instead of date so that
    the slug is stable across days and dedup works by identity, not time.
    """
    stem = path.stem.lower()
    stem = re.sub(r"[^a-z0-9\-_]", "-", stem.replace(" ", "-"))
    stem = re.sub(r"-{2,}", "-", stem).strip("-")
    stem = stem[:40]
    path_hash = hashlib.md5(str(path).encode()).hexdigest()[-6:]
    return f"file:{stem}-{path_hash}"


# ── seabed store_from_string ─────────────────────────────────────────────────

def _get_clsc_path() -> str:
    """Return path to clsc module directory (via config SHARED_ROOT)."""
    from ..config import SHARED_ROOT
    return str(SHARED_ROOT / "memocean-mcp" / "clsc")


def store_from_string(content: str, slug: str, group: str = _GROUP) -> int:
    """
    Store content string directly into radar + radar_fts, bypassing encode_note().
    Returns the radar table rowid.
    Dedup: UPDATE if slug already exists (same path re-ingest).
    """
    import sys
    clsc_path = _get_clsc_path()
    if clsc_path not in sys.path:
        sys.path.insert(0, clsc_path)
    from radar import store_sonar  # writes to .clsc.md file + radar DB table

    # store_sonar handles file + DB upsert
    store_sonar(group, slug, content)

    # MEMO-011: best-effort summary generation
    try:
        from .insert_row import generate_and_store_summary
        generate_and_store_summary(slug, content, "")  # clsc not yet available at insert time
    except Exception:
        pass

    # Return rowid
    conn = sqlite3.connect(str(FTS_DB))
    try:
        row = conn.execute("SELECT rowid FROM radar WHERE slug=?", (slug,)).fetchone()
        return row[0] if row else -1
    finally:
        conn.close()


# ── dedup by drawer_path ─────────────────────────────────────────────────────

def _upsert_by_path(slug: str, content: str, drawer_path: str, tokens: int, group: str = _GROUP) -> int:
    """
    If a radar row already has this drawer_path, UPDATE slug+clsc+tokens AND
    call store_sonar() to sync the .clsc.md file.
    Otherwise, returns None so store_from_string handles INSERT via store_sonar.
    Returns rowid.
    """
    conn = sqlite3.connect(str(FTS_DB))
    try:
        existing = conn.execute(
            "SELECT slug, rowid FROM radar WHERE drawer_path=?", (drawer_path,)
        ).fetchone()
        if existing:
            old_slug = existing[0]
            # UPDATE existing DB row
            conn.execute(
                "UPDATE radar SET slug=?, clsc=?, tokens=?, drawer_path=?, "
                "source_hash=?, encoded_at=CURRENT_TIMESTAMP WHERE drawer_path=?",
                (slug, content, tokens, drawer_path,
                 hashlib.md5(content.encode()).hexdigest(), drawer_path),
            )
            # Sync radar_fts
            conn.execute("DELETE FROM radar_fts WHERE slug=?", (old_slug,))
            conn.execute(
                "INSERT INTO radar_fts(slug, clsc) VALUES (?, ?)", (slug, content)
            )
            conn.commit()
            row = conn.execute("SELECT rowid FROM radar WHERE slug=?", (slug,)).fetchone()
            rowid = row[0] if row else -1
            conn.close()
            # Also update .clsc.md file via store_sonar
            import sys
            clsc_path = _get_clsc_path()
            if clsc_path not in sys.path:
                sys.path.insert(0, clsc_path)
            from radar import store_sonar
            store_sonar(group, slug, content)
            # MEMO-011: best-effort summary generation
            try:
                from .insert_row import generate_and_store_summary
                generate_and_store_summary(slug, content, "")  # clsc not yet available at insert time
            except Exception:
                pass
            return rowid
        return None  # no existing row — let store_from_string handle INSERT
    finally:
        try:
            conn.close()
        except Exception:
            pass  # already closed in the existing-row branch


# ── main tool function ────────────────────────────────────────────────────────

def ingest_file(file_path: str) -> dict:
    """
    Ingest a local file into MemOcean via MarkItDown.

    Args:
        file_path: Absolute or ~-expanded path to the file.

    Returns:
        Success: {"slug", "group", "chars", "radar_id", "format", "truncated"}
        Error:   {"error", "code"}
    """
    path = Path(file_path).expanduser().resolve()

    # 1. File exists?
    if not path.exists():
        return {"error": f"File not found: {path}", "code": "FILE_NOT_FOUND"}

    # 2. File size ≤ 50 MB?
    file_size = path.stat().st_size
    if file_size > _MAX_FILE_BYTES:
        mb = file_size / 1024 / 1024
        return {
            "error": f"File too large: {mb:.1f} MB (max 50 MB)",
            "code": "FILE_TOO_LARGE",
        }

    # 3. Convert with MarkItDown
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(path))
        content = result.text_content
    except Exception as e:
        return {"error": f"MarkItDown conversion failed: {e}", "code": "MARKITDOWN_FAIL"}

    # 4. Non-empty check
    if not content or len(content.strip()) < _MIN_CONTENT_CHARS:
        return {
            "error": f"Converted content too short ({len(content.strip() if content else '')} chars, min {_MIN_CONTENT_CHARS})",
            "code": "EMPTY_CONTENT",
        }

    # 5. Truncate if > 50k chars
    truncated = False
    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS]
        truncated = True

    slug = _make_slug(path)
    drawer_path = str(path)
    tokens = len(content) // 4
    fmt = path.suffix.lstrip(".").lower() or "unknown"
    group = _classify_group(path)

    # 6. Dedup: check if path already exists
    radar_id = _upsert_by_path(slug, content, drawer_path, tokens, group=group)

    if radar_id is None:
        # New entry — use store_from_string (store_sonar)
        radar_id = store_from_string(content, slug, group)
        # Update drawer_path in radar (store_sonar sets it to the .clsc.md file path)
        try:
            conn = sqlite3.connect(str(FTS_DB))
            conn.execute(
                "UPDATE radar SET drawer_path=? WHERE slug=?", (drawer_path, slug)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("ingest_file: failed to update drawer_path for %s: %s", slug, e)

    return {
        "slug": slug,
        "group": group,
        "chars": len(content),
        "radar_id": radar_id,
        "format": fmt,
        "truncated": truncated,
    }
