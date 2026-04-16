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
    for query in ("builder", "NOX", "owner"):
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
def test_kg_query_owner():
    """Query owner and confirm role=CEO fact exists."""
    from memocean_mcp.tools.kg_query import kg_query

    facts = kg_query("owner", direction="outgoing")
    assert isinstance(facts, list), "kg_query must return a list"
    assert len(facts) > 0, "owner should have at least one fact in KG"

    predicates = {f.get("predicate") for f in facts}
    objects = {f.get("obj") or f.get("object") for f in facts}

    assert "role" in predicates or any("CEO" in str(o) for o in objects), \
        f"Expected role=CEO for owner, got predicates={predicates} objects={objects}"


@pytest.mark.skipif(not KG_DB.exists(), reason=f"kg.db not found at {KG_DB}")
def test_kg_query_returns_list():
    """kg_query always returns a list."""
    from memocean_mcp.tools.kg_query import kg_query

    # Unlikely entity — should return empty list, not crash
    results = kg_query("NONEXISTENT_ENTITY_XYZ", direction="both")
    assert isinstance(results, list)


# ==================== RADAR GET ====================


def test_radar_get_stub_verbatim():
    """verbatim_fetch runs without crashing; returns string."""
    from memocean_mcp.tools.radar_get import verbatim_fetch

    result = verbatim_fetch("test-slug-that-does-not-exist")
    assert isinstance(result, str)
    assert len(result) > 0


def test_radar_get_mode_verbatim():
    """radar_get with mode=verbatim returns string."""
    from memocean_mcp.tools.radar_get import radar_get

    result = radar_get("test-slug", mode="verbatim")
    assert isinstance(result, str)


def test_radar_get_mode_sonar():
    """radar_get with mode=sonar returns string."""
    from memocean_mcp.tools.radar_get import radar_get

    result = radar_get("test-slug", mode="sonar")
    assert isinstance(result, str)


def test_radar_get_bad_mode():
    """radar_get with unknown mode returns error string."""
    from memocean_mcp.tools.radar_get import radar_get

    result = radar_get("test-slug", mode="invalid_mode")
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
        assigned_to="builder",
        assigned_by="test_runner",
        priority="low",
        acceptance_criteria=["Test passes", "File is created"],
    )

    assert result.get("status") == "pending"
    assert result.get("assigned_to") == "builder"
    assert result.get("title") == "TEST TASK DELETE ME"
    assert "task_id" in result
    assert "file_path" in result
    assert "filename" in result

    file_path = Path(result["file_path"])
    assert file_path.exists(), f"Task file not created at {file_path}"

    # Validate JSON structure
    task_data = json.loads(file_path.read_text())
    assert task_data["title"] == "TEST TASK DELETE ME"
    assert task_data["assigned_to"] == "builder"
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
        assigned_to="builder",
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

    # MEMO-010: added memocean_search + memocean_ocean_search + memocean_ingest_file → 9 total
    assert len(TOOLS) == 9
    expected = {
        "memocean_search",
        "memocean_messages_search",
        "memocean_seabed_get",
        "memocean_radar_search",
        "memocean_ocean_search",
        "memocean_kg_query",
        "memocean_skill_list",
        "memocean_task_create",
        "memocean_ingest_file",
    }
    assert set(TOOLS.keys()) == expected


def test_server_tools_list():
    """handle_request('tools/list') returns all 9 tools."""
    from memocean_mcp.server import handle_request

    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = handle_request(request)

    assert response["id"] == 1
    tools = response["result"]["tools"]
    assert len(tools) == 9
    names = {t["name"] for t in tools}
    assert "memocean_search" in names
    assert "memocean_messages_search" in names
    assert "memocean_task_create" in names


def test_server_initialize():
    """handle_request('initialize') returns correct protocol version."""
    from memocean_mcp.server import handle_request

    request = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}
    response = handle_request(request)

    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["serverInfo"]["name"] == "memocean-mcp"


# ── Security tests (v0.1.1) ─────────────────────────────────────────────────

def test_radar_get_path_traversal_blocked():
    """C1 fix: path traversal slugs must raise ValueError."""
    import pytest
    from memocean_mcp.tools.radar_get import verbatim_fetch, sonar_read
    for bad in ["../../etc/passwd", "../secrets", "/abs/path", "a" * 101]:
        with pytest.raises(ValueError):
            verbatim_fetch(bad)
        with pytest.raises(ValueError):
            sonar_read(bad)


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
        task_create("test", "desc", assigned_to="builder", priority="critical")  # type: ignore


