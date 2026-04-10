"""
test_tools.py — Integration tests for memocean-mcp tools.
Tests use real data where available, skip gracefully when dependencies are absent.
"""
import json
import os
import sys
from pathlib import Path

import pytest

# Ensure package is importable even if not pip-installed
PACKAGE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from memocean_mcp.config import FTS_DB, KG_DB, TASKS_ROOT, LEARNED_SKILLS_DIR, SHARED_ROOT


# ==================== FTS SEARCH ====================


@pytest.mark.skipif(not FTS_DB.exists(), reason=f"memory.db not found at {FTS_DB}")
def test_fts_search_basic():
    """Search for common terms and confirm non-empty results."""
    from memocean_mcp.tools.fts_search import fts_search

    # Try a few likely terms
    for query in ("anna", "NOX", "老兔"):
        results = fts_search(query, limit=5)
        if results:
            # Validate result structure
            r = results[0]
            assert "bot_name" in r
            assert "snippet" in r
            assert "ts" in r
            return  # Any successful hit is sufficient

    # If no results for any query, warn but don't fail (empty DB is valid)
    pytest.skip("No results found for test queries — DB may be empty")


@pytest.mark.skipif(not FTS_DB.exists(), reason=f"memory.db not found at {FTS_DB}")
def test_fts_search_returns_list():
    """fts_search always returns a list."""
    from memocean_mcp.tools.fts_search import fts_search

    results = fts_search("XYZZY_NONEXISTENT_TOKEN_12345", limit=5)
    assert isinstance(results, list)


def test_fts_search_missing_db(tmp_path, monkeypatch):
    """FileNotFoundError raised when memory.db is absent."""
    import memocean_mcp.config as config_mod
    monkeypatch.setattr(config_mod, "FTS_DB", tmp_path / "nonexistent.db")

    # Re-import to pick up patched config
    import importlib
    import memocean_mcp.tools.fts_search as fts_mod
    importlib.reload(fts_mod)

    with pytest.raises(FileNotFoundError):
        fts_mod.fts_search("test")


# ==================== KG QUERY ====================


@pytest.mark.skipif(not KG_DB.exists(), reason=f"kg.db not found at {KG_DB}")
def test_kg_query_laotu():
    """Query 老兔 and confirm role=CEO fact exists."""
    from memocean_mcp.tools.kg_query import kg_query

    facts = kg_query("老兔", direction="outgoing")
    assert isinstance(facts, list), "kg_query must return a list"
    assert len(facts) > 0, "老兔 should have at least one fact in KG"

    predicates = {f.get("predicate") for f in facts}
    objects = {f.get("obj") or f.get("object") for f in facts}

    assert "role" in predicates or any("CEO" in str(o) for o in objects), \
        f"Expected role=CEO for 老兔, got predicates={predicates} objects={objects}"


@pytest.mark.skipif(not KG_DB.exists(), reason=f"kg.db not found at {KG_DB}")
def test_kg_query_returns_list():
    """kg_query always returns a list."""
    from memocean_mcp.tools.kg_query import kg_query

    # Unlikely entity — should return empty list, not crash
    results = kg_query("NONEXISTENT_ENTITY_XYZ", direction="both")
    assert isinstance(results, list)


# ==================== CLOSET GET ====================


def test_closet_get_stub_verbatim():
    """verbatim_fetch runs without crashing; returns string."""
    from memocean_mcp.tools.closet_get import verbatim_fetch

    result = verbatim_fetch("test-slug-that-does-not-exist")
    assert isinstance(result, str)
    assert len(result) > 0


def test_closet_get_mode_verbatim():
    """closet_get with mode=verbatim returns string."""
    from memocean_mcp.tools.closet_get import closet_get

    result = closet_get("test-slug", mode="verbatim")
    assert isinstance(result, str)


def test_closet_get_mode_skeleton():
    """closet_get with mode=skeleton returns string."""
    from memocean_mcp.tools.closet_get import closet_get

    result = closet_get("test-slug", mode="skeleton")
    assert isinstance(result, str)


def test_closet_get_bad_mode():
    """closet_get with unknown mode returns error string."""
    from memocean_mcp.tools.closet_get import closet_get

    result = closet_get("test-slug", mode="invalid_mode")
    assert "unknown mode" in result.lower() or "invalid" in result.lower()


# ==================== SKILL TOOLS ====================


