#!/usr/bin/env python3
"""增量 ingest：給 fts5-ingest.sh hook 呼叫。

用法：
    python3 ingest_one.py /abs/path/to/inbox/messages/         # dir → inbox ingest
    python3 ingest_one.py /abs/path/to/relay-messages.log      # file → relay ingest
    python3 ingest_one.py /abs/path/to/memory/foo.md           # file → memory md ingest

idempotent，重複呼叫安全。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib import (  # noqa: E402
    ingest_dir,
    ingest_memory_md,
    ingest_relay_messages_log,
    open_db,
)


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: ingest_one.py <messages_dir|relay-messages.log|*.md>', file=sys.stderr)
        return 1
    target = Path(sys.argv[1])
    conn = open_db()
    n = 0
    label = ''
    if target.is_dir():
        n = ingest_dir(conn, target)
        label = f'inbox dir {target}'
    elif target.name == 'relay-messages.log':
        # bot_name = parent dir name (…/state/<bot>/relay-messages.log)
        bot_name = target.parent.name
        n = ingest_relay_messages_log(conn, target, bot_name)
        label = f'relay-log {bot_name}'
    elif target.suffix == '.md':
        n = 1 if ingest_memory_md(conn, target) else 0
        label = f'memory-md {target.name}'
    else:
        print(f'ingest_one: unrecognized target {target}', file=sys.stderr)
        return 1
    conn.commit()
    if n:
        print(f'fts5: ingested {n} new from {label}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
