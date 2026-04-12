"""
fts5_bridge.py — Bridge between FTS5 search and closet (v0.7).
When bot searches FTS5, returns closet skeleton by default.
Falls back to raw drawer text on verbatim request.
"""
import sqlite3
from pathlib import Path

FTS5_DB = Path.home() / ".claude-bots" / "memory.db"

def search_fts5(query: str, limit: int = 5) -> list:
    """Search FTS5 and return results with closet skeletons if available."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from radar import read_radar as read_closet, list_radars as list_closets
    from decoder import parse_skeleton

    results = []

    if FTS5_DB.exists():
        conn = sqlite3.connect(str(FTS5_DB))
        conn.row_factory = sqlite3.Row

        try:
            # FTS5 search
            rows = conn.execute(
                "SELECT bot_name, ts, source, text, message_id FROM messages WHERE text MATCH ? LIMIT ?",
                (query, limit)
            ).fetchall()
        except Exception:
            # FTS5 table might not support MATCH — fall back to LIKE
            try:
                rows = conn.execute(
                    "SELECT bot_name, ts, source, chat_id, user, message_id, text FROM messages WHERE text LIKE ? LIMIT ?",
                    (f"%{query}%", limit)
                ).fetchall()
            except Exception:
                rows = []
        finally:
            conn.close()

        for row in rows:
            keys = row.keys()
            results.append({
                'source': 'fts5',
                'bot': row['bot_name'] if 'bot_name' in keys else '',
                'text_preview': str(row['text'])[:200] if row['text'] else '',
                'closet_available': False,
            })

    # Also search closet files for wiki content
    for group in list_closets():
        content = read_closet(group)
        for line in content.splitlines():
            if not line.strip():
                continue
            # Match ASCII query or CJK characters
            query_lower = query.lower()
            line_lower = line.lower()
            cjk_match = any('\u4e00' <= c <= '\u9fff' for c in query) and any(
                c in line for c in query if '\u4e00' <= c <= '\u9fff'
            )
            if query_lower in line_lower or cjk_match:
                parsed = parse_skeleton(line)
                results.append({
                    'source': f'closet:{group}',
                    'slug': parsed.get('slug', ''),
                    'skeleton': line,
                    'entities': parsed.get('entities', []),
                    'closet_available': True,
                })

    return results

if __name__ == "__main__":
    import sys
    query = sys.argv[1] if len(sys.argv) > 1 else "ChannelLab"
    results = search_fts5(query)
    print(f"Found {len(results)} results for '{query}':")
    for r in results:
        print(f"  [{r['source']}] {r.get('slug', r.get('text_preview', '')[:60])}")
