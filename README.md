# MemOcean MCP

> A memory system built for CJK developers.

Most AI memory frameworks are designed for English: whitespace tokenization, uppercase acronyms as entities, LIKE string matching — none of these work in Chinese. MemOcean forks MemPalace's skeleton-memory architecture and rewrites the full NER + search pipeline for Chinese (Traditional, Simplified, mixed CJK-English), so your agents can actually remember and retrieve things in Chinese conversations.

**Core capabilities:**
- FTS5 + BM25 + Haiku reranker hybrid search — 87.5% Hit@5 on Chinese
- CLSC semantic skeleton extraction — 87% token reduction, semantic links preserved
- Temporal knowledge graph with non-destructive invalidation
- Cross-bot memory sharing via a single shared `memory.db`

## Install

```bash
pip install -e ~/.claude-bots/shared/memocean-mcp
claude mcp add memocean python -m memocean_mcp
```

## How it compares

| | [MemPalace](https://github.com/milla-jovovich/mempalace) | [GBrain](https://github.com/garrytan/gbrain) | MemOcean |
|---|---|---|---|
| CJK-first design | ❌ | ❌ | ✅ |
| Search architecture | BM25 + LIKE | Vector search | FTS5 + BM25 + Haiku reranker |
| Chinese Hit@5 | ~60% (est.) | ~75% (est.) | **87.5%** |
| Knowledge graph | ❌ | ✅ | ✅ temporal |
| Nightly consolidation | ❌ | ✅ Dream Cycle | ✅ Dream Cycle |
| Multi-bot sharing | ❌ | ❌ | ✅ |
| MCP integration | ❌ | ❌ | ✅ |

## Tools

### `memocean_fts_search`
Full-text search over cross-bot Telegram message history.

```json
{
  "query": "NOX OR Bonk",
  "limit": 5
}
```
Returns: `{ "query": "...", "count": 3, "results": [{ "bot_name": "anna", "ts": "...", "snippet": "...", "rank": -0.4 }, ...] }`

### `memocean_closet_get`
Retrieve knowledge content by slug.

```json
{
  "slug": "channellab-pricing",
  "mode": "verbatim"
}
```
Returns: `{ "slug": "channellab-pricing", "mode": "verbatim", "content": "# ChannelLab GEO pricing..." }`

### `memocean_closet_search`
Search CLSC skeleton closet using multi-term AND matching.

```json
{
  "query": "Knowledge Infra",
  "limit": 5
}
```
Returns: `{ "query": "...", "count": 2, "results": [{ "slug": "...", "clsc": "...", "tokens": 42 }, ...] }`

### `memocean_kg_query`
Query the temporal knowledge graph.

```json
{
  "entity": "老兔",
  "direction": "both"
}
```
Returns: `{ "entity": "老兔", "count": 4, "facts": [{ "subject": "老兔", "predicate": "role", "object": "CEO", ... }] }`

### `memocean_skill_list`
List approved skills, or get a specific skill's content.

```json
{}
```
Returns: `{ "count": 2, "skills": ["parallel-builder-reviewer-pools", "tg-supergroup-id-migration"] }`

```json
{ "name": "parallel-builder-reviewer-pools" }
```
Returns: `{ "name": "parallel-builder-reviewer-pools", "content": "# Parallel Builder..." }`

### `memocean_task_create`
Create a new task in the pending queue.

```json
{
  "title": "Build NOX staking dashboard",
  "description": "Create a dashboard showing real-time NOX staking stats.",
  "assigned_to": "anna",
  "priority": "high",
  "acceptance_criteria": ["Shows APY", "Mobile responsive"]
}
```
Returns: `{ "task_id": "20260408-120000-a1b2", "filename": "...", "file_path": "...", "status": "pending" }`

## Security model

**Data locality.** Your data never leaves your machine. The MCP server runs as a local subprocess over stdio — no outbound network connections, no telemetry, no cloud sync. The code is open source; your data is not.

**Trust boundary.** This library assumes the caller (Claude Code / your local Claude session) is trusted. Tool inputs such as `slug` and `skill name` may originate from LLM-generated content or indexed external data. To prevent prompt-injection-driven path traversal, all slug/name parameters are validated against `[A-Za-z0-9_-]{1,100}` before any filesystem access. `task_create` validates `assigned_to` and `priority` at runtime against allowlists.

**Install isolation.** We recommend installing inside a dedicated virtual environment to avoid PEP 668 conflicts with system Python packages:

```bash
python3 -m venv ~/.venvs/memocean-mcp
source ~/.venvs/memocean-mcp/bin/activate
pip install -e /path/to/memocean-mcp
```

Then register with the venv Python:

```bash
claude mcp add memocean ~/.venvs/memocean-mcp/bin/python -m memocean_mcp
```

**What this library does NOT do:** multi-tenant isolation, authentication, rate limiting, or network access. It is designed for single-user local use only.

## Configuration

Environment variable overrides (all optional):

| Variable | Default | Description |
|---|---|---|
| `CHANNELLAB_BOTS_ROOT` | `~/.claude-bots` | Root of the bots directory |

Derived paths (all under `BOTS_ROOT`):

| Path | Description |
|---|---|
| `memory.db` | FTS5 SQLite database |
| `kg.db` | Temporal knowledge graph SQLite |
| `tasks/` | Task queue directories |
| `closet/` | Closet bundle storage |
| `shared/learned-skills/approved/` | Approved skill markdown files |
| `shared/fts5/` | FTS5 search module |
| `shared/clsc/v0.7/` | Closet decoder module |
| `shared/kg/` | KG helper module |

## Recent updates

### 2026-04-11
- **Removed `memocean_ask_opus`**: replaced by native `Agent` tool with `model: "opus"` in Claude Code — more direct, fewer tokens
- **Terminology fix**: CLSC is "skeleton extraction" not "compression" — lossy and irreversible by design
- **Dream Cycle (Phase 1 shipped)**: nightly knowledge consolidation pipeline (`shared/scripts/dream_cycle.py`). 6-step pipeline: Collect → Extract → Normalize → Diff → Write → Report. Features: lock file, 30-min timeout, crash-recovery checkpoint, content-hash idempotency, dry-run/live modes, TG notification, graceful LLM degradation. Runs daily at 19:00 UTC via system crontab (`shared/scripts/install_cron.sh`). 39 tests passing.
- **`closet.py` FTS sync fix**: `store_skeleton()` now syncs to `memory.db` closet table + `closet_fts` (with `source_hash`, DELETE-before-INSERT on FTS). Applied to both `shared/clsc/v0.7/closet.py` and `shared/memocean-mcp/clsc/closet.py`.
- **Alias table**: `shared/config/alias_table.yaml` — 19-entity alias table for entity normalization in Dream Cycle.
