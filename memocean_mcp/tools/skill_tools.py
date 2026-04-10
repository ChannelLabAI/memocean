"""
skill_tools.py — List and retrieve approved learned skills.

Skill structure: each skill is a directory under LEARNED_SKILLS_DIR containing:
  SKILL.md   — main skill definition (primary content)
  USAGE.md   — usage examples (optional)
  EXAMPLE.md — worked examples (optional)

Fallback: also supports plain .md files directly in LEARNED_SKILLS_DIR.
"""
import re
from pathlib import Path
from typing import Optional

from ..config import LEARNED_SKILLS_DIR

_SAFE_NAME_RE = re.compile(r'^[A-Za-z0-9_\-]{1,100}$')


def _validate_name(name: str) -> None:
    """Reject names that could escape the sandbox via path traversal."""
    stem = name.removesuffix(".md")
    if not _SAFE_NAME_RE.match(stem):
        raise ValueError(f"Invalid skill name '{name}': must match [A-Za-z0-9_-]{{1,100}}")


def skill_list() -> list[str]:
    """
    List all approved skill names.
    Scans for subdirectories (skill bundles) and plain .md files.
    Returns empty list if directory does not exist.
    """
    if not LEARNED_SKILLS_DIR.exists():
        return []

    names = []
    for p in LEARNED_SKILLS_DIR.iterdir():
        if p.is_dir() and not p.name.startswith("."):
            # Skill bundle directory
            names.append(p.name)
        elif p.is_file() and p.suffix == ".md":
            # Plain markdown skill file
            names.append(p.stem)

    return sorted(names)


def skill_get(name: str, section: str = "SKILL") -> str:
    """
    Get the content of a named skill.

    name: skill name (directory name or .md stem)
    section: which file to read from a skill bundle — 'SKILL' (default), 'USAGE', or 'EXAMPLE'

    Returns file content, or error string if not found.
    """
    _validate_name(name)
    if not LEARNED_SKILLS_DIR.exists():
        return f"[learned-skills directory not found at {LEARNED_SKILLS_DIR}]"

    stem = name.removesuffix(".md")

    # Try skill bundle directory first
    bundle_dir = LEARNED_SKILLS_DIR / stem
    if bundle_dir.is_dir():
        skill_file = bundle_dir / f"{section.upper()}.md"
        if not skill_file.exists():
            # Fall back to SKILL.md
            skill_file = bundle_dir / "SKILL.md"
        if skill_file.exists():
            return skill_file.read_text(encoding="utf-8")
        # Return all files concatenated
        parts = []
        for fname in ("SKILL.md", "USAGE.md", "EXAMPLE.md"):
            fp = bundle_dir / fname
            if fp.exists():
                parts.append(f"# {fname}\n\n{fp.read_text(encoding='utf-8')}")
        return "\n\n---\n\n".join(parts) if parts else f"[skill bundle '{stem}' is empty]"

    # Try plain .md file
    path = LEARNED_SKILLS_DIR / f"{stem}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")

    available = skill_list()
    return f"[skill '{stem}' not found. Available: {available}]"
