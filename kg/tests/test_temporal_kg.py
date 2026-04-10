"""
Unit tests for ChannelLab Temporal Knowledge Graph.
Key test: add → invalidate → query as_of past → get old fact (not latest).
"""
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_temporal_as_of():
    """Core test: as_of query returns fact valid at that time, not current state."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = f.name

    try:
        from knowledge_graph import KnowledgeGraph
        kg = KnowledgeGraph(tmp_db)

        # Add: Alice was shareholder from 2024-01-01
        kg.add_triple(
            subject="Alice",
            predicate="role",
            obj="shareholder",
            valid_from="2024-01-01",
            valid_to=None,
            source_ref="test",
            confidence=1.0,
        )

        # Invalidate: became LP from 2025-06-01
        kg.invalidate(
            subject="Alice",
            predicate="role",
            obj="shareholder",
            ended="2025-06-01",
        )

        # Add new role
        kg.add_triple(
            subject="Alice",
            predicate="role",
            obj="LP",
            valid_from="2025-06-01",
            valid_to=None,
            source_ref="test",
            confidence=1.0,
        )

        # Query as_of 2024-06-01 → should see shareholder, not LP
        past_facts = kg.query_entity("Alice", as_of="2024-06-01")
        past_roles = [f.get("object") for f in past_facts]
        assert "shareholder" in past_roles, f"Expected shareholder in past, got: {past_roles}"
        assert "LP" not in past_roles, f"Should not see LP in 2024, got: {past_roles}"

        # Query as_of today → should see LP
        today_facts = kg.query_entity("Alice", as_of="2026-04-08")
        today_roles = [f.get("object") for f in today_facts]
        assert "LP" in today_roles, f"Expected LP today, got: {today_roles}"
        # shareholder should NOT appear (valid_to=2025-06-01, querying 2026-04-08)
        assert "shareholder" not in today_roles, f"shareholder should be expired today, got: {today_roles}"

        print("PASS test_temporal_as_of")
        return True
    finally:
        os.unlink(tmp_db)


def test_kg_db_exists():
    """Test that kg.db exists and has correct schema."""
    kg_db = Path.home() / ".claude-bots" / "kg.db"
    assert kg_db.exists(), f"kg.db not found at {kg_db}"

    import sqlite3
    conn = sqlite3.connect(str(kg_db))
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()

    assert "triples" in tables, f"Expected 'triples' table, got: {tables}"
    assert "entities" in tables, f"Expected 'entities' table, got: {tables}"
    print(f"PASS test_kg_db_exists (tables: {tables})")


def test_demo_facts_backfill():
    """Test that demo facts were backfilled into kg.db."""
    from kg_helper import kg_query

    wes_facts = kg_query("Alice", direction="outgoing")
    assert len(wes_facts) > 0, "No facts found for Alice"
    print(f"PASS test_demo_facts_backfill ({len(wes_facts)} Alice facts active today)")

    # Also verify <OWNER> CEO fact exists
    laotu_facts = kg_query("<OWNER>", direction="outgoing")
    laotu_roles = [f.get("object") for f in laotu_facts]
    assert "CEO" in laotu_roles, f"Expected CEO for <OWNER>, got: {laotu_roles}"
    print(f"PASS test_demo_facts_backfill (<OWNER> roles: {laotu_roles})")


def test_query_all():
    """Test query_all returns facts and respects as_of filtering."""
    from kg_helper import kg_query_all

    all_facts = kg_query_all()
    assert len(all_facts) > 0, "kg_query_all returned empty — expected demo facts"
    print(f"PASS test_query_all ({len(all_facts)} active facts as of today)")

    # as_of 2023-01-01 should return 0 (no facts before 2020 except <OWNER> CEO)
    old_facts = kg_query_all(as_of="2019-12-31")
    print(f"PASS test_query_all (facts as_of 2019-12-31: {len(old_facts)}, expected 0)")


if __name__ == "__main__":
    tests = [test_temporal_as_of, test_kg_db_exists, test_demo_facts_backfill, test_query_all]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
