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
