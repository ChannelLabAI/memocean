#!/usr/bin/env python3
"""CMRC 2018 External Benchmark Adapter for MemOcean BM25 evaluation.

Downloads CMRC 2018 test/dev split, builds an isolated FTS5 trigram index,
runs Hit@K evaluation, and writes results to benchmarks/.

Usage:
  python3 scripts/cmrc-benchmark.py
"""
import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────
CMRC_URLS = [
    "https://raw.githubusercontent.com/ymcui/cmrc2018/master/squad-style-data/cmrc2018_test_public.json",
    "https://raw.githubusercontent.com/ymcui/cmrc2018/master/data/cmrc2018_test_public.json",
    "https://raw.githubusercontent.com/ymcui/cmrc2018/master/squad-style-data/cmrc2018_dev.json",
]
CMRC_LOCAL = Path("/tmp/cmrc2018_test.json")
SNAPSHOT_DB = Path("/home/oldrabbit/.claude-bots/benchmarks/cmrc-snapshot.db")
RESULTS_PATH = Path("/home/oldrabbit/.claude-bots/benchmarks/results-cmrc-20260416.json")

K_VALUES = [1, 3, 5, 10]
CHUNK_MAX = 400
INTERNAL_BASELINE_HIT5 = 0.929

STOPWORDS = {
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都',
    '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会',
    '着', '没有', '看', '好', '自己', '这', '那', '但', '与', '及', '或', '而',
}

# Question-structure trigrams that appear in questions but NOT in factual passages
QUESTION_TRIGRAMS_SKIP = {
    '什么样', '样的人', '是什么', '哪一种', '哪一个', '哪一门', '哪一本',
    '哪一位', '哪些地', '哪些人', '以什么', '用什么', '由什么', '为什么',
    '什么时', '什么人', '什么地', '什么原', '什么因',
}

# Question-word chars: windows containing these are likely question-structure artifacts
QUESTION_ANYWHERE_CHARS = frozenset('什哪谁')

# Generic windows that over-match across many unrelated passages
SKIP_GENERIC = frozenset({
    '一种已经', '不再使用', '大量使用', '一般认为', '主要原因',
    '以下哪', '以下什', '根据文', '下列哪', '是以什', '下列何',
    '下面哪', '以下列', '下列那',
})

# ── Step 1: download ──────────────────────────────────────────────────────────

def download_cmrc():
    if CMRC_LOCAL.exists():
        print(f"[download] Cache hit: {CMRC_LOCAL}", flush=True)
        return
    for url in CMRC_URLS:
        try:
            print(f"[download] Trying {url} ...", flush=True)
            urllib.request.urlretrieve(url, CMRC_LOCAL)
            size_kb = CMRC_LOCAL.stat().st_size // 1024
            print(f"[download] Saved {size_kb} KB → {CMRC_LOCAL}", flush=True)
            return
        except Exception as e:
            print(f"[download] Failed: {e}", flush=True)
            if CMRC_LOCAL.exists():
                CMRC_LOCAL.unlink()
    raise RuntimeError("All CMRC download URLs failed")


# ── Step 2: parse + chunk ─────────────────────────────────────────────────────

def split_context(context: str, max_len: int = CHUNK_MAX) -> list[tuple[int, int, str]]:
    """Split context into chunks ≤ max_len chars at CJK sentence boundaries.

    Returns list of (start_char, end_char, chunk_text).
    """
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


