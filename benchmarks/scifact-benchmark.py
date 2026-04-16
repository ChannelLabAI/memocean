#!/usr/bin/env python3
"""BEIR SciFact Benchmark Adapter for MemOcean BM25 evaluation.

Downloads BEIR SciFact test split, builds an isolated FTS5 trigram index,
runs Hit@K evaluation, and writes results to benchmarks/.

P3: English sanity check — verifies BM25 generalises beyond Chinese corpora.

Usage:
  python3 scripts/scifact-benchmark.py
"""
import io
import json
import os
import re
import sqlite3
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# ── paths ─────────────────────────────────────────────────────────────────────
SCIFACT_LOCAL_DIR = Path("/tmp/scifact")
SNAPSHOT_DB = Path("/home/oldrabbit/.claude-bots/benchmarks/scifact-snapshot.db")
RESULTS_PATH = Path("/home/oldrabbit/.claude-bots/benchmarks/results-scifact-20260416.json")

K_VALUES = [1, 3, 5, 10]
CHUNK_MAX = 400
INTERNAL_BASELINE_HIT5 = 0.929

# English stopwords
STOPWORDS = {
    'the', 'a', 'an', 'in', 'of', 'to', 'and', 'or', 'is', 'are', 'was',
    'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
    'will', 'would', 'could', 'should', 'may', 'might', 'that', 'this',
    'these', 'those', 'with', 'for', 'on', 'at', 'by', 'from', 'not', 'no',
    'it', 'its', 'as', 'if', 'but',
}

# ── Step 1: download ──────────────────────────────────────────────────────────

def _try_ukp_zip() -> bool:
    """Try downloading from UKP Darmstadt hosting. Returns True on success."""
    url = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
    zip_path = Path("/tmp/scifact.zip")
    try:
        print(f"[download] Trying UKP zip: {url}", flush=True)
        urllib.request.urlretrieve(url, zip_path)
        size_mb = zip_path.stat().st_size // (1024 * 1024)
        print(f"[download] Downloaded {size_mb} MB → {zip_path}", flush=True)
    except Exception as e:
        print(f"[download] UKP failed: {e}", flush=True)
        return False

    try:
        SCIFACT_LOCAL_DIR.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall("/tmp/")
        # The zip may extract to /tmp/scifact/ directly
        if SCIFACT_LOCAL_DIR.exists():
            print(f"[download] Extracted to {SCIFACT_LOCAL_DIR}", flush=True)
            return True
        # Some BEIR zips have a nested folder; try to find it
        candidates = list(Path("/tmp").glob("scifact*"))
        for c in candidates:
            if c.is_dir():
                c.rename(SCIFACT_LOCAL_DIR)
                print(f"[download] Moved {c} → {SCIFACT_LOCAL_DIR}", flush=True)
                return True
    except Exception as e:
        print(f"[download] Extraction failed: {e}", flush=True)
    return False


def _try_hf_individual() -> bool:
    """Try downloading individual files from HuggingFace Hub."""
    base = "https://huggingface.co/datasets/BeIR/scifact/resolve/main/data"
    files = {
        "corpus.jsonl": f"{base}/corpus.jsonl.gz",
        "queries.jsonl": f"{base}/queries.jsonl.gz",
        "qrels/test.tsv": f"{base}/qrels/test.tsv",
    }
    SCIFACT_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    (SCIFACT_LOCAL_DIR / "qrels").mkdir(exist_ok=True)

    for local_name, url in files.items():
        local_path = SCIFACT_LOCAL_DIR / local_name
        if local_path.exists():
            print(f"[download] Cache hit: {local_path}", flush=True)
            continue
        try:
            print(f"[download] Fetching {url} ...", flush=True)
            tmp = Path(str(local_path) + ".tmp")
            urllib.request.urlretrieve(url, tmp)
            if url.endswith(".gz"):
                import gzip
                with gzip.open(tmp, "rb") as gz_in, open(local_path, "wb") as out:
                    out.write(gz_in.read())
                tmp.unlink()
            else:
                tmp.rename(local_path)
            print(f"[download] Saved → {local_path}", flush=True)
        except Exception as e:
            print(f"[download] HF individual failed for {local_name}: {e}", flush=True)
            return False
    return True


def _try_hf_non_gz() -> bool:
    """Try plain (non-gzipped) HuggingFace files."""
    base = "https://huggingface.co/datasets/BeIR/scifact/resolve/main"
    files = {
        "corpus.jsonl": f"{base}/corpus.jsonl",
        "queries.jsonl": f"{base}/queries.jsonl",
        "qrels/test.tsv": f"{base}/qrels/test.tsv",
    }
    SCIFACT_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    (SCIFACT_LOCAL_DIR / "qrels").mkdir(exist_ok=True)

    for local_name, url in files.items():
        local_path = SCIFACT_LOCAL_DIR / local_name
        if local_path.exists():
            print(f"[download] Cache hit: {local_path}", flush=True)
            continue
        try:
            print(f"[download] Fetching {url} ...", flush=True)
            urllib.request.urlretrieve(url, local_path)
            size_kb = local_path.stat().st_size // 1024
            print(f"[download] Saved {size_kb} KB → {local_path}", flush=True)
        except Exception as e:
            print(f"[download] HF plain failed for {local_name}: {e}", flush=True)
            return False
    return True


