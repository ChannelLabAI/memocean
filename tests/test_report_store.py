"""
test_report_store.py — Unit tests for memocean_report_store.

6 test cases per spec §3.7:
1. Write → file exists, frontmatter correct, slug matches pattern
2. title > 60 chars → truncated, write still succeeds
3. content > 500 KB → rejected with CONTENT_TOO_LARGE
4. group with invalid chars → rejected with INVALID_GROUP
5. timestamp collision → file gets -dup1 suffix
6. ttl_days set → frontmatter expires_at is correct
"""
import re
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))


@pytest.fixture
def tmp_vault(tmp_path):
    """Patch OCEAN_VAULT_ROOT to a temp dir."""
    with patch("memocean_mcp.tools.report_store.OCEAN_VAULT_ROOT", tmp_path):
        yield tmp_path


def call_store(tmp_vault, **kwargs):
    from memocean_mcp.tools.report_store import memocean_report_store
    return memocean_report_store(**kwargs)


# ── Test 1: basic write ───────────────────────────────────────────────────────

def test_write_basic(tmp_vault):
    from memocean_mcp.tools.report_store import memocean_report_store

    result = memocean_report_store("Test Report Title", "# Hello\nworld content", bot="anna")

    assert "error" not in result
    assert "path" in result
    assert "relative_path" in result
    assert "slug" in result
    assert "size_bytes" in result
    assert "tokens_estimate" in result

    out = Path(result["path"])
    assert out.exists()

    # Slug matches {YYYY-MM-DD-HHMM}-... pattern
    assert re.match(r"\d{4}-\d{2}-\d{2}-\d{4}-.+", result["slug"])

    # Frontmatter checks
    text = out.read_text(encoding="utf-8")
    assert "type: subagent-report" in text
    assert "bot: anna" in text
    assert "group: subagent-reports" in text
    assert 'title: "Test Report Title"' in text
    assert "source: memocean_report_store" in text
    assert "# Hello" in text


# ── Test 2: title too long → truncated ───────────────────────────────────────

def test_title_truncated(tmp_vault):
    from memocean_mcp.tools.report_store import memocean_report_store

    long_title = "A" * 80
    result = memocean_report_store(long_title, "content")

    assert "error" not in result
    out = Path(result["path"])
    text = out.read_text(encoding="utf-8")
    # Title in frontmatter should be truncated to 60 chars
    assert f'title: "{"A" * 60}"' in text


# ── Test 3: content too large → rejected ─────────────────────────────────────

def test_content_too_large(tmp_vault):
    from memocean_mcp.tools.report_store import memocean_report_store

    big = "x" * (500 * 1024 + 1)
    result = memocean_report_store("Title", big)

    assert result.get("code") == "CONTENT_TOO_LARGE"
    assert "error" in result


# ── Test 4: invalid group → rejected ─────────────────────────────────────────

def test_invalid_group(tmp_vault):
    from memocean_mcp.tools.report_store import memocean_report_store

    result = memocean_report_store("Title", "content", group="Invalid Group!")

    assert result.get("code") == "INVALID_GROUP"
    assert "error" in result


# ── Test 5: timestamp collision → -dup1 suffix ───────────────────────────────

def test_timestamp_collision(tmp_vault):
    from memocean_mcp.tools.report_store import memocean_report_store, _make_slug

    slug = _make_slug("Collision Title")
    reports_dir = tmp_vault / "Ocean" / "Chart" / "MemOcean" / "Reports" / "subagent-reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    # Pre-create the file so the next call hits a collision
    (reports_dir / f"{slug}.md").write_text("existing", encoding="utf-8")

    result = memocean_report_store("Collision Title", "new content")

    assert "error" not in result
    assert result["path"].endswith("-dup1.md")
    out = Path(result["path"])
    assert out.exists()
    assert "new content" in out.read_text(encoding="utf-8")


# ── Test 6: ttl_days → expires_at correct ────────────────────────────────────

def test_ttl_expires_at(tmp_vault):
    from memocean_mcp.tools.report_store import memocean_report_store

    before = datetime.now(tz=timezone.utc)
    result = memocean_report_store("TTL Report", "content", ttl_days=7)
    after = datetime.now(tz=timezone.utc)

    assert "error" not in result
    assert result["expires_at"] is not None

    expires = datetime.strptime(result["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert before + timedelta(days=7) - timedelta(seconds=1) <= expires <= after + timedelta(days=7)

    # Also verify frontmatter
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "expires_at:" in text
    assert result["expires_at"] in text