# ── radar_search tests (v0.1.3) ─────────────────────────────────────────────

def test_radar_search_single_term():
    """Single term returns results from radar."""
    from memocean_mcp.tools.radar_search import radar_search
    results = radar_search("ChannelLab", limit=5)
    assert isinstance(results, list)
    # If radar is populated, should have results
    if results:
        assert "slug" in results[0]
        assert "clsc" in results[0]


def test_radar_search_multi_term_and():
    """Multi-term AND: 'Knowledge Infra' matches hyphenated slug content."""
    from memocean_mcp.tools.radar_search import radar_search
    results = radar_search("Knowledge Infra", limit=5)
    assert isinstance(results, list)
    # With full backfill, should find Knowledge-Infra-ADR notes
    if results:
        for r in results:
            assert "Knowledge" in r["clsc"] or "Infra" in r["clsc"]


def test_radar_search_empty_query():
    """Empty query returns empty list."""
    from memocean_mcp.tools.radar_search import radar_search
    assert radar_search("") == []
    assert radar_search("   ") == []


def test_radar_search_no_match():
    """Query with no matching content returns empty list."""
    from memocean_mcp.tools.radar_search import radar_search
    results = radar_search("xyzzy_nonexistent_term_12345", limit=5)
    assert results == []


def test_radar_search_via_server():
    """radar_search tool is registered in server TOOLS dict."""
    from memocean_mcp.server import TOOLS
    assert "memocean_radar_search" in TOOLS
    spec = TOOLS["memocean_radar_search"]
    assert "handler" in spec
    assert "query" in spec["input_schema"]["properties"]


def test_radar_search_sql_injection_safe():
    """SQL injection via query must not raise or return unexpected results."""
    from memocean_mcp.tools.radar_search import radar_search
    # These payloads should be treated as literal search terms, not SQL
    payloads = [
        "'; DROP TABLE radar; --",
        "1 OR 1=1",
        "' UNION SELECT * FROM radar --",
        "\\x00null",
    ]
    for payload in payloads:
        result = radar_search(payload, limit=5)
        # Must return a list (not raise), and not return all rows
        assert isinstance(result, list), f"Raised on payload: {payload!r}"
        # Paranoia check: no more than limit results (not a full table dump)
        assert len(result) <= 5, f"Too many results for payload: {payload!r}"


# ==================== MULTI-QUERY EXPANSION ====================