def _try_datasets_lib() -> bool:
    """Try using the `datasets` library to download and export."""
    try:
        from datasets import load_dataset
    except ImportError:
        print("[download] `datasets` library not available", flush=True)
        return False

    SCIFACT_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    (SCIFACT_LOCAL_DIR / "qrels").mkdir(exist_ok=True)

    corpus_path = SCIFACT_LOCAL_DIR / "corpus.jsonl"
    queries_path = SCIFACT_LOCAL_DIR / "queries.jsonl"
    qrels_path = SCIFACT_LOCAL_DIR / "qrels" / "test.tsv"

    try:
        if not corpus_path.exists():
            print("[download] Loading corpus via datasets lib ...", flush=True)
            ds = load_dataset("BeIR/scifact", "corpus", split="corpus")
            with open(corpus_path, "w") as f:
                for row in ds:
                    f.write(json.dumps({"_id": str(row["_id"]), "title": row.get("title", ""),
                                        "text": row.get("text", "")}) + "\n")
            print(f"[download] Wrote {corpus_path}", flush=True)

        if not queries_path.exists():
            print("[download] Loading queries via datasets lib ...", flush=True)
            ds = load_dataset("BeIR/scifact", "queries", split="queries")
            with open(queries_path, "w") as f:
                for row in ds:
                    f.write(json.dumps({"_id": str(row["_id"]), "text": row.get("text", "")}) + "\n")
            print(f"[download] Wrote {queries_path}", flush=True)

        if not qrels_path.exists():
            print("[download] Loading qrels via datasets lib ...", flush=True)
            ds = load_dataset("BeIR/scifact-qrels", split="test")
            with open(qrels_path, "w") as f:
                f.write("query-id\tcorpus-id\tscore\n")
                for row in ds:
                    f.write(f"{row['query-id']}\t{row['corpus-id']}\t{row['score']}\n")
            print(f"[download] Wrote {qrels_path}", flush=True)

        return True
    except Exception as e:
        print(f"[download] datasets lib failed: {e}", flush=True)
        return False


def download_scifact():
    """Ensure all three data files are present in SCIFACT_LOCAL_DIR."""
    corpus_path = SCIFACT_LOCAL_DIR / "corpus.jsonl"
    queries_path = SCIFACT_LOCAL_DIR / "queries.jsonl"
    qrels_path = SCIFACT_LOCAL_DIR / "qrels" / "test.tsv"

    if corpus_path.exists() and queries_path.exists() and qrels_path.exists():
        print("[download] All files cached, skipping download.", flush=True)
        return

    # Try download strategies in order
    for strategy in [_try_ukp_zip, _try_hf_individual, _try_hf_non_gz, _try_datasets_lib]:
        if corpus_path.exists() and queries_path.exists() and qrels_path.exists():
            break
        strategy()

    # Final check
    missing = []
    for name, path in [("corpus", corpus_path), ("queries", queries_path), ("qrels", qrels_path)]:
        if not path.exists():
            missing.append(name)
    if missing:
        raise RuntimeError(f"Download failed. Missing files: {missing}. "
                           f"Check {SCIFACT_LOCAL_DIR}")
    print("[download] All files present.", flush=True)


# ── Step 2: parse ─────────────────────────────────────────────────────────────

def split_context(context: str, max_len: int = CHUNK_MAX) -> list[tuple[int, int, str]]:
    """Split context into chunks ≤ max_len chars at sentence boundaries.

    Returns list of (start_char, end_char, chunk_text).
    """
    # Split at English sentence terminators; keep delimiters
    parts = re.split(r'(?<=[.!?\n])\s*', context)
    chunks = []
    cur = ""
    cur_start = 0

    for part in parts:
        if not part:
            continue
        if len(cur) + len(part) <= max_len:
            cur = cur + (" " if cur and not cur.endswith(" ") else "") + part
        else:
            if cur:
                end = cur_start + len(cur)
                chunks.append((cur_start, end, cur))
                cur_start = end
            # If a single part exceeds max_len, hard-split it
            while len(part) > max_len:
                chunks.append((cur_start, cur_start + max_len, part[:max_len]))
                cur_start += max_len
                part = part[max_len:]
            cur = part
    if cur:
        chunks.append((cur_start, cur_start + len(cur), cur))
    return chunks if chunks else [(0, len(context), context)]


