"""
config.py — paths and settings, read from env or defaults.

Primary env vars:
  MEMOCEAN_DATA_DIR       — root data directory (default: ~/.memocean)
  MEMOCEAN_VAULT_ROOT     — Obsidian vault root (default: ~/Documents/Obsidian Vault)
  MEMOCEAN_VAULT_PATH     — Ocean subdirectory (default: MEMOCEAN_VAULT_ROOT/Ocean)
  MEMOCEAN_SKILLS_DIR     — Skills directory (default: MEMOCEAN_VAULT_ROOT/Ocean/Pearl/skills)

Legacy env vars (backward-compat aliases, still supported):
  CHANNELLAB_BOTS_ROOT         → MEMOCEAN_DATA_DIR
  CHANNELLAB_OCEAN_VAULT_ROOT  → MEMOCEAN_VAULT_ROOT
"""
import os
from pathlib import Path

HOME = Path.home()

# ── Primary: MEMOCEAN_DATA_DIR ──────────────────────────────────────────────
# Resolution order: MEMOCEAN_DATA_DIR > CHANNELLAB_BOTS_ROOT > ~/.memocean
def _resolve_data_dir() -> Path:
    if os.environ.get("MEMOCEAN_DATA_DIR"):
        return Path(os.environ["MEMOCEAN_DATA_DIR"])
    if os.environ.get("CHANNELLAB_BOTS_ROOT"):
        return Path(os.environ["CHANNELLAB_BOTS_ROOT"])
    return HOME / ".memocean"

MEMOCEAN_DATA_DIR = _resolve_data_dir()

# ── Primary: MEMOCEAN_VAULT_ROOT ────────────────────────────────────────────
# Resolution order: MEMOCEAN_VAULT_ROOT > CHANNELLAB_OCEAN_VAULT_ROOT > ~/Documents/Obsidian Vault
def _resolve_vault_root() -> Path:
    if os.environ.get("MEMOCEAN_VAULT_ROOT"):
        return Path(os.environ["MEMOCEAN_VAULT_ROOT"])
    if os.environ.get("CHANNELLAB_OCEAN_VAULT_ROOT"):
        return Path(os.environ["CHANNELLAB_OCEAN_VAULT_ROOT"])
    return HOME / "Documents" / "Obsidian Vault"

MEMOCEAN_VAULT_ROOT = _resolve_vault_root()

# ── Ocean vault path (Ocean/ subdir) ────────────────────────────────────────
MEMOCEAN_VAULT_PATH = Path(os.environ.get(
    "MEMOCEAN_VAULT_PATH",
    str(MEMOCEAN_VAULT_ROOT / "Ocean"),
))

# ── Skills directory ─────────────────────────────────────────────────────────
MEMOCEAN_SKILLS_DIR = Path(os.environ.get(
    "MEMOCEAN_SKILLS_DIR",
    str(MEMOCEAN_VAULT_ROOT / "Ocean" / "Pearl" / "skills"),
))

# ── Derived paths (under MEMOCEAN_DATA_DIR) ──────────────────────────────────
FTS_DB = MEMOCEAN_DATA_DIR / "memory.db"
KG_DB = MEMOCEAN_DATA_DIR / "kg.db"
TASKS_ROOT = MEMOCEAN_DATA_DIR / "tasks"
CLOSET_ROOT = MEMOCEAN_DATA_DIR / "seabed"
SHARED_ROOT = MEMOCEAN_DATA_DIR / "shared"
LEARNED_SKILLS_DIR = MEMOCEAN_DATA_DIR / "shared" / "learned-skills" / "approved"

# ── Backward-compat aliases ──────────────────────────────────────────────────
# Legacy code that references BOTS_ROOT, CHANNELLAB_BOTS_ROOT, OCEAN_VAULT_ROOT,
# or PEARL_SKILLS_DIR continues to work unchanged.
BOTS_ROOT = MEMOCEAN_DATA_DIR
CHANNELLAB_BOTS_ROOT = MEMOCEAN_DATA_DIR
OCEAN_VAULT_ROOT = MEMOCEAN_VAULT_ROOT
PEARL_SKILLS_DIR = MEMOCEAN_SKILLS_DIR
