#!/usr/bin/env python3
"""一次性 backfill：掃全部來源。

v0.1: inbox/messages/*.json[.delivered]
v0.2: relay-messages.log + ~/.claude/projects/*/memory/*.md

用法：
    python3 ~/.claude-bots/shared/fts5/backfill.py [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))
from lib import (  # noqa: E402
    DB_PATH,
    ingest_all_memory_md,
    ingest_dir,
    ingest_relay_messages_log,
    open_db,
)

try:
    from memocean_mcp.config import MEMOCEAN_DATA_DIR as _DATA_DIR
    STATE_DIR = _DATA_DIR / "state"
except Exception:
    STATE_DIR = Path(os.path.expanduser('~/.memocean/state'))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='parse + count but rollback')
    args = ap.parse_args()

    if not STATE_DIR.is_dir():
        print(f'❌ {STATE_DIR} 不存在', file=sys.stderr)
        return 1

    print(f'🗄  DB: {DB_PATH}')
    conn = open_db()
    t0 = time.time()

    # ── 1. inbox/messages ──
    inbox_inserted = 0
    inbox_scanned = 0
    print('── inbox/messages ──')
    for bot_dir in sorted(STATE_DIR.iterdir()):
        msg_dir = bot_dir / 'inbox' / 'messages'
        if not msg_dir.is_dir():
            continue
        scanned = sum(
            1
            for f in msg_dir.iterdir()
            if f.name.endswith('.json') or f.name.endswith('.json.delivered')
        )
        inserted = ingest_dir(conn, msg_dir)
        inbox_scanned += scanned
        inbox_inserted += inserted
        print(f'  {bot_dir.name:20s}  scanned={scanned:5d}  inserted={inserted:5d}')

    # ── 2. relay-messages.log ──
    relay_inserted = 0
    print('── relay-messages.log ──')
    for bot_dir in sorted(STATE_DIR.iterdir()):
        log = bot_dir / 'relay-messages.log'
        if not log.is_file():
            continue
        inserted = ingest_relay_messages_log(conn, log, bot_dir.name)
        relay_inserted += inserted
        print(f'  {bot_dir.name:20s}  inserted={inserted:6d}')

    # ── 3. memory/*.md ──
    print('── memory/*.md ──')
    memory_inserted = ingest_all_memory_md(conn)
    print(f'  inserted={memory_inserted}')

    if args.dry_run:
        conn.rollback()
        print('(dry-run: rolled back)')
    else:
        conn.commit()

    # 統計
    cur = conn.execute('SELECT COUNT(*) FROM messages')
    db_total = cur.fetchone()[0]
    elapsed = time.time() - t0
    print()
    print(f'✅ inbox={inbox_inserted}  relay={relay_inserted}  memory={memory_inserted}')
    print(f'   total_inserted={inbox_inserted + relay_inserted + memory_inserted}  db_total={db_total}  ({elapsed:.2f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
