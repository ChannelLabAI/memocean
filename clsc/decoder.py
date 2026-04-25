"""
decoder.py — Decode closet skeleton back to useful content (v0.7).
Mode (i): verbatim — fetch original from drawer (no LLM)
Mode (ii): narrative — LLM-expand skeleton to natural prose (mock or real)
Changes from v0.6: narrative_expand() now supports real LLM call with auto-detect.
"""
import os
import re
import sys
from pathlib import Path

# Resolve vault root via config when available
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from memocean_mcp.config import MEMOCEAN_VAULT_ROOT as _VAULT_ROOT, MEMOCEAN_DATA_DIR as _DATA_DIR
    # Drawer root — Obsidian Wiki subdirectory
    OBSIDIAN_WIKI = _VAULT_ROOT / "Wiki"
    # Fallback drawer under data dir
    FALLBACK_DRAWERS = [_DATA_DIR / "bots" / "builder" / "research"]
except Exception:
    OBSIDIAN_WIKI = Path.home() / "Documents" / "Obsidian Vault" / "Wiki"
    FALLBACK_DRAWERS = [Path.home() / ".memocean" / "bots" / "builder" / "research"]

def find_drawer(slug: str) -> Path:
    """Find the original markdown file for a slug."""
    # Search Obsidian Wiki
    if OBSIDIAN_WIKI.exists():
        for p in OBSIDIAN_WIKI.rglob(f"{slug}.md"):
            return p
    # Search fallback dirs
    for d in FALLBACK_DRAWERS:
        p = d / f"{slug}.md"
        if p.exists():
            return p
    return None

def verbatim_fetch(slug: str) -> str:
    """Mode (i): return original drawer content."""
    path = find_drawer(slug)
    if path:
        return path.read_text(encoding='utf-8')
    return f"[drawer not found for {slug}]"

def parse_skeleton(skeleton: str) -> dict:
    """Parse an AAAK skeleton line back into structured fields."""
    result = {'slug': '', 'title': '', 'entities': [], 'key_sentences': [], 'tags': []}

    # Header [slug|title]
    header_match = re.match(r'\[([^|]+)\|([^\]]+)\]', skeleton)
    if header_match:
        result['slug'] = header_match.group(1)
        result['title'] = header_match.group(2)

    # ENT field
    ent_match = re.search(r'ENT:([^\s]+)', skeleton)
    if ent_match:
        result['entities'] = ent_match.group(1).split(',')

    # KEY field — capture everything after KEY: up to TAG: or end of line
    key_match = re.search(r'KEY:(.+?)(?:\s+TAG:|$)', skeleton)
    if key_match:
        result['key_sentences'] = key_match.group(1).strip().split('|')

    # TAG field
    tag_match = re.search(r'TAG:([^\s]+)', skeleton)
    if tag_match:
        result['tags'] = tag_match.group(1).split(',')

    return result

def narrative_expand(skeleton: str, use_llm: bool = False) -> str:
    """
    Mode (ii): expand skeleton to natural prose.
    use_llm=False → template fallback / mock mode (default — safe when key has 0 credit)
    use_llm=True → real LLM call via Haiku (enable when Anthropic key is funded)
    use_llm=None → auto-detect (deprecated — use explicit True/False instead)
    """
    parsed = parse_skeleton(skeleton)

    # Determine mode
    if use_llm is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        use_llm = bool(api_key and api_key != "sk-ant-PLACEHOLDER")

    if use_llm:
        import anthropic
        client = anthropic.Anthropic()
        prompt = f"""You are expanding a compact AAAK skeleton note into natural Chinese prose.

Skeleton: {skeleton}

Write 2-3 natural Chinese sentences that capture the key information.
Be concise and factual. Do not add information not in the skeleton."""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text
    else:
        # Template fallback (mock mode)
        title = parsed.get('title', '?')
        entities = '、'.join(parsed.get('entities', []))
        keys = '。'.join(s for s in parsed.get('key_sentences', []) if s.strip())

        prose = f"關於「{title}」"
        if entities:
            prose += f"：主要涉及 {entities}"
        if keys:
            prose += f"。{keys}"
        prose += "。"
        return prose
