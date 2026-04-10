#!/usr/bin/env python3
"""FTS5 跨 bot 訊息搜尋 CLI。

用法：
    python3 search.py 'NOX 質押'
    python3 search.py 'NOX OR Bonk'           # boolean
    python3 search.py '"主廚 重啟"'           # phrase
    python3 search.py 'NEAR(關鍵詞 另一詞, 5)'  # NEAR

支援的旗標：
    --limit N        top N（預設 10）
    --bot NAME       只看某個 bot 的 inbox
    --json           JSON 輸出（給程式呼叫用）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import open_db  # noqa: E402


def _row(r, snippet_idx=6):
    return {
        'bot_name': r[0],
        'ts': r[1],
        'source': r[2],
        'chat_id': r[3],
        'user': r[4],
        'message_id': r[5],
        'snippet': r[snippet_idx],
        'rank': r[7] if len(r) > 7 else 0.0,
    }


def search(query: str, limit: int = 10, bot: str | None = None) -> list[dict]:
    """
    雙層搜尋：
      1. FTS5 trigram (BM25 ranking) — 最快、有 ranking
      2. 0 結果時 fallback 到 LIKE 子字串搜尋（給 <3 char 中文 token 用，
         例如「重啟」「短詞」這類 trigram tokenizer 接不到的詞）
    """
    conn = open_db()

    # ── 1. FTS5 trigram ──
    sql = (
        "SELECT bot_name, ts, source, chat_id, user, message_id, "
        "snippet(messages, 6, '«', '»', '…', 12) AS snip, "
        "bm25(messages) AS rank "
        "FROM messages WHERE messages MATCH ? "
    )
    params: list = [query]
    if bot:
        sql += 'AND bot_name = ? '
        params.append(bot)
    sql += 'ORDER BY rank LIMIT ?'
    params.append(limit)
    rows = []
    try:
        for r in conn.execute(sql, params):
            rows.append(_row(r))
    except Exception:
        # FTS5 syntax error → 直接走 LIKE fallback
        rows = []

    if rows:
        return rows

    # ── 2. LIKE fallback（給短 CJK token） ──
    # 把 query 用空白拆成 tokens，每 token 一個 LIKE，全部 AND
    tokens = [t.strip() for t in query.split() if t.strip()]
    if not tokens:
        return []
    # 注意：FTS5 虛擬表上的 LIKE/GLOB 不可靠（會被 tokenizer 接管），用 instr() 才行
    where = ' AND '.join('instr(text, ?) > 0' for _ in tokens)
    like_params = list(tokens)
    sql2 = (
        "SELECT bot_name, ts, source, chat_id, user, message_id, "
        "substr(text, 1, 120) AS snip "
        "FROM messages WHERE " + where + ' '
    )
    if bot:
        sql2 += 'AND bot_name = ? '
        like_params.append(bot)
    sql2 += 'ORDER BY ts DESC LIMIT ?'
    like_params.append(limit)
    for r in conn.execute(sql2, like_params):
        rows.append({
            'bot_name': r[0],
            'ts': r[1],
            'source': r[2],
            'chat_id': r[3],
            'user': r[4],
            'message_id': r[5],
            'snippet': '[LIKE] ' + (r[6] or ''),
            'rank': 0.0,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description='FTS5 跨 bot 訊息搜尋')
    ap.add_argument('query', help='FTS5 query string')
    ap.add_argument('--limit', type=int, default=10)
    ap.add_argument('--bot', help='限定 bot_name')
    ap.add_argument('--json', action='store_true', help='JSON 輸出')
    args = ap.parse_args()

    t0 = time.time()
    try:
        rows = search(args.query, args.limit, args.bot)
    except Exception as e:
        print(f'❌ query failed: {e}', file=sys.stderr)
        return 2
    elapsed_ms = (time.time() - t0) * 1000

    if args.json:
        print(json.dumps({'query': args.query, 'elapsed_ms': elapsed_ms, 'results': rows}, ensure_ascii=False))
        return 0

    if not rows:
        print(f'(no results, {elapsed_ms:.1f}ms)')
        return 0

    for i, r in enumerate(rows, 1):
        print(f'#{i:2d}  [{r["bot_name"]}/{r["source"]}]  {r["ts"]}  rank={r["rank"]:.2f}')
        print(f'     chat={r["chat_id"]} msg={r["message_id"]} user={r["user"]}')
        print(f'     {r["snippet"]}')
        print()
    print(f'({len(rows)} results, {elapsed_ms:.1f}ms)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
