# MemOcean MCP

> A memory system built for CJK developers.

Most AI memory frameworks are designed for English: whitespace tokenization, uppercase acronyms as entities, LIKE string matching — none of these work in Chinese. MemOcean forks MemPalace's sonar-memory architecture (MemPalace 稱為 Skeleton) and rewrites the full NER + search pipeline for Chinese (Traditional, Simplified, mixed CJK-English), so your agents can actually remember and retrieve things in Chinese conversations.

**Core capabilities:**
- FTS5 + BM25 + Haiku reranker hybrid search — 87.5% Hit@5 on Chinese
- CLSC semantic sonar extraction — 87% token reduction, semantic links preserved
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

### `memocean_seabed_get`
Retrieve knowledge content (Radar/Seabed) by slug.

```json
{
  "slug": "channellab-pricing",
  "mode": "verbatim"
}
```
Returns: `{ "slug": "channellab-pricing", "mode": "verbatim", "content": "# ChannelLab GEO pricing..." }`

### `memocean_seabed_search`
Search Radar (CLSC sonar index) using multi-term AND matching.

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

### `memocean_ingest_file`
Ingest a local file (PDF, PPT, Word, Excel, HTML, CSV, JSON) into MemOcean's Radar seabed. Converts to markdown via [MarkItDown](https://github.com/microsoft/markitdown), stores in `group='files'`. Deduplicates by file path — re-ingesting the same path updates both the DB row and the `.clsc.md` sonar file.

```json
{
  "file_path": "/home/user/Documents/report.pdf"
}
```
Returns (success): `{ "slug": "file:report-a3f9c2", "group": "files", "chars": 12500, "radar_id": 341, "format": "pdf", "truncated": false }`

Returns (error): `{ "error": "File not found: /path/to/file", "code": "FILE_NOT_FOUND" }`

**Error codes:**
| Code | Meaning |
|---|---|
| `FILE_NOT_FOUND` | Path does not exist |
| `FILE_TOO_LARGE` | File exceeds 50 MB |
| `MARKITDOWN_FAIL` | MarkItDown conversion raised an exception |
| `EMPTY_CONTENT` | Converted content is under 100 characters |

**Supported formats:** PDF, PPTX, DOCX, XLSX, HTML, CSV, JSON (and any format MarkItDown supports).

**Requires:** `markitdown[all]` installed in the active Python environment (`pip install "markitdown[all]"`).

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
| `seabed/` | Radar bundle storage (sonar index) |
| `shared/learned-skills/approved/` | Approved skill markdown files |
| `shared/fts5/` | FTS5 search module |
| `shared/clsc/v0.7/` | Radar decoder module (CLSC sonar) |
| `shared/kg/` | KG helper module |

## Tide（潮汐文件）

Tide 是 MemOcean 的第三層輸出格式。資料流向：
- Seabed 存原始素材（訊息、對話）
- Radar 做壓縮索引（CLSC sonar）
- **Tide** 是有時間維度的整合文件，每份 TideDoc 包含：
  - 上層 Compiled Truth：當前最佳理解，可覆寫，標記更新日期
  - 下層 Timeline：append-only 事件紀錄，只增不改

## Recent updates

### 2026-04-12
- **`memocean_ingest_file` (Phase 1)**: new MCP tool to ingest local files into MemOcean Radar. Converts PDF/PPT/Word/Excel/HTML/CSV/JSON to markdown via MarkItDown, stores in `group='files'` radar seabed. Deduplicates by file path — re-ingest updates both DB row and `.clsc.md` sonar. Slug format: `file:{stem}-{hash6}` (last 6 hex chars of MD5 of abs path — stable across days). Truncates at 50 k chars with `truncated: true` flag. Requires `markitdown[all]` in environment.
- **Closet → Radar rename sweep**: all internal references (`closet_fts` → `radar_fts`, `closet_vec` → `radar_vec`, SQL tables, variable names, shell vars) updated across `shared/clsc/`, `shared/fts5/`, `shared/scripts/`, and `memocean_mcp/`.

### 2026-04-11
- **Removed `memocean_ask_opus`**: replaced by native `Agent` tool with `model: "opus"` in Claude Code — more direct, fewer tokens
- **Terminology fix**: CLSC is "sonar extraction" not "compression" — lossy and irreversible by design
- **Dream Cycle (Phase 1 shipped)**: nightly knowledge consolidation pipeline (`shared/scripts/dream_cycle.py`). 6-step pipeline: Collect → Extract → Normalize → Diff → Write → Report. Features: lock file, 30-min timeout, crash-recovery checkpoint, content-hash idempotency, dry-run/live modes, TG notification, graceful LLM degradation. Runs daily at 19:00 UTC via system crontab (`shared/scripts/install_cron.sh`). 39 tests passing.
- **Radar FTS sync fix**: `store_sonar()` now syncs to `memory.db` radar table + `radar_fts` (with `source_hash`, DELETE-before-INSERT on FTS). Applied to both `shared/clsc/v0.7/radar.py` and `shared/memocean-mcp/clsc/radar.py`.
- **Alias table**: `shared/config/alias_table.yaml` — 19-entity alias table for entity normalization in Dream Cycle.
