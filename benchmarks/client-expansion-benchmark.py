#!/usr/bin/env python3
"""MEMO-012: Client-Side Query Expansion Benchmark for MemOcean.

Compares BM25 baseline (Group A) vs Haiku client-side query expansion → BM25 (Group B)
on DRCD and CMRC datasets.

Usage:
  python3 benchmarks/client-expansion-benchmark.py
"""
import json
import os
import random
import re
import sqlite3
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("client-expansion")

# ── config ────────────────────────────────────────────────────────────────────
RANDOM_SEED = 42
SAMPLE_SIZE = 200
K_VALUES = [1, 3, 5, 10]
HAIKU_MODEL = "claude-haiku-4-5-20251001"
CHUNK_MAX = 400

DRCD_URL = "https://raw.githubusercontent.com/DRCKnowledgeTeam/DRCD/master/DRCD_test.json"
CMRC_URLS = [
    "https://raw.githubusercontent.com/ymcui/cmrc2018/master/squad-style-data/cmrc2018_test_public.json",
    "https://raw.githubusercontent.com/ymcui/cmrc2018/master/data/cmrc2018_test_public.json",
    "https://raw.githubusercontent.com/ymcui/cmrc2018/master/squad-style-data/cmrc2018_dev.json",
]

DRCD_LOCAL = Path("/tmp/DRCD_test.json")
CMRC_LOCAL = Path("/tmp/cmrc2018_test.json")
CMRC_DEV_LOCAL = Path("/tmp/cmrc2018_dev.json")
DRCD_SNAPSHOT = Path("/home/oldrabbit/.claude-bots/benchmarks/drcd-snapshot.db")
CMRC_SNAPSHOT = Path("/home/oldrabbit/.claude-bots/benchmarks/cmrc-snapshot.db")
RESULTS_DIR = Path("/home/oldrabbit/.claude-bots/benchmarks")

# ── stopwords (union of DRCD + CMRC sets) ────────────────────────────────────
STOPWORDS_TW = {
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都',
    '一', '一個', '上', '也', '很', '到', '說', '要', '去', '你', '會',
    '著', '沒有', '看', '好', '自己', '這',
}
STOPWORDS_CN = {
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都',
    '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会',
    '着', '没有', '看', '好', '自己', '这', '那', '但', '与', '及', '或', '而',
}

QUESTION_TRIGRAMS_SKIP_TW = {
    '什麼樣', '麼樣的', '是什麼', '哪一種', '哪一個', '哪一門', '哪一本',
    '哪一位', '哪些地', '哪些人', '以什麼', '用什麼', '由什麼', '為什麼',
    '什麼時', '什麼人', '什麼地', '什麼原', '什麼因',
}
QUESTION_TRIGRAMS_SKIP_CN = {
    '什么样', '样的人', '是什么', '哪一种', '哪一个', '哪一门', '哪一本',
    '哪一位', '哪些地', '哪些人', '以什么', '用什么', '由什么', '为什么',
    '什么时', '什么人', '什么地', '什么原', '什么因',
}

QUESTION_ANYWHERE_CHARS_TW = frozenset('什哪誰')
QUESTION_ANYWHERE_CHARS_CN = frozenset('什哪谁')

SKIP_GENERIC = frozenset({
    '一種已經', '不再使用', '大量使用', '一般認為', '主要原因',
    '以下哪', '以下什', '根據文', '下列哪', '是以什', '下列何',
    '下面哪', '以下列', '下列那',
    '一种已经', '不再使用', '大量使用', '一般认为', '主要原因',
    '根据文', '下列那',
})


# ── Step 1: download datasets ─────────────────────────────────────────────────

def download_drcd():
    if DRCD_LOCAL.exists():
        logger.info("Cache hit: %s", DRCD_LOCAL)
        return
    logger.info("Fetching DRCD from %s ...", DRCD_URL)
    urllib.request.urlretrieve(DRCD_URL, DRCD_LOCAL)
    size_kb = DRCD_LOCAL.stat().st_size // 1024
    logger.info("Saved %d KB → %s", size_kb, DRCD_LOCAL)


