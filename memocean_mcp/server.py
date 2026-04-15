"""
server.py — MemOcean MCP Server (library mode, local only).

Registered tools:
  memocean_search          — Ocean-First unified search (ocean → radar → messages)  [MEMO-010]
  memocean_messages_search — BM25 FTS5 cross-bot message search
  memocean_seabed_get      — Seabed content retrieval (verbatim/sonar)
  memocean_seabed_search   — Multi-term AND search over CLSC sonar seabed
  memocean_ocean_search    — Full-text search over Ocean vault .md files
  memocean_kg_query      — Temporal knowledge graph query
  memocean_skill_list    — List/get approved learned skills
  memocean_task_create   — Create task in pending queue
  memocean_ingest_file   — Ingest local file (PDF/PPT/Word/Excel/HTML/CSV/JSON) into MemOcean
Run via:
  python -m memocean_mcp
  memocean-mcp
"""
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger("memocean_mcp")

# ==================== TOOL HANDLERS ====================


def tool_unified_search(query: str, source: str = "all", limit: int = 10):
    """Ocean-First unified search: ocean → radar → messages (opt-in)."""
    try:
        from .tools.unified_search import memocean_search
        results = memocean_search(query, source=source, limit=limit)
        return {"query": query, "source": source, "count": len(results), "results": results}
    except Exception as e:
        return {"error": f"Search failed: {e}", "results": []}


def tool_fts_search(query: str, limit: int = 10, bot: str = None):
    """Hybrid BM25 + BGE-m3 KNN search over cross-bot message history."""
    try:
        from .tools.messages_hybrid_search import messages_hybrid_search
        results = messages_hybrid_search(query, limit=limit, bot=bot or None)
        return {"query": query, "count": len(results), "results": results}
    except FileNotFoundError as e:
        return {"error": str(e), "results": []}
    except Exception as e:
        return {"error": f"Search failed: {e}", "results": []}


def tool_radar_get(slug: str, mode: str = "verbatim"):
    """Retrieve radar (Seabed) content by slug."""
    try:
        from .tools.radar_get import radar_get
        content = radar_get(slug, mode=mode)
        return {"slug": slug, "mode": mode, "content": content}
    except Exception as e:
        return {"error": str(e)}


def tool_radar_search(query: str, limit: int = 5):
    """Search Radar (CLSC sonar index) via multi-term AND query."""
    try:
        from .tools.radar_search import radar_search
        results = radar_search(query, limit=limit)
        return {"query": query, "count": len(results), "results": results}
    except Exception as e:
        return {"error": str(e)}


def tool_ocean_search(query: str, limit: int = 10):
    """Full-text search over Ocean vault .md files via ripgrep."""
    try:
        from .tools.ocean_search import ocean_search
        results = ocean_search(query, limit=limit)
        return {"query": query, "count": len(results), "results": results}
    except Exception as e:
        return {"error": f"Ocean search failed: {e}", "results": []}


def tool_kg_query(entity: str, as_of: str = None, direction: str = "outgoing"):
    """Query the temporal knowledge graph."""
    try:
        from .tools.kg_query import kg_query
        facts = kg_query(entity, as_of=as_of, direction=direction)
        return {"entity": entity, "as_of": as_of, "direction": direction,
                "count": len(facts), "facts": facts}
    except FileNotFoundError as e:
        return {"error": str(e), "facts": []}
    except Exception as e:
        return {"error": f"KG query failed: {e}", "facts": []}


def tool_skill_list(name: str = None):
    """List all approved skills, or get content of a specific skill."""
    try:
        from .tools.skill_tools import skill_list, skill_get
        if name:
            content = skill_get(name)
            return {"name": name, "content": content}
        else:
            skills = skill_list()
            return {"count": len(skills), "skills": skills}
    except Exception as e:
        return {"error": str(e)}


def tool_ingest_file(file_path: str):
    """Ingest a local file into MemOcean via MarkItDown."""
    try:
        from .tools.ingest_file import ingest_file
        return ingest_file(file_path)
    except Exception as e:
        return {"error": f"Ingest failed: {e}", "code": "MARKITDOWN_FAIL"}


