"""FTS5 ingest 共用函式。

DB schema 見 schema.sql。任何 ingest 腳本都應透過此模組打開 db 與插入。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Resolve DB_PATH via config when available; fall back to legacy default otherwise.
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from memocean_mcp.config import FTS_DB as DB_PATH
except Exception:
    DB_PATH = Path(os.path.expanduser('~/.memocean/memory.db'))

SCHEMA_PATH = Path(__file__).parent / 'schema.sql'


def open_db() -> sqlite3.Connection:
    """打開 db，第一次自動跑 schema。"""
    first = not DB_PATH.exists()
    conn = sqlite3.connect(str(DB_PATH))
    if first:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
        conn.execute('PRAGMA busy_timeout = 5000')
    else:
        # 確保 PRAGMA 每次連線都套
        conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('PRAGMA synchronous = NORMAL')
        conn.execute('PRAGMA busy_timeout = 5000')
    return conn


def parse_inbox_message(path: Path) -> dict | None:
    """讀一個 inbox/messages/{id}-{ts}.json[.delivered] 檔，回傳結構化 row。

    格式：JSON-RPC notification with params.content + params.meta。
    bot_name 從路徑推：state/{bot_name}/inbox/messages/...
    """
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    params = data.get('params') or {}
    text = params.get('content') or ''
    meta = params.get('meta') or {}

    # 推 bot_name：path 裡 state/{bot}/inbox
    bot_name = ''
    parts = path.parts
    if 'state' in parts:
        i = parts.index('state')
        if i + 1 < len(parts):
            bot_name = parts[i + 1]

    return {
        'bot_name': bot_name,
        'ts': str(meta.get('ts', '')),
        'source': str(meta.get('source', 'telegram')),
        'chat_id': str(meta.get('chat_id', '')),
        'user': str(meta.get('user', '')),
        'message_id': str(meta.get('message_id', '')),
        'text': text,
    }


def insert_row(conn: sqlite3.Connection, row: dict) -> bool:
    """INSERT OR IGNORE — true 表示真的插入了一筆。"""
    if not row.get('text'):
        return False
    key = '|'.join([
        row.get('bot_name', ''),
        row.get('source', ''),
        row.get('chat_id', ''),
        row.get('message_id', ''),
    ])
    cur = conn.execute('INSERT OR IGNORE INTO seen(key) VALUES (?)', (key,))
    if cur.rowcount == 0:
        return False
    conn.execute(
        'INSERT INTO messages(bot_name, ts, source, chat_id, user, message_id, text) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (
            row['bot_name'],
            row['ts'],
            row['source'],
            row['chat_id'],
            row['user'],
            row['message_id'],
            row['text'],
        ),
    )
    return True


RELAY_LINE_RE = re.compile(r'^\[([^\]]+)\] (\S+) → (\S+) \(chat:([^)]+)\): (.*)$')


def ingest_relay_messages_log(conn: sqlite3.Connection, log_path: Path, bot_name: str) -> int:
    """Parse a relay-messages.log and ingest each message. Multi-line bodies are joined with \\n."""
    if not log_path.is_file():
        return 0
    inserted = 0
    current: dict | None = None
    current_lineno = 0

    def flush(row, lineno):
        nonlocal inserted
        if row is None:
            return
        # stable synthetic key
        row['message_id'] = f'relay-msg|{bot_name}|{lineno}'
        if insert_row(conn, row):
            inserted += 1

    with log_path.open('r', errors='replace') as f:
        for lineno, line in enumerate(f, 1):
            line = line.rstrip('\n')
            m = RELAY_LINE_RE.match(line)
            if m:
                flush(current, current_lineno)
                ts, frm, to, chat, text = m.groups()
                current = {
                    'bot_name': bot_name,
                    'ts': ts,
                    'source': 'relay-msg',
                    'chat_id': chat,
                    'user': frm,
                    'message_id': '',
                    'text': text,
                }
                current_lineno = lineno
            else:
                if current is not None:
                    current['text'] += '\n' + line
        flush(current, current_lineno)
    return inserted


def _bot_name_from_project_dir(project_dir_name: str) -> str:
    """e.g. -home-<USER>--claude-bots-bots-builder → builder"""
    name = project_dir_name
    marker = 'bots-'
    idx = name.rfind(marker)
    if idx >= 0:
        tail = name[idx + len(marker):]
        if tail:
            return tail
    # 共用 project (-home-<USER>--claude-bots) 沒有單一 bot 歸屬
    if name.endswith('--claude-bots'):
        return 'shared'
    # fallback: last dash segment
    seg = name.rsplit('-', 1)[-1]
    return seg or project_dir_name


def ingest_memory_md(conn: sqlite3.Connection, md_path: Path) -> bool:
    """Ingest a single memory/*.md file. Returns True if newly inserted."""
    try:
        content = md_path.read_text(errors='replace')
    except Exception:
        return False
    try:
        mtime = md_path.stat().st_mtime
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except Exception:
        ts = ''
    # derive bot name from project dir (…/projects/<project>/memory/<file>.md)
    parts = md_path.parts
    bot_name = ''
    if 'projects' in parts:
        i = parts.index('projects')
        if i + 1 < len(parts):
            bot_name = _bot_name_from_project_dir(parts[i + 1])
    row = {
        'bot_name': bot_name,
        'ts': ts,
        'source': 'memory-md',
        'chat_id': '',
        'user': '',
        'message_id': str(md_path),
        'text': md_path.name + '\n' + content,
    }
    return insert_row(conn, row)


def ingest_all_memory_md(conn: sqlite3.Connection) -> int:
    """Scan ~/.claude/projects/*/memory/*.md and ingest all."""
    base = Path(os.path.expanduser('~/.claude/projects'))
    if not base.is_dir():
        return 0
    n = 0
    for proj in base.iterdir():
        mem_dir = proj / 'memory'
        if not mem_dir.is_dir():
            continue
        for md in mem_dir.glob('*.md'):
            if ingest_memory_md(conn, md):
                n += 1
    return n


def ingest_dir(conn: sqlite3.Connection, dir_path: Path) -> int:
    """掃一個 inbox/messages 目錄，把所有 *.json / *.json.delivered ingest 進 db。"""
    if not dir_path.is_dir():
        return 0
    n = 0
    for f in dir_path.iterdir():
        name = f.name
        if not (name.endswith('.json') or name.endswith('.json.delivered')):
            continue
        row = parse_inbox_message(f)
        if row and insert_row(conn, row):
            n += 1
    return n
