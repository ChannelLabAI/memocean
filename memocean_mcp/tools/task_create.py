"""
task_create.py — Create a new task JSON file in the pending queue.
File schema and naming follow the ChannelLab FATQ（File-Atomic Task Queue）protocol.
"""
import json
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from ..config import TASKS_ROOT, SHARED_ROOT

PriorityT = Literal["low", "medium", "high", "urgent"]

_TEAM_CONFIG_PATH = SHARED_ROOT / "team-config.json"


@lru_cache(maxsize=1)
def _load_valid_assignees() -> frozenset:
    """
    Load valid assignee state_dirs from team-config.json.
    Falls back to a minimal hardcoded set if the file is missing.
    Cached after first load (restart process to refresh).
    """
    try:
        cfg = json.loads(_TEAM_CONFIG_PATH.read_text(encoding="utf-8"))
        names: set = set()
        # Assistants
        for a in cfg.get("assistants", []):
            if isinstance(a, dict) and a.get("state_dir"):
                names.add(a["state_dir"].lower())
        # Shared pools (builder, reviewer, designer)
        for pool in cfg.get("shared_pools", {}).values():
            if isinstance(pool, list):
                for m in pool:
                    if isinstance(m, dict) and m.get("state_dir"):
                        names.add(m["state_dir"].lower())
        if names:
            return frozenset(names)
    except Exception:
        pass
    # Fallback
    return frozenset({"builder", "reviewer", "assistant"})


def task_create(
    title: str,
    description: str,
    assigned_to: str,
    assigned_by: str = "mcp",
    priority: PriorityT = "medium",
    acceptance_criteria: Optional[list[str]] = None,
) -> dict:
    """
    Create a task JSON in TASKS_ROOT/pending/.

    Returns dict with keys: task_id, file_path, filename, status.
    """
    _VALID_ASSIGNEES = _load_valid_assignees()
    _VALID_PRIORITIES = {"low", "medium", "high", "urgent"}
    if assigned_to.lower() not in _VALID_ASSIGNEES:
        raise ValueError(f"assigned_to '{assigned_to}' not in team-config known bots: {sorted(_VALID_ASSIGNEES)}")
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"priority must be one of {_VALID_PRIORITIES}, got '{priority}'")

    pending_dir = TASKS_ROOT / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(tz=timezone.utc)
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    hex4 = secrets.token_hex(2)  # 4 hex chars

    # Build slug from title: lowercase, spaces to hyphens, max 40 chars
    slug = (
        title.lower()
        .replace(" ", "-")
        .replace("/", "-")
        .replace("_", "-")
    )
    # Keep only alphanumeric + hyphens
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    slug = slug[:40].strip("-")

    filename = f"{timestamp}-{hex4}-{slug}.json"
    task_id = f"{timestamp}-{hex4}"

    task = {
        "task_id": task_id,
        "title": title,
        "assigned_to": assigned_to,
        "assigned_by": assigned_by,
        "status": "pending",
        "priority": priority,
        "created_at": now.isoformat(),
        "spec": {
            "description": description,
            "acceptance_criteria": acceptance_criteria or [],
        },
        "history": [
            {
                "status": "pending",
                "at": now.isoformat(),
                "by": assigned_by,
                "note": "Task created via memocean_task_create MCP tool",
            }
        ],
        "flow": "pending → in_progress → review → done",
    }

    file_path = pending_dir / filename
    tmp_path = pending_dir / (filename + ".tmp")

    # Atomic write: write to .tmp then rename
    tmp_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.rename(file_path)

    return {
        "task_id": task_id,
        "filename": filename,
        "file_path": str(file_path),
        "status": "pending",
        "assigned_to": assigned_to,
        "title": title,
    }
