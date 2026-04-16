"""
test_ocean_seabed.py — Unit + integration tests for ocean_seabed_write.py and
ocean_seabed_rebuild.py.

Tests are self-contained where possible; integration tests skip gracefully
when real data is not available.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Make sure scripts dir is importable
SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from ocean_seabed_write import (
    get_chat_name,
    seabed_file_path,
    write_message_to_seabed,
    backfill_from_sqlite,
    OCEAN_SEABED,
)
from ocean_seabed_rebuild import (
    parse_seabed_file,
    rebuild_messages_list,
    verify_against_sqlite,
)


# ── Unit tests: get_chat_name ──────────────────────────────────────────────────


def test_get_chat_name_known_private():
    assert get_chat_name("1050312492") == "oldrabbit-private"


def test_get_chat_name_known_group():
    assert get_chat_name("-1003634255226") == "team-main"


def test_get_chat_name_unknown_negative():
    name = get_chat_name("-9999999999")
    assert "9999999999" in name or "neg" in name


def test_get_chat_name_unknown_positive():
    name = get_chat_name("9876543210")
    assert "9876543210" in name


def test_get_chat_name_coordinator():
    assert get_chat_name("-5175060310") == "coordinator"


# ── Unit tests: seabed_file_path ───────────────────────────────────────────────


def test_seabed_file_path_format():
    path = seabed_file_path("1050312492", "2026-04-15T09:30:00.000Z")
    assert path.name == "2026-04-15-oldrabbit-private.md"
    assert "2026-04" in str(path)


def test_seabed_file_path_month_dir():
    path = seabed_file_path("-1003634255226", "2026-03-25T12:00:00.000Z")
    assert path.parent.name == "2026-03"
    assert path.name == "2026-03-25-team-main.md"


def test_seabed_file_path_unknown_chat():
    path = seabed_file_path("-5999999999", "2026-04-01T00:00:00.000Z")
    assert "2026-04-01" in path.name
    assert path.name.endswith(".md")


# ── Unit tests: write_message_to_seabed ───────────────────────────────────────


@pytest.fixture
def tmp_seabed(tmp_path, monkeypatch):
    """Redirect OCEAN_SEABED to a temp dir for isolated tests."""
    import ocean_seabed_write as osw
    monkeypatch.setattr(osw, "OCEAN_SEABED", tmp_path)
    return tmp_path


def _make_msg(**kwargs):
    defaults = {
        "bot_name": "test_bot",
        "ts": "2026-04-15T09:30:00.000Z",
        "source": "telegram",
        "chat_id": "1050312492",
        "user": "oldrabbit_eth",
        "message_id": "12345",
        "text": "Hello test message",
    }
    defaults.update(kwargs)
    return defaults


def test_write_creates_file(tmp_seabed):
    msg = _make_msg()
    result = write_message_to_seabed(msg)
    assert result is True

    expected_path = tmp_seabed / "2026-04" / "2026-04-15-oldrabbit-private.md"
    assert expected_path.exists()


def test_write_frontmatter(tmp_seabed):
    msg = _make_msg()
    write_message_to_seabed(msg)

    path = tmp_seabed / "2026-04" / "2026-04-15-oldrabbit-private.md"
    content = path.read_text()
    assert "type: seabed" in content
    assert 'chat_id: "1050312492"' in content
    assert "chat_name: oldrabbit-private" in content
    assert "date: 2026-04-15" in content
    assert "source: telegram" in content


def test_write_message_line(tmp_seabed):
    msg = _make_msg()
    write_message_to_seabed(msg)

    path = tmp_seabed / "2026-04" / "2026-04-15-oldrabbit-private.md"
    content = path.read_text()
    assert "09:30 [oldrabbit_eth] Hello test message" in content
    assert "<!-- mid:12345 -->" in content


def test_write_dedup(tmp_seabed):
    msg = _make_msg()
    r1 = write_message_to_seabed(msg)
    r2 = write_message_to_seabed(msg)  # same message_id
    assert r1 is True
    assert r2 is False  # deduped


def test_write_dedup_only_one_line(tmp_seabed):
    msg = _make_msg()
    write_message_to_seabed(msg)
    write_message_to_seabed(msg)  # try to write again

    path = tmp_seabed / "2026-04" / "2026-04-15-oldrabbit-private.md"
    content = path.read_text()
    # Message line should appear exactly once
    assert content.count("<!-- mid:12345 -->") == 1


def test_write_multiple_messages(tmp_seabed):
    msgs = [
        _make_msg(message_id="1", text="First message", ts="2026-04-15T09:00:00.000Z"),
        _make_msg(message_id="2", text="Second message", ts="2026-04-15T09:01:00.000Z"),
        _make_msg(message_id="3", text="Third message", ts="2026-04-15T09:02:00.000Z"),
    ]
    for msg in msgs:
        write_message_to_seabed(msg)

    path = tmp_seabed / "2026-04" / "2026-04-15-oldrabbit-private.md"
    content = path.read_text()
    assert content.count("<!-- mid:") == 3


def test_write_skip_empty_chat_id(tmp_seabed):
    msg = _make_msg(chat_id="")
    result = write_message_to_seabed(msg)
    assert result is False


def test_write_skip_system_chat(tmp_seabed):
    msg = _make_msg(chat_id="system")
    result = write_message_to_seabed(msg)
    assert result is False


def test_write_skip_self_chat(tmp_seabed):
    msg = _make_msg(chat_id="self")
    result = write_message_to_seabed(msg)
    assert result is False


def test_write_skip_empty_text(tmp_seabed):
    msg = _make_msg(text="")
    result = write_message_to_seabed(msg)
    assert result is False


def test_write_newlines_in_text_sanitized(tmp_seabed):
    msg = _make_msg(text="Line one\nLine two\nLine three")
    write_message_to_seabed(msg)

    path = tmp_seabed / "2026-04" / "2026-04-15-oldrabbit-private.md"
    lines = path.read_text().splitlines()
    # The message line should be a single line (newlines replaced with spaces)
    msg_lines = [l for l in lines if "<!-- mid:" in l]
    assert len(msg_lines) == 1
    assert "Line one Line two" in msg_lines[0]


def test_write_different_chats_different_files(tmp_seabed):
    msg1 = _make_msg(chat_id="1050312492", message_id="1")
    msg2 = _make_msg(chat_id="-1003634255226", message_id="2")
    write_message_to_seabed(msg1)
    write_message_to_seabed(msg2)

    p1 = tmp_seabed / "2026-04" / "2026-04-15-oldrabbit-private.md"
    p2 = tmp_seabed / "2026-04" / "2026-04-15-team-main.md"
    assert p1.exists()
    assert p2.exists()


def test_write_different_dates_different_files(tmp_seabed):
    msg1 = _make_msg(ts="2026-04-14T09:00:00.000Z", message_id="1")
    msg2 = _make_msg(ts="2026-04-15T09:00:00.000Z", message_id="2")
    write_message_to_seabed(msg1)
    write_message_to_seabed(msg2)

    p1 = tmp_seabed / "2026-04" / "2026-04-14-oldrabbit-private.md"
    p2 = tmp_seabed / "2026-04" / "2026-04-15-oldrabbit-private.md"
    assert p1.exists()
    assert p2.exists()


# ── Unit tests: backfill_from_sqlite ──────────────────────────────────────────


def _make_test_db(tmp_path) -> str:
    """Create a minimal test SQLite FTS5 DB with some messages."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE VIRTUAL TABLE messages USING fts5("
        "bot_name UNINDEXED, ts UNINDEXED, source UNINDEXED, "
        "chat_id UNINDEXED, user UNINDEXED, message_id UNINDEXED, text, "
        "tokenize='trigram case_sensitive 0')"
    )
    conn.execute("CREATE TABLE seen(key TEXT PRIMARY KEY)")
    rows = [
        ("bot1", "2026-04-15T09:00:00.000Z", "telegram", "1050312492", "alice", "1", "Hello"),
        ("bot1", "2026-04-15T09:01:00.000Z", "telegram", "1050312492", "bob", "2", "World"),
        ("bot1", "2026-04-15T09:02:00.000Z", "telegram", "-1003634255226", "charlie", "3", "Group msg"),
        ("bot1", "2026-04-15T09:03:00.000Z", "telegram", "self", "system", "4", "Internal"),
        ("bot1", "2026-04-15T09:04:00.000Z", "telegram", "", "anon", "5", "No chat"),
    ]
    conn.executemany(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    return db_path


def test_backfill_from_sqlite(tmp_path, monkeypatch):
    import ocean_seabed_write as osw
    tmp_seabed = tmp_path / "Seabed"
    tmp_seabed.mkdir()
    monkeypatch.setattr(osw, "OCEAN_SEABED", tmp_seabed)

    db_path = _make_test_db(tmp_path)
    stats = backfill_from_sqlite(db_path=db_path, verbose=False)

    assert stats["total"] == 5
    assert stats["written"] == 3   # self + empty chat_id are skipped
    assert stats["skipped"] == 2
    assert stats["errors"] == 0


def test_backfill_dedup_on_rerun(tmp_path, monkeypatch):
    import ocean_seabed_write as osw
    tmp_seabed = tmp_path / "Seabed"
    tmp_seabed.mkdir()
    monkeypatch.setattr(osw, "OCEAN_SEABED", tmp_seabed)

    db_path = _make_test_db(tmp_path)
    stats1 = backfill_from_sqlite(db_path=db_path, verbose=False)
    stats2 = backfill_from_sqlite(db_path=db_path, verbose=False)  # re-run

    assert stats1["written"] == 3
    assert stats2["written"] == 0   # all deduped
    assert stats2["skipped"] == 5   # 3 deduped + 2 filtered


# ── Unit tests: parse_seabed_file ─────────────────────────────────────────────


def test_parse_seabed_file(tmp_path):
    content = (
        "---\n"
        'type: seabed\n'
        'chat_id: "1050312492"\n'
        'chat_name: oldrabbit-private\n'
        'date: 2026-04-15\n'
        'source: telegram\n'
        "---\n"
        "- 09:30 [alice] Hello world <!-- mid:100 -->\n"
        "- 09:31 [bob] How are you <!-- mid:101 -->\n"
        "# This is a section header (should be ignored)\n"
    )
    f = tmp_path / "2026-04-15-oldrabbit-private.md"
    f.write_text(content)

    msgs = parse_seabed_file(f)
    assert len(msgs) == 2
    assert msgs[0]["user"] == "alice"
    assert msgs[0]["text"] == "Hello world"
    assert msgs[0]["message_id"] == "100"
    assert msgs[0]["chat_id"] == "1050312492"
    assert msgs[0]["ts"] == "2026-04-15T09:30:00.000Z"
    assert msgs[1]["user"] == "bob"
    assert msgs[1]["message_id"] == "101"


def test_parse_seabed_file_missing_file(tmp_path):
    msgs = parse_seabed_file(tmp_path / "nonexistent.md")
    assert msgs == []


# ── Unit tests: rebuild_messages_list ─────────────────────────────────────────


def test_rebuild_messages_list(tmp_path):
    for fname, content in [
        ("2026-04/2026-04-15-oldrabbit-private.md",
         "---\ntype: seabed\nchat_id: \"1050312492\"\nchat_name: oldrabbit-private\n"
         "date: 2026-04-15\nsource: telegram\n---\n"
         "- 09:00 [alice] Msg1 <!-- mid:1 -->\n"),
        ("2026-04/2026-04-15-team-main.md",
         "---\ntype: seabed\nchat_id: \"-1003634255226\"\nchat_name: team-main\n"
         "date: 2026-04-15\nsource: telegram\n---\n"
         "- 10:00 [bob] Msg2 <!-- mid:2 -->\n"
         "- 10:01 [carol] Msg3 <!-- mid:3 -->\n"),
    ]:
        p = tmp_path / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    msgs = rebuild_messages_list(tmp_path)
    assert len(msgs) == 3
    # Should be sorted by ts
    assert msgs[0]["message_id"] == "1"
    assert msgs[1]["message_id"] == "2"


# ── Integration tests (skip if real data not available) ───────────────────────


REAL_DB = Path(os.path.expanduser("~/.claude-bots/memory.db"))
REAL_SEABED = OCEAN_SEABED


@pytest.mark.skipif(
    not REAL_SEABED.exists() or not any(REAL_SEABED.rglob("*.md")),
    reason="Real Ocean/Seabed not populated (run backfill first)"
)
def test_integration_seabed_has_files():
    """Integration: Ocean/Seabed should have at least one .md file after backfill."""
    md_files = list(REAL_SEABED.rglob("*.md"))
    # Filter out chats.clsc.md
    seabed_files = [f for f in md_files if "chats.clsc" not in f.name]
    assert len(seabed_files) > 0, "No Seabed .md files found"


@pytest.mark.skipif(
    not REAL_SEABED.exists() or not any(REAL_SEABED.rglob("*.md")),
    reason="Real Ocean/Seabed not populated"
)
def test_integration_seabed_frontmatter_valid():
    """Integration: All Seabed .md files should have valid frontmatter."""
    md_files = [
        f for f in REAL_SEABED.rglob("*.md")
        if "chats.clsc" not in f.name
    ]
    for f in md_files[:10]:  # spot check first 10
        content = f.read_text(encoding="utf-8")
        assert content.startswith("---"), f"{f} missing frontmatter"
        assert "type: seabed" in content, f"{f} missing type: seabed"
        assert "source: telegram" in content, f"{f} missing source"


@pytest.mark.skipif(
    not REAL_DB.exists() or not REAL_SEABED.exists(),
    reason="Real DB or Seabed not available"
)
def test_integration_seabed_coverage():
    """Integration: Seabed should cover at least 95% of non-system messages in DB."""
    msgs = rebuild_messages_list(REAL_SEABED)
    stats = verify_against_sqlite(msgs, db_path=str(REAL_DB))

    # Accept that self/system/empty chat_id messages won't be in Seabed
    assert stats["coverage_pct"] >= 95.0, (
        f"Seabed coverage {stats['coverage_pct']}% < 95% expected. "
        f"Missing {stats['in_db_not_seabed']} messages."
    )


@pytest.mark.skipif(
    not REAL_SEABED.exists(),
    reason="Real Ocean/Seabed not populated"
)
def test_integration_rebuild_roundtrip():
    """Integration: rebuild_messages_list reads back what backfill wrote."""
    msgs = rebuild_messages_list(REAL_SEABED)
    assert len(msgs) > 1000, f"Expected >1000 messages, got {len(msgs)}"
    # Verify message structure
    sample = msgs[0]
    assert "chat_id" in sample
    assert "message_id" in sample
    assert "user" in sample
    assert "text" in sample
    assert "ts" in sample
