"""
config.py — paths and settings, read from env or defaults.
"""
import os
from pathlib import Path

HOME = Path.home()
BOTS_ROOT = Path(os.environ.get("CHANNELLAB_BOTS_ROOT", str(HOME / ".claude-bots")))

FTS_DB = BOTS_ROOT / "memory.db"
KG_DB = BOTS_ROOT / "kg.db"
TASKS_ROOT = BOTS_ROOT / "tasks"
CLOSET_ROOT = BOTS_ROOT / "seabed"
LEARNED_SKILLS_DIR = BOTS_ROOT / "shared" / "learned-skills" / "approved"
SHARED_ROOT = BOTS_ROOT / "shared"
