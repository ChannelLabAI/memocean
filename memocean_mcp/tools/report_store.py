"""
report_store.py — Store verbatim subagent reports into MemOcean Reports folder.

Writes to:
  {OCEAN_VAULT_ROOT}/Ocean/技術海圖/MemOcean/Reports/{group}/{YYYY-MM-DD-HHMM}-{slug}.md

Returns:
  {"path", "relative_path", "slug", "size_bytes", "tokens_estimate", "expires_at"}
Errors:
  {"error": "...", "code": "CONTENT_TOO_LARGE | INVALID_GROUP"}
"""
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ..config import OCEAN_VAULT_ROOT

logger = logging.getLogger("memocean_mcp.report_store")

_MAX_TITLE_CHARS = 60
_MAX_CONTENT_BYTES = 500 * 1024  # 500 KB
_GROUP_PATTERN = re.compile(r"^[a-z0-9-]+$")
_REPORTS_BASE = Path("Ocean") / "Chart" / "MemOcean" / "Reports"


def _sanitize_title(title: str) -> str:
    """Convert title to safe filename slug component (≤50 chars)."""
    sanitized = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", title).strip("-")
    return sanitized[:50]


def _make_slug(title: str) -> str:
    """Build slug: {YYYY-MM-DD-HHMM}-{sanitized-title}."""
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    return f"{ts}-{_sanitize_title(title)}"


def memocean_report_store(
    title: str,
    content: str,
    group: str = "subagent-reports",
    bot: str | None = None,
    ttl_days: int | None = None,
) -> dict:
    """Store a verbatim report into Ocean/技術海圖/MemOcean/Reports/{group}/."""

    # Validation
    if len(title) > _MAX_TITLE_CHARS:
        logger.warning("report_store: title truncated from %d to %d chars", len(title), _MAX_TITLE_CHARS)
        title = title[:_MAX_TITLE_CHARS]

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > _MAX_CONTENT_BYTES:
        return {
            "error": f"content_too_large: {len(content_bytes)} bytes (max {_MAX_CONTENT_BYTES})",
            "code": "CONTENT_TOO_LARGE",
        }

    if not _GROUP_PATTERN.match(group):
        return {
            "error": f"invalid_group: '{group}' must match [a-z0-9-]+",
            "code": "INVALID_GROUP",
        }

    # Bot name resolution — enforce [a-z0-9_-]+ whitelist for YAML safety
    _BOT_PATTERN = re.compile(r"^[a-z0-9_-]+$")
    if bot is None:
        state_dir = os.environ.get("TELEGRAM_STATE_DIR", "")
        if state_dir:
            bot = Path(state_dir).name
        else:
            bot = os.environ.get("BOT_NAME", "unknown")
    if not _BOT_PATTERN.match(str(bot)):
        bot = "unknown"

    # Paths
    reports_dir = OCEAN_VAULT_ROOT / _REPORTS_BASE / group
    reports_dir.mkdir(parents=True, exist_ok=True)

    slug = _make_slug(title)
    out_path = reports_dir / f"{slug}.md"

    # Handle rare timestamp collision → append -dupN (cap at 100)
    dup_n = 1
    while out_path.exists():
        if dup_n > 100:
            return {"error": "too_many_duplicates", "code": "SLUG_COLLISION"}
        out_path = reports_dir / f"{slug}-dup{dup_n}.md"
        dup_n += 1

    # Timestamps
    now_iso = datetime.now(tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if ttl_days is not None:
        expires_dt = datetime.now(tz=timezone.utc) + timedelta(days=ttl_days)
        expires_at = expires_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        expires_at = None

    tokens_estimate = len(content) // 4  # conservative: char_count / 3.5 ≈ / 4

    # Write
    expires_yaml = f'"{expires_at}"' if expires_at else "null"
    title_yaml = title.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", "")
    file_content = (
        f"---\n"
        f"type: subagent-report\n"
        f"created: {now_iso}\n"
        f"bot: {bot}\n"
        f"group: {group}\n"
        f'title: "{title_yaml}"\n'
        f"size_bytes: {len(content_bytes)}\n"
        f"tokens_estimate: {tokens_estimate}\n"
        f"expires_at: {expires_yaml}\n"
        f"source: memocean_report_store\n"
        f"---\n\n"
        f"{content}"
    )
    out_path.write_text(file_content, encoding="utf-8")

    size_bytes = out_path.stat().st_size
    relative_path = str(_REPORTS_BASE / group / out_path.name)

    return {
        "path": str(out_path),
        "relative_path": relative_path,
        "slug": slug,
        "size_bytes": size_bytes,
        "tokens_estimate": tokens_estimate,
        "expires_at": expires_at,
    }
