# memocean-mcp

MemOcean MCP server — a local-only library-mode server that wraps FTS5 message search, closet content retrieval, temporal knowledge graph queries, learned skill access, FATQ（File-Atomic Task Queue）task creation, and Opus advisor into a single Claude Code MCP integration.

## Install

```bash
pip install -e ~/.claude-bots/shared/memocean-mcp
claude mcp add memocean python -m memocean_mcp
```

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

### `memocean_ask_opus`
Ask Claude Opus for high-level business judgment or strategic advice.

```json
{
  "question": "Should we prioritize the GEO service or the NOX staking dashboard?",
  "context": "Current sprint has 3 days left. GEO has a client waiting. NOX has no deadline.",
  "max_tokens": 1000
}
```
Returns: `{ "question": "...", "response": "..." }`

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
