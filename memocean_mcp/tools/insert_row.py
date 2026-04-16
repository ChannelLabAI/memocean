"""
insert_row.py — MEMO-011: Best-effort Haiku summary generation for radar entries.

Called after a radar row is inserted/updated. Generates a 150-char execution summary
for SOP/spec/guide content. Non-procedural content gets summary=NULL.
"""
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

from ..config import FTS_DB

logger = logging.getLogger("memocean_mcp.insert_row")

# Trigger keywords in content
_CONTENT_TRIGGER_KEYWORDS = re.compile(
    r'\b(SOP|spec|指引|CLAUDE|README|task|procedure|步驟|流程|規範)\b',
    re.IGNORECASE
)

# Trigger tags in CLSC Sonar
_TAG_TRIGGER_PATTERN = re.compile(
    r'TAG:[^|\n]*\b(sop|spec|guide|procedure)\b',
    re.IGNORECASE
)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_HAIKU_TIMEOUT = 10.0  # seconds

_SYSTEM_PROMPT = """你是記憶摘要器，輸出必須是 JSON {"summary": string|null}。
從以下內容提取如何執行的核心步驟和判斷邏輯，150字以內，繁體中文，條列式。
非程序性內容（純知識、資料、描述）輸出 {"summary": null}。
只輸出 JSON，不要其他文字。"""


def _should_generate_summary(content: str, clsc: str) -> bool:
    """Check if this entry triggers summary generation."""
    if content and _CONTENT_TRIGGER_KEYWORDS.search(content):
        return True
    if clsc and _TAG_TRIGGER_PATTERN.search(clsc):
        return True
    return False


def _get_anthropic_client():
    """Lazy singleton Anthropic client (consistent with radar_search.py pattern)."""
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _anthropic_client

_anthropic_client = None


def _call_haiku(content: str) -> str | None:
    """Call Haiku to generate summary. Returns summary string, None, or raises."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None  # No API key — skip silently

    client = _get_anthropic_client()

    # Truncate content to ~3000 chars for cost control
    truncated = content[:3000] if len(content) > 3000 else content

    response = client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=300,
        temperature=0,
        timeout=_HAIKU_TIMEOUT,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": truncated}],
    )
    raw = response.content[0].text.strip()

    # Parse JSON response
    try:
        parsed = json.loads(raw)
        return parsed.get("summary")  # None if {"summary": null}
    except json.JSONDecodeError:
        logger.warning("insert_row: JSON parse failed for Haiku response: %r", raw[:100])
        return None  # Fallback: treat as NULL


def generate_and_store_summary(slug: str, content: str, clsc: str) -> None:
    """
    Best-effort: generate summary for slug and write to radar.summary.
    Never raises — all exceptions are caught and logged as warnings.
    Does not block the main write path.
    """
    try:
        if not _should_generate_summary(content, clsc):
            return  # Not a procedural entry — skip

        summary = _call_haiku(content)
        # summary is now: str (procedural) | None (non-procedural or API unavailable)

        if not FTS_DB.exists():
            return

        conn = sqlite3.connect(str(FTS_DB))
        try:
            # Check column exists (migration may not have run yet)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(radar)")}
            if "summary" not in cols:
                logger.warning("insert_row: summary column not found in radar table, run migration first")
                return
            conn.execute(
                "UPDATE radar SET summary=? WHERE slug=?",
                (summary, slug)
            )
            conn.commit()
        finally:
            conn.close()

    except Exception as e:
        logger.warning("insert_row: summary generation failed for %s: %s", slug, e)
        # Never re-raise — best-effort only
