"""
test_ocean_search_gbrain.py — Unit tests for GBrain delegate in ocean_search.

Tests:
  - _parse_gbrain_output: 0/1/3 results, multiline content
  - Feature flag routing: flag=false→bm25, flag=true+healthy→gbrain, flag=true+timeout→bm25
  - Privacy gate: assert_under_ocean passes/raises
  - Fault injection: timeout, exit≠0, empty output, binary missing
"""
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure package is importable from this test file's location
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from memocean_mcp.tools.ocean_search import (
    GBrainUnhealthy,
    _gbrain_search,
    _legacy_bm25_search,
    _normalize_gbrain,
    _parse_gbrain_output,
    ocean_search,
)
from memocean_mcp.privacy import PrivacyViolation, assert_under_ocean, OCEAN_VAULT_ABSOLUTE_PATH


class TestParseGBrainOutput(unittest.TestCase):

    def test_zero_results(self):
        self.assertEqual(_parse_gbrain_output(""), [])
        self.assertEqual(_parse_gbrain_output("   \n  "), [])

    def test_single_result(self):
        raw = "[0.85] chart/memocean/memocean -- # MemOcean MCP\n\nSome content here."
        results = _parse_gbrain_output(raw)
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0]["_score"], 0.85)
        self.assertEqual(results[0]["_slug"], "chart/memocean/memocean")
        self.assertIn("MemOcean", results[0]["_content"])

    def test_three_results(self):
        raw = (
            "[0.90] pearl/foo -- First result\n"
            "[0.80] chart/bar -- Second result\nmultiline\n"
            "[0.70] currents/baz -- Third result"
        )
        results = _parse_gbrain_output(raw)
        self.assertEqual(len(results), 3)
        self.assertAlmostEqual(results[0]["_score"], 0.90)
        self.assertEqual(results[1]["_slug"], "chart/bar")
        self.assertIn("multiline", results[1]["_content"])
        self.assertEqual(results[2]["_slug"], "currents/baz")

    def test_multiline_content_capped_at_2000(self):
        long_content = "x" * 3000
        raw = f"[0.75] some/slug -- {long_content}"
        results = _parse_gbrain_output(raw)
        self.assertEqual(len(results), 1)
        self.assertLessEqual(len(results[0]["_content"]), 2000)

    def test_integer_score(self):
        raw = "[1] some/slug -- content"
        results = _parse_gbrain_output(raw)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["_score"], 1.0)


class TestNormalizeGBrain(unittest.TestCase):

    def test_backward_compat_fields_present(self):
        raw = [{"_score": 0.8, "_slug": "chart/memocean/memocean", "_content": "# MemOcean\nBody text"}]
        with patch("memocean_mcp.slug_mapper.slug_to_path", return_value=None):
            results = _normalize_gbrain(raw)
        self.assertEqual(len(results), 1)
        r = results[0]
        # New schema fields
        self.assertIn("slug", r)
        self.assertIn("content", r)
        self.assertIn("score", r)
        self.assertIn("source", r)
        self.assertEqual(r["source"], "gbrain")
        # Backward-compat fields
        self.assertIn("title", r)
        self.assertIn("wikilink", r)
        self.assertIn("excerpt", r)
        self.assertEqual(r["title"], "MemOcean")
        self.assertTrue(r["wikilink"].startswith("[["))

    def test_excerpt_capped_at_200(self):
        long = "x" * 300
        raw = [{"_score": 0.5, "_slug": "a/b", "_content": long}]
        with patch("memocean_mcp.slug_mapper.slug_to_path", return_value=None):
            results = _normalize_gbrain(raw)
        self.assertLessEqual(len(results[0]["excerpt"]), 200)