def parse_scifact(local_dir: Path):
    """Parse BEIR SciFact data files.

    Returns:
        rows     – list of (slug, title, content) for FTS indexing
        qa_pairs – list of (query_text, [expected_slug, ...])
        skipped  – count of queries with no relevance judgments
    """
    corpus_path = local_dir / "corpus.jsonl"
    queries_path = local_dir / "queries.jsonl"
    qrels_path = local_dir / "qrels" / "test.tsv"

    # Load corpus
    corpus: dict[str, dict] = {}
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            doc = json.loads(line)
            doc_id = str(doc.get("_id", ""))
            corpus[doc_id] = doc
    print(f"[parse] Loaded {len(corpus)} corpus docs", flush=True)

    # Load queries
    queries: dict[str, str] = {}
    with open(queries_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            qid = str(q.get("_id", ""))
            queries[qid] = q.get("text", "")
    print(f"[parse] Loaded {len(queries)} queries", flush=True)

    # Load qrels
    qrels: dict[str, list[str]] = {}
    with open(qrels_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("query-id"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            qid, doc_id, score = parts[0], parts[1], parts[2]
            try:
                s = int(score)
            except ValueError:
                s = 0
            if s >= 1:
                qrels.setdefault(qid, []).append(doc_id)
    test_query_ids = set(qrels.keys())
    print(f"[parse] Loaded qrels for {len(test_query_ids)} test queries", flush=True)

    # Build FTS rows from corpus (all docs, chunked just in case)
    rows: list[tuple[str, str, str]] = []
    # Map from doc_id → list of (slug, start, end) for span lookup
    doc_chunk_map: dict[str, list[tuple[str, int, int]]] = {}

    for doc_id, doc in corpus.items():
        title = doc.get("title", "")
        text = doc.get("text", "")
        content = (title + " " + text).strip() if title else text
        chunks = split_context(content)
        chunk_list = []
        for idx, (start, end, chunk_text) in enumerate(chunks):
            slug = f"scifact_{doc_id}_{idx}" if len(chunks) > 1 else f"scifact_{doc_id}"
            rows.append((slug, title, chunk_text))
            chunk_list.append((slug, start, end))
        doc_chunk_map[doc_id] = chunk_list

    print(f"[parse] Built {len(rows)} chunks from corpus", flush=True)

    # Build QA pairs
    qa_pairs: list[tuple[str, list[str]]] = []
    skipped = 0

    for qid in test_query_ids:
        query_text = queries.get(qid, "")
        if not query_text:
            skipped += 1
            continue

        relevant_doc_ids = qrels.get(qid, [])
        expected_slugs = []
        for doc_id in relevant_doc_ids:
            if doc_id in doc_chunk_map:
                # All chunks of the relevant doc are acceptable answers
                for slug, _, _ in doc_chunk_map[doc_id]:
                    if slug not in expected_slugs:
                        expected_slugs.append(slug)

        if not expected_slugs:
            skipped += 1
            continue

        qa_pairs.append((query_text, expected_slugs))

    print(f"[parse] {len(qa_pairs)} QA pairs, {skipped} skipped", flush=True)
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
    """Build a trigram-compatible FTS5 OR-query from an English question.

    FTS5 trigram tokenizer requires each search term ≥3 chars.
    Strategy (OR semantics for maximum recall):
    1. Split on whitespace and punctuation
    2. Filter stopwords and short terms (< 3 chars)
    3. Join up to 15 terms with OR
    """
    # Tokenize: split on whitespace/punctuation, lowercase
    tokens = re.split(r'[\s\W]+', question.lower())

    # Filter: ≥3 chars, not stopword
    terms = []
    seen = set()
    for t in tokens:
        t = t.strip()
        if len(t) >= 3 and t not in STOPWORDS and t not in seen:
            # For trigram FTS5, we just use the word directly (works great for English)
            terms.append(t)
            seen.add(t)

    if not terms:
        # Last resort fallback: take first 3 chars of question
        clean = re.sub(r'\W+', '', question.lower())
        return clean[:3] if len(clean) >= 3 else question

    return " OR ".join(terms[:15])


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
        # FTS5 can throw on invalid query syntax; fall back to empty
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

        if (i + 1) % 50 == 0:
            pct5 = hits[5] / (i + 1) * 100
            print(f"  {i+1}/{total}  Hit@5={pct5:.1f}%", flush=True)

    return hits, sample_failures


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    print("=== SciFact (BEIR) Benchmark ===", flush=True)

    # 1. Download
    download_scifact()

    # 2. Parse
    rows, qa_pairs, skipped = parse_scifact(SCIFACT_LOCAL_DIR)

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
        pct = hits[k] / evaluated * 100 if evaluated else 0
        print(f"  Hit@{k}: {hits[k]}/{evaluated} = {pct:.2f}%")

    hit5 = hits[5] / evaluated if evaluated else 0
    gap = hit5 - INTERNAL_BASELINE_HIT5

    result = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "BEIR SciFact test split",
        "total": total_reported,
        "evaluated": evaluated,
        "skipped": skipped,
        "hit_at_1": round(hits[1] / evaluated, 4) if evaluated else 0,
        "hit_at_3": round(hits[3] / evaluated, 4) if evaluated else 0,
        "hit_at_5": round(hits[5] / evaluated, 4) if evaluated else 0,
        "hit_at_10": round(hits[10] / evaluated, 4) if evaluated else 0,
        "internal_baseline_hit_at_5": INTERNAL_BASELINE_HIT5,
        "gap_hit_at_5": round(gap, 4),
        "note": "English corpus, language gap expected vs Chinese internal baseline",
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
