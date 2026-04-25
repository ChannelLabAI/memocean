"""
radar.py — Radar bundle file management (v0.7).
Stores CLSC sonar index in grouped .clsc.md files.
Changes from v0.6: adds group_from_path() for auto-grouping by wiki subdir.
"""
from pathlib import Path

try:
    from memocean_mcp.config import CLOSET_ROOT as RADAR_DIR
except Exception:
    RADAR_DIR = Path.home() / ".memocean" / "seabed"

def radar_path(group: str) -> Path:
    RADAR_DIR.mkdir(parents=True, exist_ok=True)
    return RADAR_DIR / f"wiki-{group}.clsc.md"

def store_sonar(group: str, slug: str, sonar: str) -> None:
    """Append or update a sonar entry in the group radar file."""
    path = radar_path(group)
    lines = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.startswith('[') and '|' in line:
                s = line.split('|')[0][1:]
                lines[s] = line
    lines[slug] = sonar
    path.write_text('\n'.join(lines.values()) + '\n', encoding='utf-8')

    # DB upsert — memory.db radar + radar_fts
    try:
        import hashlib
        import sqlite3 as _sqlite3
        _source_hash = hashlib.sha256(sonar.encode()).hexdigest()
        _tokens = len(sonar) // 4
        _drawer_path = str(path)
        try:
            from memocean_mcp.config import FTS_DB as _db_path
        except Exception:
            _db_path = Path.home() / ".memocean" / "memory.db"
        _conn = _sqlite3.connect(str(_db_path))
        _conn.execute(
            "INSERT OR REPLACE INTO radar (slug, clsc, tokens, drawer_path, source_hash) VALUES (?, ?, ?, ?, ?)",
            (slug, sonar, _tokens, _drawer_path, _source_hash)
        )
        _conn.execute("DELETE FROM radar_fts WHERE slug = ?", (slug,))
        _conn.execute(
            "INSERT INTO radar_fts (slug, clsc) VALUES (?, ?)",
            (slug, sonar)
        )
        _conn.commit()
        _conn.close()
    except Exception:
        pass  # DB sync is best-effort; file write already succeeded

def read_radar(group: str) -> str:
    """Read full radar bundle as a string (for bot context injection)."""
    path = radar_path(group)
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')

def list_radars() -> list:
    """List all radar groups."""
    RADAR_DIR.mkdir(parents=True, exist_ok=True)
    return [p.name.replace('wiki-', '').replace('.clsc.md', '') for p in RADAR_DIR.glob('wiki-*.clsc.md')]

def group_from_path(path: str) -> str:
    """Determine radar group from wiki note path."""
    p = Path(path)
    parts = p.parts
    # Find the part after the vault root ('Ocean' or legacy 'Wiki')
    try:
        anchor_idx = next(i for i, part in enumerate(parts) if part in ('Ocean', 'Wiki'))
        subdir = parts[anchor_idx + 1].lower() if anchor_idx + 1 < len(parts) else 'general'
    except StopIteration:
        subdir = 'general'

    mapping = {
        'research': 'research',
        'chart': 'chart',
        'pearl': 'pearl',
        'concepts': 'concepts',
        'cards': 'cards',
        'companies': 'companies',
        'people': 'people',
        'deals': 'deals',
        'reviews': 'reviews',
    }
    return mapping.get(subdir, 'general')
