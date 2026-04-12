"""
hancloset.py — Main wrapper for CLSC v0.6 HanCloset.
Bot-facing API: get_wiki_summary(slug, mode)
"""
from radar import read_radar as read_closet, store_sonar as store_skeleton, list_radars as list_closets
from encoder import encode_note
from decoder import verbatim_fetch, narrative_expand, parse_skeleton, find_drawer
from pathlib import Path
import re

def get_wiki_summary(slug: str, mode: str = 'verbatim', group: str = 'general') -> str:
    """
    Get a wiki note summary.

    mode='verbatim': fetch original drawer markdown (no LLM, accurate)
    mode='narrative': expand closet skeleton to natural prose (fast, lossy)
    mode='skeleton': return raw AAAK skeleton (for bot context injection)
    """
    if mode == 'verbatim':
        return verbatim_fetch(slug)

    # Find skeleton in closet
    closet_content = read_closet(group)
    skeleton = None
    for line in closet_content.splitlines():
        if not line.strip():
            continue
        parsed = parse_skeleton(line)
        if parsed['slug'] == slug or slug in line:
            skeleton = line
            break

    if not skeleton:
        # Fall back to verbatim if not in closet
        return verbatim_fetch(slug)

    if mode == 'skeleton':
        return skeleton

    if mode == 'narrative':
        return narrative_expand(skeleton)

    return verbatim_fetch(slug)

def encode_and_store(note_path: str, group: str = 'general') -> dict:
    """Encode a wiki note and store in closet. Returns metrics."""
    result = encode_note(note_path)
    store_skeleton(group, result['slug'], result['skeleton'])
    return result