class TestOceanSearchFlagRouting(unittest.TestCase):

    def _ocean_search_with_gbrain(self, healthy=True, side_effect=None, return_value=None):
        """Helper: run ocean_search with MEMOCEAN_USE_GBRAIN=true and mocked internals."""
        import memocean_mcp.tools.ocean_search as mod
        original_healthy = mod._gbrain_healthy
        mod._gbrain_healthy = healthy
        try:
            with patch.dict(os.environ, {"MEMOCEAN_USE_GBRAIN": "true"}):
                if side_effect:
                    with patch.object(mod, "_gbrain_search", side_effect=side_effect):
                        with patch.object(mod, "_legacy_bm25_search", return_value=[{"source": "bm25_fallback"}]):
                            return ocean_search("test query")
                else:
                    with patch.object(mod, "_gbrain_search", return_value=return_value or []):
                        with patch.object(mod, "_legacy_bm25_search", return_value=[{"source": "bm25_fallback"}]):
                            return ocean_search("test query")
        finally:
            mod._gbrain_healthy = original_healthy

    def test_flag_false_uses_bm25(self):
        import memocean_mcp.tools.ocean_search as mod
        with patch.dict(os.environ, {"MEMOCEAN_USE_GBRAIN": "false"}):
            with patch.object(mod, "_gbrain_search") as mock_gbrain:
                with patch.object(mod, "_legacy_bm25_search", return_value=[{"source": "bm25"}]):
                    results = ocean_search("test")
        mock_gbrain.assert_not_called()
        self.assertTrue(all(r.get("source") == "bm25" for r in results))

    def test_flag_true_healthy_uses_gbrain(self):
        gbrain_result = [{"slug": "a/b", "source": "gbrain", "content": "x", "score": 0.9,
                          "path": "a/b", "title": "B", "wikilink": "[[B]]", "excerpt": "x"}]
        results = self._ocean_search_with_gbrain(healthy=True, return_value=gbrain_result)
        self.assertEqual(results[0]["source"], "gbrain")

    def test_flag_true_unhealthy_falls_back(self):
        results = self._ocean_search_with_gbrain(healthy=False)
        self.assertEqual(results[0]["source"], "bm25_fallback")

    def test_timeout_falls_back(self):
        results = self._ocean_search_with_gbrain(
            healthy=True, side_effect=subprocess.TimeoutExpired(["gbrain"], 3.0)
        )
        self.assertEqual(results[0]["source"], "bm25_fallback")

    def test_gbrain_unhealthy_exception_falls_back(self):
        results = self._ocean_search_with_gbrain(
            healthy=True, side_effect=GBrainUnhealthy("exit=1")
        )
        self.assertEqual(results[0]["source"], "bm25_fallback")

    def test_file_not_found_falls_back(self):
        results = self._ocean_search_with_gbrain(
            healthy=True, side_effect=FileNotFoundError("gbrain not found")
        )
        self.assertEqual(results[0]["source"], "bm25_fallback")

    def test_unexpected_exception_falls_back(self):
        results = self._ocean_search_with_gbrain(
            healthy=True, side_effect=RuntimeError("unexpected")
        )
        self.assertEqual(results[0]["source"], "bm25_fallback")

    def test_empty_query_returns_empty(self):
        self.assertEqual(ocean_search(""), [])
        self.assertEqual(ocean_search("   "), [])


class TestPrivacyGate(unittest.TestCase):

    def test_ocean_path_passes(self):
        ocean_subpath = os.path.join(OCEAN_VAULT_ABSOLUTE_PATH, "Chart", "test.md")
        # Mock realpath to return controlled value
        with patch("os.path.realpath", return_value=ocean_subpath):
            result = assert_under_ocean(ocean_subpath)
        self.assertEqual(result, ocean_subpath)

    def test_ocean_root_passes(self):
        with patch("os.path.realpath", return_value=OCEAN_VAULT_ABSOLUTE_PATH):
            result = assert_under_ocean(OCEAN_VAULT_ABSOLUTE_PATH)
        self.assertEqual(result, OCEAN_VAULT_ABSOLUTE_PATH)

    def test_oldrabbit_raises(self):
        oldrabbit_path = os.path.expanduser("~/Documents/Obsidian Vault - OldRabbit/secret.md")
        with self.assertRaises(PrivacyViolation):
            assert_under_ocean(oldrabbit_path)

    def test_parent_traversal_raises(self):
        traversal = os.path.join(OCEAN_VAULT_ABSOLUTE_PATH, "..", "OldRabbit", "secret.md")
        with self.assertRaises(PrivacyViolation):
            assert_under_ocean(traversal)

    def test_symlink_escape_blocked(self):
        # Simulate a symlink that resolves outside Ocean
        outside_path = os.path.expanduser("~/Documents/Obsidian Vault/Ocean-symlink")
        with patch("os.path.realpath", return_value=outside_path):
            with self.assertRaises(PrivacyViolation):
                assert_under_ocean(outside_path)


class TestGBrainSearchSubprocess(unittest.TestCase):
    """Tests for _gbrain_search() subprocess error handling."""

    def _run(self, returncode=0, stdout="", stderr="", timeout=False):
        import memocean_mcp.tools.ocean_search as mod
        if timeout:
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["gbrain"], 3.0)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    _gbrain_search("query", 5)
            return
        mock_result = MagicMock()
        mock_result.returncode = returncode
        mock_result.stdout = stdout
        mock_result.stderr = stderr
        with patch("subprocess.run", return_value=mock_result):
            return _gbrain_search("query", 5)

    def test_nonzero_exit_raises_gbrain_unhealthy(self):
        mock_result = MagicMock(returncode=1, stdout="", stderr="error msg")
        with patch("subprocess.run", return_value=mock_result):
            with self.assertRaises(GBrainUnhealthy):
                _gbrain_search("query", 5)

    def test_empty_output_returns_empty_list(self):
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            with patch("memocean_mcp.slug_mapper.slug_to_path", return_value=None):
                result = _gbrain_search("query", 5)
        self.assertEqual(result, [])

    def test_valid_output_parsed_correctly(self):
        stdout = "[0.75] chart/test -- Test content\nMore content"
        mock_result = MagicMock(returncode=0, stdout=stdout, stderr="")
        with patch("subprocess.run", return_value=mock_result):
            with patch("memocean_mcp.slug_mapper.slug_to_path", return_value=None):
                results = _gbrain_search("query", 5)
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0]["score"], 0.75)
        self.assertEqual(results[0]["slug"], "chart/test")
        self.assertEqual(results[0]["source"], "gbrain")


if __name__ == "__main__":
    unittest.main()