def parse_cmrc(path: Path):
    """Parse CMRC 2018 SQuAD-style JSON.

    Returns:
        rows     – list of (slug, title, content) for FTS indexing
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

            # Build slug list for this paragraph
            para_rows = []
            for idx, (start, end, text) in enumerate(chunks):
                slug = f"cmrc_{para_id}_{idx}"
                para_rows.append((slug, title, text, start, end))
                rows.append((slug, title, text))

            # Map each QA to the chunk containing answer_start
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

    print(f"[parse] {len(rows)} chunks, {len(qa_pairs)} QA pairs, {skipped} skipped", flush=True)
    return rows, qa_pairs, skipped


# ── Step 3: build FTS5 index ──────────────────────────────────────────────────

def build_index(rows: list[tuple[str, str, str]]) -> sqlite3.Connection:
    """Create fresh FTS5 trigram db and insert rows. Returns open connection."""
    if SNAPSHOT_DB.exists():
        SNAPSHOT_DB.unlink()
    SNAPSHOT_DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(SNAPSHOT_DB))
    conn.execute(
        "CREATE VIRTUAL TABLE radar USING fts5(id, title, content, slug, tokenize='trigram')"
    )
    conn.executemany(
        "INSERT INTO radar(id, title, content, slug) VALUES (?, ?, ?, ?)",
        [(slug, title, content, slug) for slug, title, content in rows]
    )
    conn.commit()
    print(f"[index] Indexed {len(rows)} chunks (trigram) into {SNAPSHOT_DB}", flush=True)
    return conn


# ── Step 4 + 5: query construction + search ───────────────────────────────────

def build_query_terms(question: str) -> str:
    """Build a trigram-compatible FTS5 OR-query from a simplified Chinese question."""
    import jieba

    # High-quality anchors: pure CJK, >=3 chars, not stopwords
    anchor_tokens = [
        t for t in jieba.cut(question)
        if len(t) >= 3
        and t not in STOPWORDS
        and re.fullmatch(r'[\u4e00-\u9fff]+', t)
    ]

    # 3-char sliding windows from all CJK runs
    cjk_runs = re.findall(r'[\u4e00-\u9fff]+', question)
    all_windows: list[str] = []
    for run in cjk_runs:
        for i in range(len(run) - 2):
            w = run[i:i + 3]
            if w not in QUESTION_TRIGRAMS_SKIP:
                all_windows.append(w)

    # Drop windows that contain question-word characters
    good_windows = [
        w for w in all_windows
        if not any(c in QUESTION_ANYWHERE_CHARS for c in w)
    ]

    # Merge: anchors first (better precision), then windows (better recall)
    candidates: list[str] = []
    for t in anchor_tokens:
        if t not in candidates:
            candidates.append(t)
    for w in good_windows:
        if w not in candidates:
            candidates.append(w)

    result = [c for c in candidates if c not in SKIP_GENERIC]

    if not result:
        return question[:3] if len(question) >= 3 else question

    return " OR ".join(result[:12])


def fts_search(conn: sqlite3.Connection, query_terms: str, limit: int = 10) -> list[str]:
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


# ── Step 6: evaluate ─────────────────────────────────────────────────────────

def evaluate(conn: sqlite3.Connection, qa_pairs: list[tuple[str, list[str]]]):
    hits = {k: 0 for k in K_VALUES}
    sample_failures = []
    total = len(qa_pairs)

    for i, (question, expected_slugs) in enumerate(qa_pairs):
        query_terms = build_query_terms(question)
        got_slugs = fts_search(conn, query_terms, limit=10)

        for k in K_VALUES:
            if any(s in got_slugs[:k] for s in expected_slugs):
                hits[k] += 1

        # Collect up to 5 failure samples (miss at Hit@10)
        if len(sample_failures) < 5 and not any(s in got_slugs[:10] for s in expected_slugs):
            sample_failures.append({
                "question": question,
                "query_terms": query_terms,
                "expected_slugs": expected_slugs,
                "got_slugs": got_slugs[:5],
            })

        if (i + 1) % 200 == 0:
            pct5 = hits[5] / (i + 1) * 100
            print(f"  {i+1}/{total}  Hit@5={pct5:.1f}%", flush=True)

    return hits, sample_failures


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("=== CMRC 2018 Benchmark ===", flush=True)

    # 1. Download
    download_cmrc()

    # 2. Parse
    rows, qa_pairs, skipped = parse_cmrc(CMRC_LOCAL)

    # 3. Build index
    conn = build_index(rows)

    # 4+5+6. Evaluate
    print(f"\n[eval] Running Hit@K over {len(qa_pairs)} QA pairs ...", flush=True)
    hits, sample_failures = evaluate(conn, qa_pairs)
    conn.close()

    evaluated = len(qa_pairs)
    total_reported = evaluated + skipped

    print("\n=== Results ===")
    for k in K_VALUES:
        print(f"  Hit@{k}: {hits[k]}/{evaluated} = {hits[k]/evaluated*100:.2f}%")

    hit5 = hits[5] / evaluated
    gap = hit5 - INTERNAL_BASELINE_HIT5

    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "CMRC 2018 test split",
        "total": total_reported,
        "evaluated": evaluated,
        "skipped": skipped,
        "hit_at_1": round(hits[1] / evaluated, 4),
        "hit_at_3": round(hits[3] / evaluated, 4),
        "hit_at_5": round(hits[5] / evaluated, 4),
        "hit_at_10": round(hits[10] / evaluated, 4),
        "internal_baseline_hit_at_5": INTERNAL_BASELINE_HIT5,
        "gap_hit_at_5": round(hit5 - INTERNAL_BASELINE_HIT5, 4),
        "sample_failures": sample_failures,
    }

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n[output] Written → {RESULTS_PATH}")
    print(f"  gap vs internal baseline (Hit@5): {gap:+.3f}")
    if hit5 >= 0.9:
        print("  Target ≥90%: YES")
    else:
        print(f"  Target ≥90%: NO (need {(0.9 - hit5)*evaluated:.0f} more hits)")


if __name__ == "__main__":
    main()
