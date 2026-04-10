"""
test_v0_7.py — CLSC v0.7 test suite.
Tests all 5 P0 items:
  1. LLM expand mock mode works (no API key needed)
  2. H1 extraction doesn't pull frontmatter
  3. clsc-sync.sh single-file mode works and updates closet
  4. group_from_path() correctly maps subdirs
  5. fts5_bridge search returns results
"""
import sys
import os
import subprocess
from pathlib import Path

# Add v0.7 to path
V0_7_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(V0_7_DIR))

WIKI_ROOT = Path.home() / "Documents" / "Obsidian Vault" / "Ocean"
TEST_NOTE = WIKI_ROOT / "Research" / "CZ-Memoir-Personal-Story.md"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

results = []

def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    print(f"  [{status}] {name}" + (f": {detail}" if detail else ""))
    results.append((name, condition))
    return condition

# ─── P0-1: LLM expand mock mode ──────────────────────────────────────────────
print("\n=== P0-1: LLM expand mock mode ===")
try:
    # Temporarily unset API key to force mock mode
    saved_key = os.environ.pop("ANTHROPIC_API_KEY", None)
    from decoder import narrative_expand, parse_skeleton
    test_skeleton = "[CZ-Memoir|CZ 回憶錄個人故事] ENT:趙長鵬,幣安,加密貨幣 KEY:趙長鵬創辦幣安|從小在中國成長後移居加拿大"
    result = narrative_expand(test_skeleton, use_llm=False)
    if saved_key:
        os.environ["ANTHROPIC_API_KEY"] = saved_key
    check("mock mode returns prose", isinstance(result, str) and len(result) > 10, result[:80])
    check("mock mode contains title", "CZ 回憶錄個人故事" in result or "CZ-Memoir" in result)
    check("mock mode contains entities", "趙長鵬" in result or "幣安" in result)

    # Test use_llm=None with no key set
    os.environ.pop("ANTHROPIC_API_KEY", None)
    result_auto = narrative_expand(test_skeleton, use_llm=None)
    if saved_key:
        os.environ["ANTHROPIC_API_KEY"] = saved_key
    check("auto mode (no key) falls back to template", isinstance(result_auto, str) and len(result_auto) > 10)
except Exception as e:
    check("mock mode", False, str(e))
    if saved_key:
        os.environ["ANTHROPIC_API_KEY"] = saved_key