def download_cmrc():
    if CMRC_LOCAL.exists():
        logger.info("Cache hit: %s", CMRC_LOCAL)
        return
    for url in CMRC_URLS:
        try:
            logger.info("Trying %s ...", url)
            urllib.request.urlretrieve(url, CMRC_LOCAL)
            size_kb = CMRC_LOCAL.stat().st_size // 1024
            logger.info("Saved %d KB → %s", size_kb, CMRC_LOCAL)
            return
        except Exception as e:
            logger.warning("Failed: %s", e)
            if CMRC_LOCAL.exists():
                CMRC_LOCAL.unlink()
    raise RuntimeError("All CMRC download URLs failed")


# ── Step 2: split context ─────────────────────────────────────────────────────

def split_context(context: str, max_len: int = CHUNK_MAX) -> list[tuple[int, int, str]]:
    """Split context into chunks ≤ max_len chars at CJK sentence boundaries."""
    parts = re.split(r'(?<=[。！？\n])', context)
    chunks = []
    cur = ""
    cur_start = 0

    for part in parts:
        if not part:
            continue
        if len(cur) + len(part) <= max_len:
            cur += part
        else:
            if cur:
                end = cur_start + len(cur)
                chunks.append((cur_start, end, cur))
                cur_start = end
            while len(part) > max_len:
                chunks.append((cur_start, cur_start + max_len, part[:max_len]))
                cur_start += max_len
                part = part[max_len:]
            cur = part
    if cur:
        chunks.append((cur_start, cur_start + len(cur), cur))
    return chunks


# ── Step 3: parse datasets ────────────────────────────────────────────────────