def test_skill_list_returns_list():
    """skill_list always returns a list."""
    from memocean_mcp.tools.skill_tools import skill_list

    skills = skill_list()
    assert isinstance(skills, list)


@pytest.mark.skipif(not LEARNED_SKILLS_DIR.exists(), reason=f"Skills dir not found at {LEARNED_SKILLS_DIR}")
def test_skill_list_nonempty():
    """If skills dir exists, returns at least one skill."""
    from memocean_mcp.tools.skill_tools import skill_list

    skills = skill_list()
    assert len(skills) > 0, f"Expected skills in {LEARNED_SKILLS_DIR}"


@pytest.mark.skipif(not LEARNED_SKILLS_DIR.exists(), reason=f"Skills dir not found at {LEARNED_SKILLS_DIR}")
def test_skill_get_known():
    """skill_get returns markdown content for a known skill."""
    from memocean_mcp.tools.skill_tools import skill_list, skill_get

    skills = skill_list()
    assert skills, "No skills available for test"

    content = skill_get(skills[0])
    assert isinstance(content, str)
    assert len(content) > 0
    # Should look like markdown
    assert not content.startswith("[skill '")


def test_skill_get_missing():
    """skill_get returns error string for unknown skill."""
    from memocean_mcp.tools.skill_tools import skill_get

    result = skill_get("definitely-nonexistent-skill-xyz-abc-123")
    assert isinstance(result, str)
    # Should be an error message
    assert "not found" in result.lower() or "not found" in result or "[" in result


# ==================== TASK CREATE ====================


def test_task_create_dry_run(tmp_path, monkeypatch):
    """Create a test task, verify file created, then delete it."""
    import memocean_mcp.config as config_mod
    monkeypatch.setattr(config_mod, "TASKS_ROOT", tmp_path / "tasks")

    import importlib
    import memocean_mcp.tools.task_create as tc_mod
    importlib.reload(tc_mod)

    result = tc_mod.task_create(
        title="TEST TASK DELETE ME",
        description="Automated test task — safe to delete",
        assigned_to="anna",
        assigned_by="test_runner",
        priority="low",
        acceptance_criteria=["Test passes", "File is created"],
    )

    assert result.get("status") == "pending"
    assert result.get("assigned_to") == "anna"
    assert result.get("title") == "TEST TASK DELETE ME"
    assert "task_id" in result
    assert "file_path" in result
    assert "filename" in result

    file_path = Path(result["file_path"])
    assert file_path.exists(), f"Task file not created at {file_path}"

    # Validate JSON structure
    task_data = json.loads(file_path.read_text())
    assert task_data["title"] == "TEST TASK DELETE ME"
    assert task_data["assigned_to"] == "anna"
    assert task_data["status"] == "pending"
    assert task_data["priority"] == "low"
    assert "history" in task_data
    assert len(task_data["history"]) == 1
    assert task_data["spec"]["acceptance_criteria"] == ["Test passes", "File is created"]

    # Cleanup (tmp_path auto-cleans, but explicit for clarity)
    file_path.unlink()
    assert not file_path.exists()


def test_task_create_real_pending(tmp_path, monkeypatch):
    """Create task in real tasks/pending dir if it exists."""
    real_pending = TASKS_ROOT / "pending"
    if not real_pending.exists():
        pytest.skip(f"Real tasks/pending not found at {real_pending}")

    from memocean_mcp.tools.task_create import task_create

    result = task_create(
        title="TEST TASK DELETE ME",
        description="Automated test task — safe to delete. Created by test suite.",
        assigned_to="anna",
        assigned_by="test_suite",
        priority="low",
    )

    file_path = Path(result["file_path"])
    assert file_path.exists(), f"Task file not created at {file_path}"

    # Verify content
    task_data = json.loads(file_path.read_text())
    assert task_data["title"] == "TEST TASK DELETE ME"

    # Clean up
    file_path.unlink()
    print(f"\nCreated and deleted test task: {file_path.name}")


# ==================== SERVER IMPORT ====================


def test_server_import():
    """server.py imports cleanly and TOOLS dict is populated."""
    from memocean_mcp.server import TOOLS, handle_request

    assert len(TOOLS) == 7
    expected = {
        "memocean_fts_search",
        "memocean_closet_get",
        "memocean_closet_search",
        "memocean_kg_query",
        "memocean_skill_list",
        "memocean_task_create",
        "memocean_ask_opus",
    }
    assert set(TOOLS.keys()) == expected