def test_expand_query_no_api_key(monkeypatch):
    """Without API key, _expand_query returns [original_query]."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from memocean_mcp.tools.radar_search import _expand_query, _EXPANSION_CACHE
    _EXPANSION_CACHE.clear()
    result = _expand_query("ChannelLab GEO")
    assert result == ["ChannelLab GEO"]

def test_rrf_merge_basic():
    """RRF merge gives highest score to items appearing in all lists."""
    from memocean_mcp.tools.radar_search import _rrf_merge
    list1 = [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}]
    list2 = [{"slug": "b"}, {"slug": "a"}, {"slug": "d"}]
    merged = _rrf_merge([list1, list2])
    slugs = [r["slug"] for r in merged]
    # a and b appear in both lists → should rank above c and d
    assert slugs.index("a") < slugs.index("c")
    assert slugs.index("b") < slugs.index("d")

def test_rrf_merge_empty():
    """RRF merge handles empty lists."""
    from memocean_mcp.tools.radar_search import _rrf_merge
    assert _rrf_merge([]) == []
    assert _rrf_merge([[]]) == []

def test_rrf_merge_single_list():
    """RRF merge with single list preserves order."""
    from memocean_mcp.tools.radar_search import _rrf_merge
    items = [{"slug": "x"}, {"slug": "y"}]
    result = _rrf_merge([items])
    assert [r["slug"] for r in result] == ["x", "y"]

def test_expansion_cache():
    """Same query is cached and not re-expanded."""
    from memocean_mcp.tools.radar_search import _expand_query, _EXPANSION_CACHE
    _EXPANSION_CACHE.clear()
    _EXPANSION_CACHE["test-query"] = ["test-query", "variant1"]
    result = _expand_query("test-query")
    assert result == ["test-query", "variant1"]


# ==================== MERGE CANDIDATES (P5 RRF) ====================

def test_merge_candidates_empty():
    """Both empty → empty list."""
    from memocean_mcp.tools.radar_search import _merge_candidates
    assert _merge_candidates([], []) == []


def test_merge_candidates_fts_only():
    """sem empty → returns fts results in RRF order (single list preserves order)."""
    from memocean_mcp.tools.radar_search import _merge_candidates
    fts = [{"slug": "a"}, {"slug": "b"}]
    result = _merge_candidates(fts, [])
    assert [r["slug"] for r in result] == ["a", "b"]
    assert result[0]["sources"] == ["fts"]
    assert result[1]["sources"] == ["fts"]


def test_merge_candidates_sem_only():
    """fts empty → returns sem results in RRF order."""
    from memocean_mcp.tools.radar_search import _merge_candidates
    sem = [{"slug": "x"}, {"slug": "y"}]
    result = _merge_candidates([], sem)
    assert [r["slug"] for r in result] == ["x", "y"]
    assert result[0]["sources"] == ["sem"]


def test_merge_candidates_cross_path_ranks_above_single():
    """Items appearing in both fts and sem rank above single-path items."""
    from memocean_mcp.tools.radar_search import _merge_candidates
    fts = [{"slug": "shared"}, {"slug": "fts-only"}]
    sem = [{"slug": "shared"}, {"slug": "sem-only"}]
    result = _merge_candidates(fts, sem)
    slugs = [r["slug"] for r in result]
    # shared appears in both → highest RRF score
    assert slugs[0] == "shared"


def test_merge_candidates_sources_tracking():
    """sources field correctly reflects which retrieval paths hit each doc."""
    from memocean_mcp.tools.radar_search import _merge_candidates
    fts = [{"slug": "a"}, {"slug": "b"}]
    sem = [{"slug": "b"}, {"slug": "c"}]
    result = _merge_candidates(fts, sem)
    by_slug = {r["slug"]: r["sources"] for r in result}
    assert by_slug["a"] == ["fts"]
    assert set(by_slug["b"]) == {"fts", "sem"}
    assert by_slug["c"] == ["sem"]


def test_merge_candidates_rrf_order():
    """Verify RRF score order: item ranked #1 in both > item ranked #1 in one."""
    from memocean_mcp.tools.radar_search import _merge_candidates
    # 'top' is #1 in fts, #1 in sem → max possible RRF score
    # 'mid' is #2 in fts only
    fts = [{"slug": "top"}, {"slug": "mid"}]
    sem = [{"slug": "top"}, {"slug": "other"}]
    result = _merge_candidates(fts, sem)
    slugs = [r["slug"] for r in result]
    assert slugs.index("top") < slugs.index("mid")
    assert slugs.index("top") < slugs.index("other")


# ==================== MESSAGES HYBRID SEARCH ====================


def test_messages_hybrid_import():
    """messages_hybrid_search module imports cleanly."""
    from memocean_mcp.tools.messages_hybrid_search import messages_hybrid_search
    assert callable(messages_hybrid_search)


def test_messages_hybrid_empty_query():
    """Empty query returns empty list."""
    from memocean_mcp.tools.messages_hybrid_search import messages_hybrid_search
    assert messages_hybrid_search("") == []
    assert messages_hybrid_search("   ") == []


def test_messages_hybrid_returns_list():
    """messages_hybrid_search always returns a list."""
    import os
    from memocean_mcp.tools.messages_hybrid_search import messages_hybrid_search
    # Force BM25-only to keep test fast/deterministic
    orig = os.environ.get("KNN_ENABLED")
    os.environ["KNN_ENABLED"] = "false"
    try:
        results = messages_hybrid_search("XYZZY_NONEXISTENT_TOKEN_99999", limit=5)
        assert isinstance(results, list)
    finally:
        if orig is None:
            os.environ.pop("KNN_ENABLED", None)
        else:
            os.environ["KNN_ENABLED"] = orig


@pytest.mark.skipif(not FTS_DB.exists(), reason=f"memory.db not found at {FTS_DB}")
def test_messages_hybrid_bm25_fallback():
    """KNN_ENABLED=false returns BM25-only results with expected schema."""
    import os
    from memocean_mcp.tools.messages_hybrid_search import messages_hybrid_search

    orig = os.environ.get("KNN_ENABLED")
    os.environ["KNN_ENABLED"] = "false"
    try:
        results = messages_hybrid_search("部署", limit=5)
        assert isinstance(results, list)
        # Result schema must include standard BM25 fields
        if results:
            r = results[0]
            assert "bot_name" in r
            assert "snippet" in r
            assert "chat_id" in r
            assert "message_id" in r
            # slug must be stripped from output
            assert "slug" not in r
    finally:
        if orig is None:
            os.environ.pop("KNN_ENABLED", None)
        else:
            os.environ["KNN_ENABLED"] = orig