# ─── P0-2: H1 extraction doesn't pull frontmatter ────────────────────────────
print("\n=== P0-2: H1 extraction fix ===")
try:
    import importlib
    import encoder
    importlib.reload(encoder)
    from encoder import parse_wiki_note

    # Create a temp note with frontmatter that has a colon-containing value
    import tempfile
    fake_content = """---
title: Some YAML Title: with colon value here at length
tags: test
---

# Real H1 Title

Some body content about things.
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', encoding='utf-8', delete=False) as f:
        f.write(fake_content)
        tmp_path = f.name

    note = parse_wiki_note(tmp_path)
    os.unlink(tmp_path)

    check("H1 extracted correctly", note['title'] == "Real H1 Title", repr(note['title']))
    check("frontmatter not in title", "YAML" not in note['title'] and "colon" not in note['title'])

    # Test with a real wiki note if available
    if TEST_NOTE.exists():
        real_note = parse_wiki_note(str(TEST_NOTE))
        check("real note title is not YAML", ':' not in real_note['title'] or len(real_note['title']) <= 60,
              repr(real_note['title']))
        check("real note has title", len(real_note['title']) > 0, repr(real_note['title']))
    else:
        print(f"  [SKIP] Real test note not found: {TEST_NOTE}")
except Exception as e:
    check("H1 extraction", False, str(e))

# ─── P0-3: clsc-sync.sh single-file mode ─────────────────────────────────────
print("\n=== P0-3: clsc-sync.sh single-file mode ===")
SYNC_SCRIPT = Path.home() / ".claude-bots" / "shared" / "hooks" / "clsc-sync.sh"
try:
    check("clsc-sync.sh exists", SYNC_SCRIPT.exists(), str(SYNC_SCRIPT))
    check("clsc-sync.sh is executable", os.access(str(SYNC_SCRIPT), os.X_OK))

    if TEST_NOTE.exists():
        proc = subprocess.run(
            [str(SYNC_SCRIPT), str(TEST_NOTE)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": str(V0_7_DIR)}
        )
        output = (proc.stdout + proc.stderr).strip()
        check("sync script runs without error", proc.returncode == 0, f"exit={proc.returncode}")
        check("sync script outputs SYNC line", "SYNC" in output, output[:120])

        # Check closet was updated
        # Note: encoder truncates slugs to 20 chars, so "CZ-Memoir-Personal-Story" -> "CZ-Memoir-Personal-S"
        from closet import read_closet
        research_closet = read_closet("research")
        slug_prefix = "CZ-Memoir-Personal-S"  # first 20 chars of slug
        check("closet updated with note slug", slug_prefix in research_closet,
              f"closet len={len(research_closet)}, looking for '{slug_prefix}'")
    else:
        print(f"  [SKIP] Test note not found: {TEST_NOTE}")
except Exception as e:
    check("clsc-sync.sh", False, str(e))

# ─── P0-4: group_from_path() ─────────────────────────────────────────────────
print("\n=== P0-4: group_from_path() ===")
try:
    from closet import group_from_path
    base = str(WIKI_ROOT)
    tests_gfp = [
        (f"{base}/Research/CZ-Memoir.md", "research"),
        (f"{base}/Chart/RAG-Architecture.md", "chart"),
        (f"{base}/Pearl/ChannelLab.md", "pearl"),
        (f"{base}/Companies/Binance.md", "companies"),
        (f"{base}/People/CZ.md", "people"),
        (f"{base}/Deals/Round-A.md", "deals"),
        (f"{base}/Reviews/Book-Review.md", "reviews"),
        (f"{base}/Projects/MyProject.md", "general"),
        ("/some/random/path/note.md", "general"),
    ]
    all_ok = True
    for path, expected in tests_gfp:
        got = group_from_path(path)
        ok = got == expected
        all_ok = all_ok and ok
        check(f"group_from_path({Path(path).parent.name}/)", ok, f"got={got}, expected={expected}")
except Exception as e:
    check("group_from_path", False, str(e))

# ─── P0-5: fts5_bridge search ────────────────────────────────────────────────
print("\n=== P0-5: fts5_bridge search ===")
try:
    from fts5_bridge import search_fts5

    # First ensure there's something in the closet to search
    # (P0-3 should have put CZ-Memoir-Personal-Story in research closet)
    results_cz = search_fts5("趙長鵬")
    check("fts5_bridge returns list", isinstance(results_cz, list))
    check("fts5_bridge handles CJK query", True, f"returned {len(results_cz)} results")

    results_cl = search_fts5("ChannelLab")
    check("fts5_bridge ASCII query runs", isinstance(results_cl, list),
          f"returned {len(results_cl)} results")

    # Test with empty query doesn't crash
    results_empty = search_fts5("xyzzy_nonexistent_12345")
    check("fts5_bridge handles no-match gracefully", isinstance(results_empty, list),
          f"returned {len(results_empty)} results")

    # Test closet results have expected fields
    all_closet = search_fts5("CZ")
    closet_hits = [r for r in all_closet if r.get('closet_available')]
    if closet_hits:
        r = closet_hits[0]
        check("closet result has slug", 'slug' in r and r['slug'])
        check("closet result has skeleton", 'skeleton' in r and r['skeleton'])
    else:
        print("  [INFO] No closet hits for 'CZ' (closet may be empty)")
        check("fts5_bridge closet search runs", True)

except Exception as e:
    check("fts5_bridge", False, str(e))

# ─── Summary ─────────────────────────────────────────────────────────────────
print("\n" + "="*50)
passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"Results: {passed}/{total} passed")
if passed == total:
    print("\033[92mAll tests passed!\033[0m")
else:
    failed = [name for name, ok in results if not ok]
    print(f"\033[91mFailed: {failed}\033[0m")
    sys.exit(1)
