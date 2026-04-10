"""
backfill_closet.py — Encode Obsidian wiki notes → closet table in memory.db.

Usage:
  python3 backfill_closet.py --sample 100   # encode first N notes, print report
  python3 backfill_closet.py --full          # full backfill (skips unchanged via source_hash)
  python3 backfill_closet.py --stats         # show closet table stats
"""
import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

# Add shared libs to path
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "fts5"))

import tiktoken
from encoder import parse_wiki_note, encode_to_skeleton

enc = tiktoken.get_encoding("cl100k_base")

VAULT_ROOT = Path.home() / "Documents" / "Obsidian Vault"
DB_PATH = Path.home() / ".claude-bots" / "memory.db"


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS closet (
        slug TEXT PRIMARY KEY,
        clsc TEXT NOT NULL,
        tokens INTEGER NOT NULL,
        drawer_path TEXT,
        source_hash TEXT NOT NULL,
        encoded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS closet_encoded_at ON closet(encoded_at)")
    # v0.1.1: widen slug column to support relative-path slugs (up to 200 chars)
    # SQLite TEXT has no enforced length limit, but we document the intent here
    conn.commit()
    return conn


def make_slug(path: Path, vault_root: Path) -> str:
    """
    Generate a collision-resistant slug from vault-relative path.
    e.g. Ocean/Chart/Knowledge-Infra-ADR-2026-04-08.md
      → Wiki-Concepts-Knowledge-Infra-ADR-2026-04-08
    Separator: hyphen. Max length: 200 chars.
    """
    try:
        rel = path.relative_to(vault_root).with_suffix("")
    except ValueError:
        rel = path.with_suffix("")
    slug = str(rel).replace("/", "-").replace("\\", "-").replace(" ", "-")
    slug = re.sub(r"[^A-Za-z0-9\-_]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:200]


def source_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def already_encoded(conn, slug: str, sh: str) -> bool:
    row = conn.execute(
        "SELECT source_hash FROM closet WHERE slug = ?", (slug,)
    ).fetchone()
    return row is not None and row[0] == sh


def encode_file(path: Path) -> dict | None:
    """Encode a single .md file. Returns None on error."""
    try:
        content = path.read_text(encoding="utf-8")
        note = parse_wiki_note(str(path))
        clsc = encode_to_skeleton(note)
        clsc_tokens = len(enc.encode(clsc))
        orig_tokens = note["raw_tokens"]
        ratio = clsc_tokens / orig_tokens if orig_tokens > 0 else 1.0
        # Category from path (first subdir under vault)
        try:
            rel = path.relative_to(VAULT_ROOT)
            category = rel.parts[0] if len(rel.parts) > 1 else "root"
        except ValueError:
            category = "unknown"
        return {
            "slug": note["slug"],
            "clsc": clsc,
            "tokens": clsc_tokens,
            "drawer_path": str(path),
            "source_hash": hashlib.md5(content.encode()).hexdigest(),
            "orig_tokens": orig_tokens,
            "ratio": ratio,
            "category": category,
        }
    except Exception as e:
        return {"error": str(e), "path": str(path)}


def run_backfill(limit: int | None = None, verbose: bool = True):
    if not VAULT_ROOT.exists():
        print(f"[ERROR] Vault not found: {VAULT_ROOT}")
        return

    _IGNORE_DIRS = {".stversions", ".obsidian", ".trash", "_drafts", "_archive"}

    def _should_skip(path: Path) -> bool:
        """Return True for noise files that should not be indexed."""
        parts = set(path.parts)
        if parts & _IGNORE_DIRS:
            return True
        # sync-conflict files from Syncthing / iCloud
        if "sync-conflict" in path.name or path.name.startswith("."):
            return True
        return False

    conn = get_conn()
    files = sorted(p for p in VAULT_ROOT.rglob("*.md") if not _should_skip(p))
    if limit:
        files = files[:limit]

    total = len(files)
    encoded = 0
    skipped = 0
    errors = 0
    rows = []

    print(f"Processing {total} files (limit={limit})...")

    batch = []
    for i, path in enumerate(files, 1):
        sh = source_hash(path)
        slug = make_slug(path, VAULT_ROOT)

        if already_encoded(conn, slug, sh):
            skipped += 1
            continue

        result = encode_file(path)
        if result is None or "error" in result:
            errors += 1
            if verbose:
                print(f"  [ERR] {path.name}: {result.get('error','?') if result else 'None'}")
            continue

        result["slug"] = make_slug(path, VAULT_ROOT)  # vault-relative slug
        batch.append(result)
        rows.append(result)
        encoded += 1

        # Commit every 50
        if len(batch) >= 50:
            conn.executemany(
                "INSERT OR REPLACE INTO closet (slug, clsc, tokens, drawer_path, source_hash) "
                "VALUES (:slug, :clsc, :tokens, :drawer_path, :source_hash)",
                batch,
            )
            conn.commit()
            batch.clear()
            if verbose:
                print(f"  [{i}/{total}] encoded={encoded} skipped={skipped} errors={errors}")

    # Final batch
    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO closet (slug, clsc, tokens, drawer_path, source_hash) "
            "VALUES (:slug, :clsc, :tokens, :drawer_path, :source_hash)",
            batch,
        )
        conn.commit()

    conn.close()

    # Report
    if rows:
        total_orig = sum(r["orig_tokens"] for r in rows)
        total_clsc = sum(r["tokens"] for r in rows)
        overall_ratio = total_clsc / total_orig if total_orig > 0 else 1.0

        print(f"\n{'='*60}")
        print(f"SAMPLE REPORT ({encoded} files encoded, {skipped} skipped, {errors} errors)")
        print(f"{'='*60}")
        print(f"Total original tokens : {total_orig:,}")
        print(f"Total CLSC tokens     : {total_clsc:,}")
        print(f"Overall ratio         : {overall_ratio:.1%}  (savings: {(1-overall_ratio):.1%})")

        # Per-category
        cats = {}
        for r in rows:
            c = r["category"]
            if c not in cats:
                cats[c] = {"count": 0, "orig": 0, "clsc": 0}
            cats[c]["count"] += 1
            cats[c]["orig"] += r["orig_tokens"]
            cats[c]["clsc"] += r["tokens"]

        print(f"\n{'Category':<30} {'Count':>6} {'Orig':>8} {'CLSC':>8} {'Ratio':>8}")
        print("-" * 65)
        for cat, v in sorted(cats.items()):
            ratio = v["clsc"] / v["orig"] if v["orig"] > 0 else 1.0
            print(f"{cat:<30} {v['count']:>6} {v['orig']:>8,} {v['clsc']:>8,} {ratio:>7.1%}")

        print(f"\nFirst 10 rows:")
        print(f"{'Slug':<35} {'Orig':>6} {'CLSC':>5} {'Ratio':>7}  Preview")
        print("-" * 100)
        for r in rows[:10]:
            preview = r["clsc"][:60].replace("\n", " ")
            print(f"{r['slug'][:35]:<35} {r['orig_tokens']:>6} {r['tokens']:>5} {r['ratio']:>7.1%}  {preview}")

    return rows


def run_query_comparison(queries: list[str]):
    """Compare verbatim vs closet token counts for a list of queries."""
    conn = get_conn()
    print(f"\n{'Query':<25} {'Verbatim':>10} {'Closet':>8} {'Savings':>8}")
    print("-" * 58)
    results = []
    for q in queries:
        # Closet: full-text search on clsc column (LIKE fallback)
        rows_closet = conn.execute(
            "SELECT clsc, tokens FROM closet WHERE clsc LIKE ? LIMIT 5",
            (f"%{q}%",)
        ).fetchall()
        # Verbatim: read drawer files for matched slugs
        slugs = [r for r in conn.execute(
            "SELECT slug, drawer_path FROM closet WHERE clsc LIKE ? LIMIT 5",
            (f"%{q}%",)
        ).fetchall()]
        verbatim_tokens = 0
        for _, dp in slugs:
            if dp and Path(dp).exists():
                verbatim_tokens += len(enc.encode(Path(dp).read_text(encoding="utf-8")))
        closet_tokens = sum(r[1] for r in rows_closet)
        savings = (1 - closet_tokens / verbatim_tokens) if verbatim_tokens > 0 else 0.0
        print(f"{q:<25} {verbatim_tokens:>10,} {closet_tokens:>8,} {savings:>7.1%}")
        results.append({"query": q, "verbatim": verbatim_tokens, "closet": closet_tokens, "savings": savings})
    conn.close()
    if results:
        avg_savings = sum(r["savings"] for r in results) / len(results)
        print(f"\nAverage savings: {avg_savings:.1%}")
    return results


def run_stats():
    conn = get_conn()
    count = conn.execute("SELECT COUNT(*) FROM closet").fetchone()[0]
    total_clsc = conn.execute("SELECT SUM(tokens) FROM closet").fetchone()[0] or 0
    print(f"Closet rows: {count}")
    print(f"Total CLSC tokens: {total_clsc:,}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, help="Encode first N files")
    parser.add_argument("--full", action="store_true", help="Full backfill")
    parser.add_argument("--stats", action="store_true", help="Show stats")
    args = parser.parse_args()

    if args.stats:
        run_stats()
    elif args.full:
        run_backfill(limit=None)
    elif args.sample:
        rows = run_backfill(limit=args.sample)
        # Token comparison
        queries = ["股權結構", "學習筆記", "Bonk GEO", "CLSC", "Knowledge Infra",
                   "ChannelLab", "任務", "CEO", "anna", "bot"]
        print("\n" + "="*60)
        print("TOKEN COMPARISON: Verbatim vs Closet (top-5 per query)")
        print("="*60)
        run_query_comparison(queries)
    else:
        parser.print_help()
