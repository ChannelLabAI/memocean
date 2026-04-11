"""
closet.py — Closet bundle file management (v0.7).
Stores CLSC skeletons in grouped .clsc.md files.
Changes from v0.6: adds group_from_path() for auto-grouping by wiki subdir.
"""
from pathlib import Path

CLOSET_DIR = Path.home() / ".claude-bots" / "seabed"

def closet_path(group: str) -> Path:
    CLOSET_DIR.mkdir(parents=True, exist_ok=True)
    return CLOSET_DIR / f"wiki-{group}.clsc.md"

def store_skeleton(group: str, slug: str, skeleton: str) -> None:
    """Append or update a skeleton in the group closet file."""
    path = closet_path(group)
    lines = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.startswith('[') and '|' in line:
                s = line.split('|')[0][1:]
                lines[s] = line
    lines[slug] = skeleton
    path.write_text('\n'.join(lines.values()) + '\n', encoding='utf-8')

    # DB upsert — memory.db closet + closet_fts
    try:
        import hashlib
        import sqlite3 as _sqlite3
        _source_hash = hashlib.sha256(skeleton.encode()).hexdigest()
        _tokens = len(skeleton) // 4
        _drawer_path = str(path)
        _db_path = Path.home() / ".claude-bots" / "memory.db"
        _conn = _sqlite3.connect(str(_db_path))
        _conn.execute(
            "INSERT OR REPLACE INTO closet (slug, clsc, tokens, drawer_path, source_hash) VALUES (?, ?, ?, ?, ?)",
            (slug, skeleton, _tokens, _drawer_path, _source_hash)
        )
        _conn.execute("DELETE FROM closet_fts WHERE slug = ?", (slug,))
        _conn.execute(
            "INSERT INTO closet_fts (slug, clsc) VALUES (?, ?)",
            (slug, skeleton)
        )
        _conn.commit()
        _conn.close()
    except Exception:
        pass  # DB sync is best-effort; file write already succeeded

def read_closet(group: str) -> str:
    """Read full closet bundle as a string (for bot context injection)."""
    path = closet_path(group)
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')

def list_closets() -> list:
    """List all closet groups."""
    CLOSET_DIR.mkdir(parents=True, exist_ok=True)
    return [p.name.replace('wiki-', '').replace('.clsc.md', '') for p in CLOSET_DIR.glob('wiki-*.clsc.md')]

def group_from_path(path: str) -> str:
    """Determine closet group from wiki note path."""
    p = Path(path)
    parts = p.parts
    # Find the part after 'Wiki'
    try:
        wiki_idx = next(i for i, part in enumerate(parts) if part == 'Wiki')
        subdir = parts[wiki_idx + 1].lower() if wiki_idx + 1 < len(parts) else 'general'
    except StopIteration:
        subdir = 'general'

    mapping = {
        'research': 'research',
        'concepts': 'concepts',
        'cards': 'cards',
        'companies': 'companies',
        'people': 'people',
        'deals': 'deals',
        'reviews': 'reviews',
    }
    return mapping.get(subdir, 'general')
