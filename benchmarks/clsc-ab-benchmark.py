#!/usr/bin/env python3
"""
CLSC A/B Benchmark
Compare two indexing strategies:
  Group A — Stripped skeleton (no structure tags)
  Group B — Production CLSC skeleton (with tags, current baseline)
"""

import json
import re
import sqlite3
import sys
import os
from datetime import datetime

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
SNAPSHOT_DB = "/home/oldrabbit/.claude-bots/benchmarks/memory-snapshot-20260416.db"
LIVE_DB = "/home/oldrabbit/.claude-bots/memory.db"
QUESTIONS_FILE = "/home/oldrabbit/.claude-bots/benchmarks/work-scenario-200.json"
OUT_DIR = "/home/oldrabbit/.claude-bots/benchmarks"
K_VALUES = [1, 3, 5, 10]


# ─────────────────────────────────────────────
# Group A: build stripped in-memory FTS5 table
# ─────────────────────────────────────────────
def strip_clsc(clsc: str) -> str:
    """Remove structure tags from CLSC skeleton."""
    # Remove [SLUG|TITLE] prefix
    stripped = re.sub(r'^\[.*?\]\s*', '', clsc)
    # Remove label prefixes like ENT:, KEY:, TAG:
    stripped = re.sub(r'\b(ENT|KEY|TAG):', '', stripped)
    # Strip extra whitespace
    stripped = ' '.join(stripped.split())
    return stripped


def build_group_a_db(snapshot_conn) -> sqlite3.Connection:
    """Build in-memory SQLite FTS5 table with stripped CLSC content."""
    print("Building Group A in-memory DB (stripped skeletons)...")

    cur = snapshot_conn.cursor()
    cur.execute("SELECT slug, clsc FROM radar")
    rows = cur.fetchall()

    mem_conn = sqlite3.connect(":memory:")
    mem_cur = mem_conn.cursor()
    mem_cur.execute(
        "CREATE VIRTUAL TABLE radar_stripped USING fts5(slug, content, tokenize='trigram')"
    )

    for slug, clsc in rows:
        stripped = strip_clsc(clsc or "")
        mem_cur.execute(
            "INSERT INTO radar_stripped (slug, content) VALUES (?, ?)",
            (slug, stripped)
        )
    mem_conn.commit()

    mem_cur.execute("SELECT COUNT(*) FROM radar_stripped")
    count = mem_cur.fetchone()[0]
    print(f"  Inserted {count} rows into radar_stripped")
    return mem_conn


def search_group_a(conn: sqlite3.Connection, query: str, k: int) -> list:
    """Search stripped FTS5 table."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT slug FROM radar_stripped WHERE radar_stripped MATCH ? ORDER BY rank LIMIT ?",
            (query, k)
        )
        return [row[0] for row in cur.fetchall()]
    except Exception as e:
        return []


# ─────────────────────────────────────────────
# Group B: production radar_fts search
# ─────────────────────────────────────────────
def search_group_b(conn: sqlite3.Connection, query: str, k: int) -> list:
    """Search production radar_fts table."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT slug FROM radar_fts WHERE radar_fts MATCH ? ORDER BY bm25(radar_fts) LIMIT ?",
            (query, k)
        )
        return [row[0] for row in cur.fetchall()]
    except Exception as e:
        return []


# ─────────────────────────────────────────────
# Hit@K evaluation
# ─────────────────────────────────────────────
def evaluate_hit_at_k(questions, search_fn, k_values, label=""):
    """Evaluate Hit@K for all questions using a given search function."""
    hits = {k: 0 for k in k_values}
    total = len(questions)
    max_k = max(k_values)

    for i, q in enumerate(questions):
        if i > 0 and i % 30 == 0:
            print(f"  [{label}] Progress: {i}/{total}")

        query = q["query_terms"]
        expected = set(q["expected_slugs"])
        results = search_fn(query, max_k)

        for k in k_values:
            top_k = set(results[:k])
            if top_k & expected:
                hits[k] += 1

    return {k: hits[k] / total for k in k_values}