def tool_task_create(
    title: str,
    description: str,
    assigned_to: str,
    assigned_by: str = "mcp",
    priority: str = "medium",
    acceptance_criteria: list = None,
):
    """Create a task JSON in the pending queue."""
    try:
        from .tools.task_create import task_create
        return task_create(
            title=title,
            description=description,
            assigned_to=assigned_to,
            assigned_by=assigned_by,
            priority=priority,
            acceptance_criteria=acceptance_criteria,
        )
    except Exception as e:
        return {"error": f"Task creation failed: {e}"}


# ==================== MCP PROTOCOL ====================

TOOLS = {
    "memocean_search": {
        "description": (
            "Ocean-First unified search across ChannelLab knowledge layers. "
            "Searches Ocean vault .md files first (wiki pages, Pearl cards, Research notes, specs), "
            "then Radar sonar index (CLSC compressed summaries), then Message history (opt-in). "
            "Results ranked by source priority: ocean > radar > messages. "
            "Use the `source` param to restrict to a specific layer: "
            "'ocean' (vault only), 'radar' (sonar only), 'messages' (history only), 'all' (default). "
            "AC1: memocean_search('CHL 現在在推什麼') returns Ocean page results before SQLite messages. "
            "No BGE-m3/KNN — pure FTS + Haiku query expansion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language or keyword query. E.g. 'CHL 現在在推什麼', 'MemOcean 架構'",
                },
                "source": {
                    "type": "string",
                    "description": "Which layer to search: 'all' (default), 'ocean', 'radar', or 'messages'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 10)",
                },
            },
            "required": ["query"],
        },
        "handler": tool_unified_search,
    },
    "memocean_messages_search": {
        "description": (
            "BM25 full-text search over ChannelLab cross-bot Telegram message history. "
            "Uses FTS5 BM25 with Haiku query expansion. "
            "Supports natural-language queries ('上次那個 OTC 討論'), boolean operators (AND/OR/NOT), "
            "phrase search (\"quoted\"), and NEAR proximity. "
            "KNN vector search disabled by default (set KNN_ENABLED=true to re-enable BGE-m3)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "FTS5 query string. E.g. 'ProjectA OR ProjectB', '\"deploy 重啟\"', 'NEAR(AgentA AgentB, 5)'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 10)",
                },
                "bot": {
                    "type": "string",
                    "description": "Filter by bot name (e.g. 'builder', 'reviewer'). Optional.",
                },
            },
            "required": ["query"],
        },
        "handler": tool_fts_search,
    },
    "memocean_seabed_get": {
        "description": (
            "Retrieve content from MemOcean's seabed knowledge store by slug. "
            "mode='verbatim' returns the original drawer content (Obsidian Wiki or fallback). "
            "mode='sonar' returns raw radar bundle sonar index."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Content slug to retrieve (e.g. 'nox-staking', 'channellab-pricing')",
                },
                "mode": {
                    "type": "string",
                    "description": "'verbatim' (default) or 'sonar'",
                },
            },
            "required": ["slug"],
        },
        "handler": tool_radar_get,
    },
    "memocean_seabed_search": {
        "description": (
            "Search MemOcean's Radar (CLSC sonar index) using multi-term AND matching. "
            "Splits query on whitespace; all terms must appear in the radar sonar index. "
            "Handles hyphenated slugs that exact-phrase LIKE would miss. "
            "Returns compact CLSC sonar entries (~13% of verbatim token count)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms (space-separated AND). E.g. 'Knowledge Infra', 'ChannelLab GEO'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 5)",
                },
            },
            "required": ["query"],
        },
        "handler": tool_radar_search,
    },
    "memocean_ocean_search": {
        "description": (
            "Full-text search over ChannelLab Ocean vault .md files using ripgrep. "
            "Searches wiki pages, Pearl cards, Research notes, specs, and all Obsidian docs "
            "under Ocean/ (excludes personal vaults). "
            "Returns title, [[wikilink]], excerpt (~200 chars), and relative path. "
            "Falls back to [] when Ocean vault not found or rg unavailable."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language or keyword query. E.g. 'ChannelLab GEO 服務', 'MemOcean 架構'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 10)",
                },
            },
            "required": ["query"],
        },
        "handler": tool_ocean_search,
    },
    "memocean_kg_query": {
        "description": (
            "Query the ChannelLab temporal knowledge graph. Returns typed facts with "
            "time validity windows. Filter by date to see what was true at any point in time. "
            "Example: entity='owner' → role=CEO, direction='both' shows all relationships."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity to query (e.g. 'ProjectName', 'agent-name', 'ChannelLab')",
                },
                "as_of": {
                    "type": "string",
                    "description": "Date filter YYYY-MM-DD — facts valid at this date (default: today)",
                },
                "direction": {
                    "type": "string",
                    "description": "'outgoing' (default), 'incoming', or 'both'",
                },
            },
            "required": ["entity"],
        },
        "handler": tool_kg_query,
    },
    "memocean_skill_list": {
        "description": (
            "List or retrieve approved learned skills from the team skill library. "
            "Without a name, returns all skill names. "
            "With a name, returns full markdown content of that skill."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name to retrieve (optional). Omit to list all skills.",
                },
            },
        },
        "handler": tool_skill_list,
    },
    "memocean_ingest_file": {
        "description": (
            "Ingest a local file (PDF, PPT, Word, Excel, HTML, CSV, JSON) into MemOcean. "
            "Converts to markdown via MarkItDown, stores in radar group='files'. "
            "Returns slug, chars, radar_id, format, truncated. "
            "Errors: FILE_NOT_FOUND | FILE_TOO_LARGE | MARKITDOWN_FAIL | EMPTY_CONTENT"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path (or ~-expanded) to the local file to ingest.",
                },
            },
            "required": ["file_path"],
        },
        "handler": tool_ingest_file,
    },
    "memocean_task_create": {
        "description": (
            "Create a new task in the ChannelLab FATQ (pending/). "
            "Assigns to builder, reviewer, or assistant. Returns task_id and file path. "
            "The task will be picked up by the assigned bot on their next startup scan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short task title (used in filename slug)",
                },
                "description": {
                    "type": "string",
                    "description": "Full task specification and requirements",
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Bot to assign to: 'builder', 'reviewer', or 'assistant'",
                },
                "assigned_by": {
                    "type": "string",
                    "description": "Who is creating this task (default: 'mcp')",
                },
                "priority": {
                    "type": "string",
                    "description": "'low', 'medium' (default), 'high', or 'urgent'",
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of acceptance criteria strings (optional)",
                },
            },
            "required": ["title", "description", "assigned_to"],
        },
        "handler": tool_task_create,
    },
}