@pytest.mark.skipif(not FTS_DB.exists(), reason=f"memory.db not found at {FTS_DB}")
def test_messages_hybrid_no_slug_in_output():
    """Internal slug field must be stripped before returning results."""
    import os
    from memocean_mcp.tools.messages_hybrid_search import messages_hybrid_search

    orig = os.environ.get("KNN_ENABLED")
    os.environ["KNN_ENABLED"] = "false"
    try:
        results = messages_hybrid_search("OTC", limit=5)
        for r in results:
            assert "slug" not in r, "slug must not appear in output"
    finally:
        if orig is None:
            os.environ.pop("KNN_ENABLED", None)
        else:
            os.environ["KNN_ENABLED"] = orig


def test_messages_hybrid_server_handler():
    """server TOOLS has memocean_messages_search; MEMO-010: now pure BM25, KNN opt-in."""
    from memocean_mcp.server import TOOLS
    assert "memocean_messages_search" in TOOLS
    spec = TOOLS["memocean_messages_search"]
    # MEMO-010: description updated to reflect BM25-default + KNN opt-in
    assert "bm25" in spec["description"].lower() or "fts" in spec["description"].lower()


# ── ocean_search tests ─────────────────────────────────────────────────────


def test_ocean_search_missing_vault(tmp_path, monkeypatch):
    """ocean_search returns [] when Ocean vault path doesn't exist."""
    import memocean_mcp.tools.ocean_search as mod
    monkeypatch.setattr(mod, "OCEAN_PATH", str(tmp_path / "nonexistent/"))
    from memocean_mcp.tools.ocean_search import ocean_search
    assert ocean_search("ChannelLab") == []


def test_ocean_search_empty_query(tmp_path, monkeypatch):
    """ocean_search returns [] for empty query."""
    import memocean_mcp.tools.ocean_search as mod
    monkeypatch.setattr(mod, "OCEAN_PATH", str(tmp_path))
    from memocean_mcp.tools.ocean_search import ocean_search
    assert ocean_search("") == []
    assert ocean_search("   ") == []


def test_ocean_search_returns_list(tmp_path, monkeypatch):
    """ocean_search with real vault dir returns list (may be empty if no matches)."""
    import os
    import memocean_mcp.tools.ocean_search as mod
    ocean_dir = tmp_path / "Ocean"
    ocean_dir.mkdir()
    # Create a test .md file
    (ocean_dir / "Test Page.md").write_text("# Test Page\nChannelLab GEO 服務測試", encoding="utf-8")
    monkeypatch.setattr(mod, "OCEAN_PATH", str(ocean_dir) + "/")
    from memocean_mcp.tools.ocean_search import ocean_search
    results = ocean_search("ChannelLab GEO", limit=5)
    assert isinstance(results, list)


def test_ocean_search_result_schema(tmp_path, monkeypatch):
    """ocean_search result dicts have required fields."""
    import memocean_mcp.tools.ocean_search as mod
    ocean_dir = tmp_path / "Ocean"
    ocean_dir.mkdir()
    (ocean_dir / "MyPage.md").write_text("# MyPage\nHello World content", encoding="utf-8")
    monkeypatch.setattr(mod, "OCEAN_PATH", str(ocean_dir) + "/")
    from memocean_mcp.tools.ocean_search import ocean_search
    results = ocean_search("Hello World", limit=5)
    if results:
        r = results[0]
        assert "title" in r
        assert "wikilink" in r
        assert r["wikilink"].startswith("[[")
        assert r["wikilink"].endswith("]]")
        assert "excerpt" in r
        assert "path" in r
        assert r["source"] == "ocean"


def test_ocean_search_no_personal_vault_leak(tmp_path, monkeypatch):
    """ocean_search scope is limited to Ocean path, not parent directories."""
    import memocean_mcp.tools.ocean_search as mod
    ocean_dir = tmp_path / "Ocean"
    ocean_dir.mkdir()
    personal_dir = tmp_path / "OldRabbit"
    personal_dir.mkdir()
    (personal_dir / "Private.md").write_text("private secret content", encoding="utf-8")
    (ocean_dir / "Public.md").write_text("public content", encoding="utf-8")
    monkeypatch.setattr(mod, "OCEAN_PATH", str(ocean_dir) + "/")
    from memocean_mcp.tools.ocean_search import ocean_search
    results = ocean_search("private secret", limit=5)
    paths = [r["path"] for r in results]
    assert not any("OldRabbit" in p or "Private" in p for p in paths)