# ─────────────────────────────────────────────
# Token analysis (live DB)
# ─────────────────────────────────────────────
def analyze_tokens():
    """Analyze token compression ratio between skeleton and raw files."""
    print("\nAnalyzing token compression ratios (live DB)...")

    # Try tiktoken, fall back to approximation
    use_tiktoken = False
    encoder = None
    try:
        import tiktoken
        encoder = tiktoken.encoding_for_model("gpt-4")
        use_tiktoken = True
        print("  Using tiktoken (cl100k_base)")
    except ImportError:
        print("  tiktoken not available, using word-count approximation (len*1.3)")

    try:
        conn = sqlite3.connect(LIVE_DB)
        cur = conn.cursor()
        cur.execute(
            "SELECT slug, tokens, drawer_path FROM radar WHERE tokens IS NOT NULL AND drawer_path IS NOT NULL"
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        print(f"  Warning: could not query live DB: {e}")
        return None

    print(f"  Found {len(rows)} entries with tokens + drawer_path")

    ratios = []
    n_missing = 0

    for slug, skeleton_tokens, drawer_path in rows:
        if not drawer_path or not os.path.exists(drawer_path):
            n_missing += 1
            continue

        try:
            with open(drawer_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            n_missing += 1
            continue

        if not content.strip():
            n_missing += 1
            continue

        if use_tiktoken:
            raw_tokens = len(encoder.encode(content))
        else:
            raw_tokens = int(len(content.split()) * 1.3)

        if raw_tokens == 0:
            continue

        ratio = skeleton_tokens / raw_tokens
        ratios.append(ratio)

    if not ratios:
        print("  No valid ratios computed")
        return None

    ratios.sort()
    n = len(ratios)
    mean_ratio = sum(ratios) / n
    median_ratio = ratios[n // 2]

    dist = {
        "lt_20pct": sum(1 for r in ratios if r < 0.2),
        "20_30pct": sum(1 for r in ratios if 0.2 <= r < 0.3),
        "30_40pct": sum(1 for r in ratios if 0.3 <= r < 0.4),
        "gt_40pct": sum(1 for r in ratios if r >= 0.4),
    }

    print(f"  Analyzed: {n}, Missing/skipped: {n_missing}")
    print(f"  Mean ratio: {mean_ratio:.3f}, Median: {median_ratio:.3f}")
    print(f"  Distribution: {dist}")

    return {
        "n_entries_analyzed": n,
        "n_files_missing": n_missing,
        "mean_compression_ratio": round(mean_ratio, 4),
        "median_compression_ratio": round(median_ratio, 4),
        "distribution": dist,
        "ratios": ratios,  # kept for charting, removed from JSON output
        "token_method": "tiktoken cl100k_base" if use_tiktoken else "word*1.3 approximation",
    }


# ─────────────────────────────────────────────
# Charts
# ─────────────────────────────────────────────
def make_hitatk_chart(group_a_scores, group_b_scores, k_values, out_path):
    """Bar chart comparing Hit@K for Group A vs Group B."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  matplotlib not available, skipping chart")
        return False

    fig, ax = plt.subplots(figsize=(12, 8), dpi=100)

    x = np.arange(len(k_values))
    width = 0.35

    vals_a = [group_a_scores[k] * 100 for k in k_values]
    vals_b = [group_b_scores[k] * 100 for k in k_values]

    bars_a = ax.bar(x - width/2, vals_a, width, label="A: Stripped (no tags)", color="#4472C4", alpha=0.85)
    bars_b = ax.bar(x + width/2, vals_b, width, label="B: Production CLSC (92.9% baseline)", color="#ED7D31", alpha=0.85)

    # Value labels on bars
    for bar in bars_a:
        h = bar.get_height()
        ax.annotate(f"{h:.1f}%", xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=10)
    for bar in bars_b:
        h = bar.get_height()
        ax.annotate(f"{h:.1f}%", xy=(bar.get_x() + bar.get_width() / 2, h),
                    xytext=(0, 3), textcoords="offset points", ha="center", va="bottom", fontsize=10)

    ax.set_title("CLSC A/B: Hit@K Comparison (n=156)", fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel("K", fontsize=12)
    ax.set_ylabel("Hit@K (%)", fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels([f"K={k}" for k in k_values], fontsize=11)
    ax.set_ylim(0, 110)
    ax.yaxis.grid(True, linestyle="--", alpha=0.7)
    ax.set_axisbelow(True)
    ax.legend(fontsize=11)

    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    print(f"  Saved: {out_path}")
    return True


def make_token_chart(token_data, out_path):
    """Histogram of token compression ratios."""
    if not token_data or not token_data.get("ratios"):
        print("  No ratio data for token chart")
        return False

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  matplotlib not available, skipping chart")
        return False

    ratios = token_data["ratios"]
    mean_r = token_data["mean_compression_ratio"]
    median_r = token_data["median_compression_ratio"]

    fig, ax = plt.subplots(figsize=(12, 8), dpi=100)

    ax.hist(ratios, bins=40, color="#5B9BD5", alpha=0.8, edgecolor="white")

    # Vertical lines at 0.2, 0.3, 0.4
    for v, color, ls in [(0.2, "gray", "--"), (0.3, "gray", "--"), (0.4, "gray", "--")]:
        ax.axvline(v, color=color, linestyle=ls, linewidth=1, alpha=0.6, label=f"{int(v*100)}%")

    # Mean and median lines
    ax.axvline(mean_r, color="red", linestyle="-", linewidth=2, label=f"Mean={mean_r:.3f}")
    ax.axvline(median_r, color="green", linestyle="-", linewidth=2, label=f"Median={median_r:.3f}")

    ax.set_title(f"CLSC Token Compression Distribution (n={len(ratios)}, tiktoken cl100k_base)", fontsize=14, fontweight="bold", pad=15)
    ax.set_xlabel("Compression Ratio (skeleton tokens / raw tokens)", fontsize=12)
    ax.set_ylabel("Count", fontsize=12)
    ax.yaxis.grid(True, linestyle="--", alpha=0.5)
    ax.set_axisbelow(True)
    ax.legend(fontsize=11)

    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()
    print(f"  Saved: {out_path}")
    return True


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    print("=" * 60)
    print("CLSC A/B Benchmark")
    print(f"Run at: {datetime.now().isoformat()}")
    print("=" * 60)

    # Load questions
    print("\nLoading questions...")
    with open(QUESTIONS_FILE) as f:
        data = json.load(f)
    questions_all = data.get("questions", [])
    questions = [
        q for q in questions_all
        if q.get("search_api") == "seabed_search" and q.get("expected_slugs")
    ]
    print(f"  Loaded {len(questions)} questions (seabed_search + expected_slugs)")

    # Open snapshot DB
    snap_conn = sqlite3.connect(f"file:{SNAPSHOT_DB}?mode=ro", uri=True)

    # Build Group A
    group_a_conn = build_group_a_db(snap_conn)

    def search_a(query, k):
        return search_group_a(group_a_conn, query, k)

    def search_b(query, k):
        return search_group_b(snap_conn, query, k)

    # Evaluate Group A
    print(f"\nEvaluating Group A (stripped skeleton)...")
    group_a_scores = evaluate_hit_at_k(questions, search_a, K_VALUES, "Group A")
    print("  Group A results:")
    for k in K_VALUES:
        print(f"    Hit@{k}: {group_a_scores[k]*100:.1f}%")

    # Evaluate Group B
    print(f"\nEvaluating Group B (production CLSC)...")
    group_b_scores = evaluate_hit_at_k(questions, search_b, K_VALUES, "Group B")
    print("  Group B results:")
    for k in K_VALUES:
        print(f"    Hit@{k}: {group_b_scores[k]*100:.1f}%")

    snap_conn.close()
    group_a_conn.close()

    hit5_gap = group_b_scores[5] - group_a_scores[5]
    print(f"\nHit@5 gap (B - A): {hit5_gap*100:+.1f}%")

    # Token analysis
    token_data = analyze_tokens()

    # Charts
    print("\nGenerating charts...")
    hitatk_chart_path = os.path.join(OUT_DIR, "clsc-ab-chart-hitatk.png")
    token_chart_path = os.path.join(OUT_DIR, "clsc-ab-chart-tokens.png")

    make_hitatk_chart(group_a_scores, group_b_scores, K_VALUES, hitatk_chart_path)
    if token_data:
        make_token_chart(token_data, token_chart_path)

    # Build output JSON
    result = {
        "run_at": datetime.now().isoformat(),
        "snapshot_date": "2026-04-16",
        "dataset": "work-scenario-200",
        "n_questions": len(questions),
        "group_a": {
            "name": "stripped skeleton（去結構 tag）",
            "hit_at_1": round(group_a_scores[1], 4),
            "hit_at_3": round(group_a_scores[3], 4),
            "hit_at_5": round(group_a_scores[5], 4),
            "hit_at_10": round(group_a_scores[10], 4),
        },
        "group_b": {
            "name": "CLSC skeleton（生產，含 tag）",
            "hit_at_1": round(group_b_scores[1], 4),
            "hit_at_3": round(group_b_scores[3], 4),
            "hit_at_5": round(group_b_scores[5], 4),
            "hit_at_10": round(group_b_scores[10], 4),
        },
        "hit5_gap_b_minus_a": round(hit5_gap, 4),
        "token_analysis": {
            "snapshot_date": "2026-04-16",
            "n_entries_analyzed": token_data["n_entries_analyzed"] if token_data else 0,
            "n_files_missing": token_data["n_files_missing"] if token_data else 0,
            "mean_compression_ratio": token_data["mean_compression_ratio"] if token_data else None,
            "median_compression_ratio": token_data["median_compression_ratio"] if token_data else None,
            "distribution": token_data["distribution"] if token_data else None,
            "token_method": token_data["token_method"] if token_data else None,
        },
        "charts": [
            "benchmarks/clsc-ab-chart-hitatk.png",
            "benchmarks/clsc-ab-chart-tokens.png",
        ],
    }

    out_json = os.path.join(OUT_DIR, "results-clsc-ab-20260416.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\nResults written: {out_json}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'K':<6} {'Group A':>12} {'Group B':>12} {'Gap (B-A)':>12}")
    print("-" * 44)
    for k in K_VALUES:
        a = group_a_scores[k] * 100
        b = group_b_scores[k] * 100
        gap = b - a
        print(f"{'Hit@'+str(k):<6} {a:>11.1f}% {b:>11.1f}% {gap:>+11.1f}%")

    if token_data:
        print(f"\nToken compression:")
        print(f"  Mean:   {token_data['mean_compression_ratio']:.3f}")
        print(f"  Median: {token_data['median_compression_ratio']:.3f}")
        print(f"  Distribution: {token_data['distribution']}")


if __name__ == "__main__":
    main()