# ==================== REQUEST DISPATCH ====================


def handle_request(request: dict):
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "memocean-mcp", "version": "0.1.0"},
            },
        }

    elif method == "notifications/initialized":
        return None

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": name,
                        "description": spec["description"],
                        "inputSchema": spec["input_schema"],
                    }
                    for name, spec in TOOLS.items()
                ]
            },
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        # Coerce integer/number types from JSON transport
        schema_props = TOOLS[tool_name]["input_schema"].get("properties", {})
        for key, value in list(tool_args.items()):
            declared_type = schema_props.get(key, {}).get("type")
            if declared_type == "integer" and not isinstance(value, int):
                tool_args[key] = int(value)
            elif declared_type == "number" and not isinstance(value, (int, float)):
                tool_args[key] = float(value)

        try:
            result = TOOLS[tool_name]["handler"](**tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
                },
            }
        except Exception:
            logger.exception(f"Tool error in {tool_name}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": "Internal tool error"},
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


# ==================== ENTRY POINT ====================


# Module-level alias so `from memocean_mcp.server import mcp` works.
# This server uses low-level JSON-RPC (like the mempalace reference), not FastMCP.
# `mcp` here is a reference to the TOOLS registry — introspectable by callers.
mcp = TOOLS


def main():
    logger.info("MemOcean MCP Server starting (library mode)...")
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Server error: {e}")


if __name__ == "__main__":
    main()
