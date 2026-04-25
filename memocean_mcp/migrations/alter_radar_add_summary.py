"""MEMO-011: Add summary column to radar table."""
import sqlite3
import sys
from pathlib import Path

# Use config.FTS_DB so MEMOCEAN_DATA_DIR env var is respected
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
try:
    from memocean_mcp.config import FTS_DB as DB_PATH
except ImportError:
    DB_PATH = Path.home() / ".memocean" / "memory.db"

def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Check if column already exists
        cols = {row[1] for row in conn.execute("PRAGMA table_info(radar)")}
        if "summary" not in cols:
            conn.execute("ALTER TABLE radar ADD COLUMN summary TEXT")
            conn.commit()
            print("[MEMO-011] Added summary column to radar table")
        else:
            print("[MEMO-011] summary column already exists, skipping")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