def test_server_tools_list():
    """handle_request('tools/list') returns all 7 tools."""
    from memocean_mcp.server import handle_request

    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = handle_request(request)

    assert response["id"] == 1
    tools = response["result"]["tools"]
    assert len(tools) == 7
    names = {t["name"] for t in tools}
    assert "memocean_fts_search" in names
    assert "memocean_task_create" in names


def test_server_initialize():
    """handle_request('initialize') returns correct protocol version."""
    from memocean_mcp.server import handle_request

    request = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}
    response = handle_request(request)

    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["serverInfo"]["name"] == "memocean-mcp"


# ── Security tests (v0.1.1) ─────────────────────────────────────────────────

def test_closet_get_path_traversal_blocked():
    """C1 fix: path traversal slugs must raise ValueError."""
    import pytest
    from memocean_mcp.tools.closet_get import verbatim_fetch, skeleton_read
    for bad in ["../../etc/passwd", "../secrets", "/abs/path", "a" * 101]:
        with pytest.raises(ValueError):
            verbatim_fetch(bad)
        with pytest.raises(ValueError):
            skeleton_read(bad)


def test_skill_get_path_traversal_blocked():
    """C2 fix: path traversal names must raise ValueError."""
    import pytest
    from memocean_mcp.tools.skill_tools import skill_get
    for bad in ["../../etc/passwd", "../secrets", "/abs/path", "a" * 101]:
        with pytest.raises(ValueError):
            skill_get(bad)


def test_task_create_invalid_assignee():
    """C4 fix: invalid assignee must raise ValueError at runtime."""
    import pytest
    from memocean_mcp.tools.task_create import task_create
    with pytest.raises(ValueError, match="assigned_to"):
        task_create("test", "desc", assigned_to="hacker")  # type: ignore


def test_task_create_invalid_priority():
    """C4 fix: invalid priority must raise ValueError at runtime."""
    import pytest
    from memocean_mcp.tools.task_create import task_create
    with pytest.raises(ValueError, match="priority"):
        task_create("test", "desc", assigned_to="anna", priority="critical")  # type: ignore


# ── closet_search tests (v0.1.3) ─────────────────────────────────────────────

def test_closet_search_single_term():
    """Single term returns results from closet."""
    from memocean_mcp.tools.closet_search import closet_search
    results = closet_search("ChannelLab", limit=5)
    assert isinstance(results, list)
    # If closet is populated, should have results
    if results:
        assert "slug" in results[0]
        assert "aaak" in results[0]


def test_closet_search_multi_term_and():
    """Multi-term AND: 'Knowledge Infra' matches hyphenated slug content."""
    from memocean_mcp.tools.closet_search import closet_search
    results = closet_search("Knowledge Infra", limit=5)
    assert isinstance(results, list)
    # With full backfill, should find Knowledge-Infra-ADR notes
    if results:
        for r in results:
            assert "Knowledge" in r["aaak"] or "Infra" in r["aaak"]


def test_closet_search_empty_query():
    """Empty query returns empty list."""
    from memocean_mcp.tools.closet_search import closet_search
    assert closet_search("") == []
    assert closet_search("   ") == []


def test_closet_search_no_match():
    """Query with no matching content returns empty list."""
    from memocean_mcp.tools.closet_search import closet_search
    results = closet_search("xyzzy_nonexistent_term_12345", limit=5)
    assert results == []


def test_closet_search_via_server():
    """closet_search tool is registered in server TOOLS dict."""
    from memocean_mcp.server import TOOLS
    assert "memocean_closet_search" in TOOLS
    spec = TOOLS["memocean_closet_search"]
    assert "handler" in spec
    assert "query" in spec["input_schema"]["properties"]


def test_closet_search_sql_injection_safe():
    """SQL injection via query must not raise or return unexpected results."""
    from memocean_mcp.tools.closet_search import closet_search
    # These payloads should be treated as literal search terms, not SQL
    payloads = [
        "'; DROP TABLE closet; --",
        "1 OR 1=1",
        "' UNION SELECT * FROM closet --",
        "\\x00null",
    ]
    for payload in payloads:
        result = closet_search(payload, limit=5)
        # Must return a list (not raise), and not return all rows
        assert isinstance(result, list), f"Raised on payload: {payload!r}"
        # Paranoia check: no more than limit results (not a full table dump)
        assert len(result) <= 5, f"Too many results for payload: {payload!r}"
