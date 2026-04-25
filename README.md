# memocean-mcp — Local-first AI Memory MCP Server

![MemOcean](assets/memocean-banner.jpg)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

A local MCP server for AI agents to search and manage knowledge stored in Obsidian vaults.
Built for CJK (Chinese/Japanese/Korean) developers — 94.4% Hit@5 on Chinese queries with zero AI components required.

**Core features:**
- BM25/INSTR hybrid search — CJK-optimized, pure SQLite, no embeddings needed
- CLSC sonar compression — 92.5% token reduction (13x compression) on Obsidian notes
- Temporal knowledge graph — entity-relationship store with non-destructive invalidation
- Cross-agent memory sharing — multiple agents share one `memory.db`
- FATQ task queue — File-Atomic Task Queue for agent coordination

---

## Quick Start

```bash
pip install memocean-mcp
```

Add to Claude Desktop / Claude Code `.mcp.json`:

```json
{
  "mcpServers": {
    "memocean": {
      "command": "memocean-mcp",
      "env": {
        "MEMOCEAN_VAULT_ROOT": "/path/to/your/obsidian/vault"
      }
    }
  }
}
```

Or register with Claude Code CLI:

```bash
MEMOCEAN_VAULT_ROOT=/path/to/vault claude mcp add memocean memocean-mcp
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MEMOCEAN_VAULT_ROOT` | `~/Documents/Obsidian Vault` | Root of your Obsidian vault |
| `MEMOCEAN_DATA_DIR` | `~/.memocean` | Data directory (databases, task queue) |
| `MEMOCEAN_VAULT_PATH` | `MEMOCEAN_VAULT_ROOT/Ocean` | Ocean subdirectory for full-text search |
| `MEMOCEAN_SKILLS_DIR` | `MEMOCEAN_VAULT_ROOT/Ocean/Pearl/skills` | Skills markdown directory |
| `MEMOCEAN_USE_GBRAIN` | `false` | Enable GBrain hybrid search delegate |
| `KNN_ENABLED` | `false` | Enable BGE-m3 KNN vector search |
| `ENABLE_QUERY_EXPANSION` | unset | Enable Haiku query expansion (requires `ANTHROPIC_API_KEY`) |
| `ENABLE_HAIKU_RERANKER` | unset | Enable Haiku LLM reranker |
| `ANTHROPIC_API_KEY` | unset | Required only for AI-assisted features above |

Backward-compat: `CHANNELLAB_BOTS_ROOT` → `MEMOCEAN_DATA_DIR`, `CHANNELLAB_OCEAN_VAULT_ROOT` → `MEMOCEAN_VAULT_ROOT`.

---

## Available Tools

| Tool | Description |
|---|---|
| `memocean_search` | Unified search across Radar (sonar index) + message history. Default entry point. |
| `memocean_radar_search` | Search CLSC sonar index — fast keyword search, ~13% of verbatim token cost. |
| `memocean_seabed_get` | Retrieve full content by slug (verbatim or sonar mode). |
| `memocean_ocean_search` | Full-text search over Ocean vault `.md` files via ripgrep. |
| `memocean_messages_search` | BM25 search over cross-agent message history. |
| `memocean_kg_query` | Query the temporal knowledge graph by entity name. |
| `memocean_skill_list` | List or retrieve approved skills from the skill library. |
| `memocean_task_create` | Create a task in the FATQ pending queue (agent coordination). |
| `memocean_ingest_file` | Ingest local file (PDF/DOCX/XLSX/HTML/CSV/JSON) into Radar via MarkItDown. |
| `memocean_report_store` | Store a verbatim markdown report into Ocean vault Reports folder. |

---

## Search Architecture

Two-path retrieval, zero AI dependency by default:

```
CJK query  →  SQLite INSTR on radar.clsc  →  ranked by match_count
EN query   →  FTS5 BM25                   →  fallback to INSTR on miss
```

Benchmark (pure BM25/INSTR, no AI):

| Dataset | Language | Hit@5 |
|---|---|---|
| Internal corpus | Chinese (mixed) | **94.4%** |
| DRCD | Traditional Chinese | **91.9%** |
| CMRC | Simplified Chinese | **93.3%** |
| BEIR SciFact | English | 70.7% |

---

## CLSC Sonar Compression

CLSC (Closet Lossy Summary for Chinese) extracts each document into a compact single-line sonar entry. Format:

```
[SLUG|ENTITIES|topics|"key_quote"|WEIGHT|EMOTIONS|FLAGS]
```

Compression ratio: **1,716,211 raw tokens → 129,529 sonar tokens = 13x (92.5% reduction)**.

---

## Requirements

- Python 3.11+
- SQLite 3.35+
- Optional: `markitdown[all]` for file ingestion
- Optional: `anthropic` package for AI-assisted features (query expansion, reranking)

---

## License

MIT — see [LICENSE](LICENSE).

---

## Acknowledgements

Built on [MemPalace](https://github.com/milla-jovovich/mempalace) (dual-layer architecture, AAAK skeleton format) and inspired by [GBrain](https://github.com/garrytan/gbrain) (Compiled Truth + Dream Cycle design).
