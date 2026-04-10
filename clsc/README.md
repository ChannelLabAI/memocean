# CLSC v0.7 — HanCloset

Extends v0.6 with LLM narrative expand, H1 title fix, auto-sync hook, fine-grained grouping, and FTS5 bridge.

## What's new in v0.7

| Feature | Description |
|---|---|
| LLM narrative expand | `narrative_expand()` auto-detects `ANTHROPIC_API_KEY`; falls back to template mock |
| H1 title fix | Frontmatter no longer bleeds into extracted title |
| clsc-sync.sh | Hook to re-encode closet when wiki note is saved |
| `group_from_path()` | Auto-detect closet group from wiki subdirectory |
| FTS5 bridge | `fts5_bridge.search_fts5()` searches both memory.db and closet files |

## Quick start

```bash
cd ~/.claude-bots/shared/clsc/v0.7
python3 tests/test_v0_7.py

# Sync a wiki note to closet
~/.claude-bots/shared/hooks/clsc-sync.sh "/path/to/Ocean/Research/note.md"

# Watch mode (requires inotify-tools)
~/.claude-bots/shared/hooks/clsc-sync.sh --watch
```

## API

```python
from hancloset import get_wiki_summary, encode_and_store
from decoder import narrative_expand
from closet import group_from_path
from fts5_bridge import search_fts5

# Auto-encode and store
encode_and_store("/path/to/note.md", group="research")

# Get summary
summary = get_wiki_summary("CZ-Memoir-Personal-Story", mode="narrative", group="research")

# Search
results = search_fts5("趙長鵬")
```

## Notes

- `narrative_expand(use_llm=None)`: auto mode — uses LLM if `ANTHROPIC_API_KEY` is set and non-placeholder
- `--watch` mode needs `sudo apt install inotify-tools`; single-file mode has no extra deps
- Do NOT touch v0.6 files