def parse_squad_style(path: Path, slug_prefix: str) -> tuple[list, list, int]:
    """Parse SQuAD-style JSON (works for both DRCD and CMRC).

    Returns:
        rows     – list of (slug, title, content)
        qa_pairs – list of (question, [expected_slug, ...])
        skipped  – count of QA pairs with no mappable answer
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    qa_pairs = []
    skipped = 0

    for article in data["data"]:
        title = article.get("title", "")
        for para in article.get("paragraphs", []):
            para_id = para.get("id", "")
            context = para.get("context", "")
            chunks = split_context(context)

            para_rows = []
            for idx, (start, end, text) in enumerate(chunks):
                slug = f"{slug_prefix}_{para_id}_{idx}"
                para_rows.append((slug, title, text, start, end))
                rows.append((slug, title, text))

            for qa in para.get("qas", []):
                question = qa.get("question", "")
                answers = qa.get("answers", [])
                if not answers:
                    skipped += 1
                    continue

                expected_slugs = []
                for ans in answers:
                    ans_start = ans.get("answer_start", -1)
                    if ans_start < 0:
                        continue
                    for slug, _, _, c_start, c_end in para_rows:
                        if c_start <= ans_start < c_end:
                            if slug not in expected_slugs:
                                expected_slugs.append(slug)
                            break

                if not expected_slugs:
                    skipped += 1
                    continue

                qa_pairs.append((question, expected_slugs))

    logger.info("[%s] %d chunks, %d QA pairs, %d skipped", slug_prefix, len(rows), len(qa_pairs), skipped)
    return rows, qa_pairs, skipped


# ── Build FTS5 index (fallback if snapshot missing) ──────────────────────────

def build_fts_index(rows: list[tuple[str, str, str]], db_path: Path) -> sqlite3.Connection:
    """Build a fresh FTS5 trigram index from rows and return open connection."""
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE VIRTUAL TABLE radar USING fts5(slug, content, tokenize='trigram')"
    )
    conn.executemany(
        "INSERT INTO radar(slug, content) VALUES (?, ?)",
        [(slug, content) for slug, _title, content in rows]
    )
    conn.commit()
    logger.info("Built FTS5 index: %d rows → %s", len(rows), db_path)
    return conn


# ── Step 4: FTS5 search ───────────────────────────────────────────────────────

def fts_search(conn: sqlite3.Connection, query_terms: str, limit: int = 10) -> list[str]:
    """Run FTS5 MATCH query against the snapshot DB. Returns list of slugs."""
    if not query_terms or not query_terms.strip():
        return []
    try:
        cur = conn.execute(
            "SELECT slug FROM radar WHERE radar MATCH ? ORDER BY rank LIMIT ?",
            (query_terms, limit)
        )
        return [row[0] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


# ── Step 5: query construction ────────────────────────────────────────────────

def build_query_terms(text: str, is_simplified: bool = False) -> str:
    """Build a trigram-compatible FTS5 OR-query from a Chinese text string.

    Works for both Traditional (DRCD) and Simplified (CMRC) Chinese.
    Uses the same logic as drcd-benchmark.py and cmrc-benchmark.py.
    """
    import jieba

    stopwords = STOPWORDS_CN if is_simplified else STOPWORDS_TW
    q_skip = QUESTION_TRIGRAMS_SKIP_CN if is_simplified else QUESTION_TRIGRAMS_SKIP_TW
    q_chars = QUESTION_ANYWHERE_CHARS_CN if is_simplified else QUESTION_ANYWHERE_CHARS_TW

    anchor_tokens = [
        t for t in jieba.cut(text)
        if len(t) >= 3
        and t not in stopwords
        and re.fullmatch(r'[\u4e00-\u9fff]+', t)
    ]

    cjk_runs = re.findall(r'[\u4e00-\u9fff]+', text)
    all_windows: list[str] = []
    for run in cjk_runs:
        for i in range(len(run) - 2):
            w = run[i:i + 3]
            if w not in q_skip:
                all_windows.append(w)

    good_windows = [
        w for w in all_windows
        if not any(c in q_chars for c in w)
    ]

    candidates: list[str] = []
    for t in anchor_tokens:
        if t not in candidates:
            candidates.append(t)
    for w in good_windows:
        if w not in candidates:
            candidates.append(w)

    result = [c for c in candidates if c not in SKIP_GENERIC]

    if not result:
        return text[:3] if len(text) >= 3 else text

    return " OR ".join(result[:12])


# ── Step 6: Haiku expansion ───────────────────────────────────────────────────

_haiku_client = None

def _get_haiku_client():
    global _haiku_client
    if _haiku_client is None:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        _haiku_client = anthropic.Anthropic(api_key=api_key)
    return _haiku_client


def expand_with_haiku(question: str) -> str | None:
    """Expand question to keywords using Haiku.

    Returns expanded string (original + keywords) on success,
    None on failure (caller should use original question).
    """
    client = _get_haiku_client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=100,
            temperature=0,
            system="你是搜尋關鍵字生成器。將以下問題轉換為 3-5 個最佳搜尋關鍵詞，以空格分隔，只輸出關鍵詞，不加解釋。",
            messages=[{"role": "user", "content": question}]
        )
        keywords = resp.content[0].text.strip()
        # Merge expanded keywords with original question terms
        return question + " " + keywords
    except Exception as e:
        logger.warning("Haiku expansion failed for %r: %s — using original", question[:50], e)
        return None


# ── Step 7: evaluate both groups ─────────────────────────────────────────────

def evaluate_ab(
    conn: sqlite3.Connection,
    qa_pairs: list[tuple[str, list[str]]],
    is_simplified: bool,
    dataset_label: str,
) -> dict:
    """Run Group A (BM25 baseline) and Group B (Haiku + BM25) evaluation.

    Returns dict with hits_a, hits_b, haiku_fallback_count.
    """
    hits_a = {k: 0 for k in K_VALUES}
    hits_b = {k: 0 for k in K_VALUES}
    haiku_fallback_count = 0
    total = len(qa_pairs)

    logger.info("[%s] Evaluating %d QA pairs (A/B) ...", dataset_label, total)

    for i, (question, expected_slugs) in enumerate(qa_pairs):
        # Group A: BM25 baseline
        query_a = build_query_terms(question, is_simplified=is_simplified)
        got_a = fts_search(conn, query_a, limit=max(K_VALUES))

        # Group B: Haiku expansion → BM25
        expanded = expand_with_haiku(question)
        if expanded is None:
            haiku_fallback_count += 1
            # Fall back: Group B == Group A
            query_b = query_a
        else:
            query_b = build_query_terms(expanded, is_simplified=is_simplified)
        got_b = fts_search(conn, query_b, limit=max(K_VALUES))

        for k in K_VALUES:
            if any(s in got_a[:k] for s in expected_slugs):
                hits_a[k] += 1
            if any(s in got_b[:k] for s in expected_slugs):
                hits_b[k] += 1

        if (i + 1) % 50 == 0:
            pct5_a = hits_a[5] / (i + 1) * 100
            pct5_b = hits_b[5] / (i + 1) * 100
            logger.info(
                "  %d/%d  A Hit@5=%.1f%%  B Hit@5=%.1f%%  fallbacks=%d",
                i + 1, total, pct5_a, pct5_b, haiku_fallback_count
            )

    return {
        "hits_a": hits_a,
        "hits_b": hits_b,
        "haiku_fallback_count": haiku_fallback_count,
    }


# ── Step 8: build result JSON ─────────────────────────────────────────────────

def build_result(
    dataset_label: str,
    sample_size: int,
    hits_a: dict,
    hits_b: dict,
    haiku_fallback_count: int,
) -> dict:
    n = sample_size
    hit5_a = round(hits_a[5] / n, 4)
    hit5_b = round(hits_b[5] / n, 4)
    gap = round(hit5_b - hit5_a, 4)

    if abs(gap) < 0.005:
        verdict = "neutral"
    elif gap > 0:
        verdict = "expansion helped"
    else:
        verdict = "expansion hurt"

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_label,
        "sample_size": n,
        "seed": RANDOM_SEED,
        "haiku_model": HAIKU_MODEL,
        "haiku_fallback_count": haiku_fallback_count,
        "group_a": {
            "name": "BM25 baseline",
            "hit_at_1": round(hits_a[1] / n, 4),
            "hit_at_3": round(hits_a[3] / n, 4),
            "hit_at_5": hit5_a,
            "hit_at_10": round(hits_a[10] / n, 4),
        },
        "group_b": {
            "name": "Haiku expansion → BM25",
            "hit_at_1": round(hits_b[1] / n, 4),
            "hit_at_3": round(hits_b[3] / n, 4),
            "hit_at_5": hit5_b,
            "hit_at_10": round(hits_b[10] / n, 4),
        },
        "gap_b_minus_a_hit5": gap,
        "summary": f"B vs A gap @ Hit@5: {gap:+.1%} ({verdict})",
    }


# ── Step 9: chart ─────────────────────────────────────────────────────────────

def draw_chart(result_drcd: dict, result_cmrc: dict, out_path: Path) -> None:
    """Draw grouped bar chart comparing A vs B at K=1,3,5,10 for both datasets."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        logger.warning("matplotlib not available — skipping chart")
        return

    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle("Client-Side Query Expansion: A/B Hit@K Comparison", fontsize=14, fontweight="bold")

    k_labels = [f"K={k}" for k in K_VALUES]
    x = np.arange(len(K_VALUES))
    width = 0.35

    for ax, result, title in [
        (axes[0], result_drcd, "DRCD (Traditional Chinese)"),
        (axes[1], result_cmrc, "CMRC 2018 (Simplified Chinese)"),
    ]:
        vals_a = [result["group_a"][f"hit_at_{k}"] for k in K_VALUES]
        vals_b = [result["group_b"][f"hit_at_{k}"] for k in K_VALUES]

        bars_a = ax.bar(x - width / 2, vals_a, width, label="Group A: BM25 baseline", color="#4C72B0")
        bars_b = ax.bar(x + width / 2, vals_b, width, label="Group B: Haiku + BM25", color="#DD8452")

        ax.set_title(title, fontsize=11)
        ax.set_xlabel("K", fontsize=10)
        ax.set_ylabel("Hit Rate", fontsize=10)
        ax.set_xticks(x)
        ax.set_xticklabels(k_labels)
        ax.set_ylim(0, 1.05)
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        ax.legend(fontsize=9)

        # Annotate bars
        for bar in list(bars_a) + list(bars_b):
            h = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                h + 0.01,
                f"{h:.1%}",
                ha="center", va="bottom", fontsize=7.5
            )

        # Add gap annotation
        gap = result["gap_b_minus_a_hit5"]
        gap_str = f"Hit@5 gap: {gap:+.1%}"
        ax.text(0.98, 0.04, gap_str, transform=ax.transAxes,
                ha="right", va="bottom", fontsize=9,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow", edgecolor="gray"))

    plt.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Chart saved → %s", out_path)


# ── main ───────────────────────────────────────────────────────────────────────

def run_dataset(
    dataset_json: Path,
    snapshot_db: Path,
    slug_prefix: str,
    is_simplified: bool,
    dataset_label: str,
    results_filename: str,
) -> dict:
    """Full pipeline for one dataset. Returns result dict."""
    logger.info("=== %s ===", dataset_label)

    # Parse + sample
    rows, qa_pairs, skipped = parse_squad_style(dataset_json, slug_prefix)
    random.seed(RANDOM_SEED)
    sample = random.sample(qa_pairs, min(SAMPLE_SIZE, len(qa_pairs)))
    logger.info("Sampled %d / %d QA pairs (seed=%d)", len(sample), len(qa_pairs), RANDOM_SEED)

    # Connect to snapshot; rebuild from rows if missing (spec: auto-rebuild)
    if not snapshot_db.exists():
        logger.warning("Snapshot DB not found: %s — rebuilding from parsed rows", snapshot_db)
        conn = build_fts_index(rows, snapshot_db)
    else:
        conn = sqlite3.connect(str(snapshot_db))

    # Evaluate A/B
    eval_result = evaluate_ab(conn, sample, is_simplified=is_simplified, dataset_label=dataset_label)
    conn.close()

    # Build result dict
    result = build_result(
        dataset_label=dataset_label,
        sample_size=len(sample),
        hits_a=eval_result["hits_a"],
        hits_b=eval_result["hits_b"],
        haiku_fallback_count=eval_result["haiku_fallback_count"],
    )

    # Print summary
    logger.info("Group A: Hit@1=%.1f%% Hit@3=%.1f%% Hit@5=%.1f%% Hit@10=%.1f%%",
                result["group_a"]["hit_at_1"] * 100,
                result["group_a"]["hit_at_3"] * 100,
                result["group_a"]["hit_at_5"] * 100,
                result["group_a"]["hit_at_10"] * 100)
    logger.info("Group B: Hit@1=%.1f%% Hit@3=%.1f%% Hit@5=%.1f%% Hit@10=%.1f%%",
                result["group_b"]["hit_at_1"] * 100,
                result["group_b"]["hit_at_3"] * 100,
                result["group_b"]["hit_at_5"] * 100,
                result["group_b"]["hit_at_10"] * 100)
    logger.info("%s", result["summary"])
    logger.info("Haiku fallbacks: %d / %d", eval_result["haiku_fallback_count"], len(sample))

    # Write JSON
    out_path = RESULTS_DIR / results_filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info("Results written → %s", out_path)

    return result


def main():
    logger.info("=== MEMO-012: Client-Side Query Expansion Benchmark ===")
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    # Download datasets if needed
    download_drcd()
    download_cmrc()

    # Run DRCD
    result_drcd = run_dataset(
        dataset_json=DRCD_LOCAL,
        snapshot_db=DRCD_SNAPSHOT,
        slug_prefix="drcd",
        is_simplified=False,
        dataset_label="DRCD v2 test split",
        results_filename=f"results-client-expansion-drcd-{today}.json",
    )

    # Run CMRC
    result_cmrc = run_dataset(
        dataset_json=CMRC_LOCAL,
        snapshot_db=CMRC_SNAPSHOT,
        slug_prefix="cmrc",
        is_simplified=True,
        dataset_label="CMRC 2018 test split",
        results_filename=f"results-client-expansion-cmrc-{today}.json",
    )

    # Draw chart
    chart_path = RESULTS_DIR / "client-expansion-chart.png"
    draw_chart(result_drcd, result_cmrc, chart_path)

    # Final summary
    print("\n" + "=" * 60)
    print("MEMO-012 FINAL SUMMARY")
    print("=" * 60)
    for label, r in [("DRCD", result_drcd), ("CMRC", result_cmrc)]:
        print(f"\n{label}:")
        print(f"  Group A (BM25): Hit@1={r['group_a']['hit_at_1']:.1%}  Hit@3={r['group_a']['hit_at_3']:.1%}  Hit@5={r['group_a']['hit_at_5']:.1%}  Hit@10={r['group_a']['hit_at_10']:.1%}")
        print(f"  Group B (Haiku+BM25): Hit@1={r['group_b']['hit_at_1']:.1%}  Hit@3={r['group_b']['hit_at_3']:.1%}  Hit@5={r['group_b']['hit_at_5']:.1%}  Hit@10={r['group_b']['hit_at_10']:.1%}")
        print(f"  {r['summary']}")
        print(f"  Haiku fallbacks: {r['haiku_fallback_count']} / {r['sample_size']}")


if __name__ == "__main__":
    main()