def test_ocean_search_via_server_tool():
    """memocean_ocean_search MCP tool exists and returns proper schema."""
    from memocean_mcp.server import TOOLS
    assert "memocean_ocean_search" in TOOLS
    tool = TOOLS["memocean_ocean_search"]
    assert "query" in tool["input_schema"]["properties"]
    assert callable(tool["handler"])


# ==================== MEMO-010: unified_search tests ====================

def test_unified_search_returns_list():
    """memocean_search returns a list (empty is fine when no data)."""
    from memocean_mcp.tools.unified_search import memocean_search
    result = memocean_search("test query")
    assert isinstance(result, list)


def test_unified_search_empty_query():
    """Empty query returns empty list without error."""
    from memocean_mcp.tools.unified_search import memocean_search
    assert memocean_search("") == []
    assert memocean_search("   ") == []


def test_unified_search_result_schema(tmp_path, monkeypatch):
    """Results from Ocean layer have required schema fields."""
    import memocean_mcp.tools.ocean_search as ocean_mod
    ocean_dir = tmp_path / "Ocean"
    ocean_dir.mkdir()
    (ocean_dir / "SchemaTest.md").write_text("# SchemaTest\nunified search test content", encoding="utf-8")
    monkeypatch.setattr(ocean_mod, "OCEAN_PATH", str(ocean_dir) + "/")
    from memocean_mcp.tools.unified_search import memocean_search
    results = memocean_search("unified search test content", source="ocean")
    if results:
        r = results[0]
        for field in ("title", "excerpt", "source", "ref", "score_rank", "wikilink", "path", "drawer_path"):
            assert field in r, f"Missing field: {field}"
        assert r["source"] == "ocean"
        assert isinstance(r["score_rank"], int)


def test_unified_search_ocean_before_messages(tmp_path, monkeypatch):
    """Ocean results appear before messages results (source priority order)."""
    import memocean_mcp.tools.ocean_search as ocean_mod
    ocean_dir = tmp_path / "Ocean"
    ocean_dir.mkdir()
    (ocean_dir / "PriorityPage.md").write_text("# PriorityPage\npriority test CHL GEO", encoding="utf-8")
    monkeypatch.setattr(ocean_mod, "OCEAN_PATH", str(ocean_dir) + "/")

    from memocean_mcp.tools.unified_search import _SOURCE_PRIORITY
    assert _SOURCE_PRIORITY["ocean"] > _SOURCE_PRIORITY["messages"]


def test_unified_search_source_ocean_only(tmp_path, monkeypatch):
    """source='ocean' only searches Ocean vault, not messages."""
    import memocean_mcp.tools.ocean_search as ocean_mod
    ocean_dir = tmp_path / "Ocean"
    ocean_dir.mkdir()
    (ocean_dir / "OceanOnly.md").write_text("# OceanOnly\nocean only query", encoding="utf-8")
    monkeypatch.setattr(ocean_mod, "OCEAN_PATH", str(ocean_dir) + "/")

    from memocean_mcp.tools.unified_search import memocean_search
    results = memocean_search("ocean only query", source="ocean")
    for r in results:
        assert r["source"] == "ocean"


def test_unified_search_dedup():
    """_merge_and_rank deduplicates by (source, ref)."""
    from memocean_mcp.tools.unified_search import _merge_and_rank
    dup = {"title": "T", "excerpt": "e", "source": "ocean", "ref": "Ocean/T.md",
           "score_rank": 1, "wikilink": "[[T]]", "path": "Ocean/T.md", "drawer_path": ""}
    results = _merge_and_rank([dup, dup], [], [], limit=10)
    assert len(results) == 1


def test_unified_search_via_server():
    """memocean_search tool is registered in TOOLS with correct schema."""
    from memocean_mcp.server import TOOLS
    assert "memocean_search" in TOOLS
    tool = TOOLS["memocean_search"]
    assert "query" in tool["input_schema"]["properties"]
    assert "source" in tool["input_schema"]["properties"]
    assert "limit" in tool["input_schema"]["properties"]
    assert callable(tool["handler"])


def test_knn_disabled_by_default():
    """KNN_ENABLED defaults to false — BGE-m3 must not load by default."""
    import os
    env_val = os.environ.get("KNN_ENABLED", "false")
    assert env_val.lower() in ("false", "0", "no"), (
        "KNN_ENABLED should default to 'false'; "
        "if set, must not be true in production config"
    )
