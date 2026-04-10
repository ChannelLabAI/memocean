"""
encoder.py — Encode wiki notes to AAAK skeleton format (v0.7).
Target: closet skeleton <= 30% of drawer token count.
Changes from v0.6: H1 title extraction fix (no frontmatter bleed).
"""
import re
import tiktoken
from pathlib import Path
from han_ner import extract_entities, extract_key_sentences

enc = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(enc.encode(text))

def parse_wiki_note(path: str) -> dict:
    """Parse an Obsidian markdown note into structured fields."""
    content = Path(path).read_text(encoding='utf-8')

    # Extract frontmatter
    frontmatter = {}
    body = content
    if content.startswith('---'):
        end = content.find('---', 3)
        if end > 0:
            fm_text = content[3:end]
            body = content[end+3:].strip()  # strip() removes leading newlines
            for line in fm_text.strip().split('\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    frontmatter[k.strip()] = v.strip()

    # H1 must come from body only (after frontmatter stripped)
    title_match = re.search(r'^#\s+(.+)', body, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else Path(path).stem
    # Final safety: strip any remaining YAML-like content from title
    if ':' in title and len(title) > 60:
        title = Path(path).stem  # fallback to filename

    # Strip markdown formatting for NER
    plain = re.sub(r'[#*`\[\]()>]', '', body)
    plain = re.sub(r'\n+', ' ', plain).strip()

    return {
        'path': path,
        'slug': Path(path).stem,
        'title': title,
        'frontmatter': frontmatter,
        'body': body,
        'plain': plain,
        'raw_tokens': count_tokens(content),
    }

def encode_to_skeleton(note: dict) -> str:
    """
    Encode a parsed wiki note to AAAK skeleton (single line).
    Format: [SLUG|TITLE] ENT:e1,e2 KEY:s1|s2 TAG:t1,t2
    """
    slug = note['slug'][:20]
    title = note['title'][:40]

    # Content-proportional extraction (scales with input size)
    raw_tokens = note['raw_tokens']
    n_entities = max(3, min(10, raw_tokens // 300))
    n_sentences = max(2, min(10, raw_tokens // 200))

    entities = extract_entities(note['plain'])
    priority = [e for e in entities if e['category'] in ('person', 'org')][:n_entities//2+1]
    other = [e for e in entities if e['category'] not in ('person', 'org')][:n_entities//2]
    top_entities = [e['text'] for e in (priority + other)]

    key_sentences = extract_key_sentences(note['plain'], n=n_sentences)
    key_sentences = [s[:80] for s in key_sentences]

    # Tags from frontmatter
    tags = note['frontmatter'].get('tags', '').replace(' ', '').split(',')
    tags = [t for t in tags if t][:3]

    parts = [f"[{slug}|{title}]"]
    if top_entities:
        parts.append(f"ENT:{','.join(top_entities)}")
    if key_sentences:
        parts.append(f"KEY:{'|'.join(key_sentences)}")
    if tags:
        parts.append(f"TAG:{','.join(tags)}")

    return ' '.join(parts)

def encode_note(path: str) -> dict:
    """Full encode pipeline: path -> skeleton with metrics."""
    note = parse_wiki_note(path)
    skeleton = encode_to_skeleton(note)
    skeleton_tokens = count_tokens(skeleton)
    ratio = skeleton_tokens / note['raw_tokens'] if note['raw_tokens'] > 0 else 1.0

    return {
        'slug': note['slug'],
        'skeleton': skeleton,
        'raw_tokens': note['raw_tokens'],
        'skeleton_tokens': skeleton_tokens,
        'ratio': round(ratio, 4),
        'saving_pct': round((1 - ratio) * 100, 1),
    }
