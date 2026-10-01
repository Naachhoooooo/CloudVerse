"""
Shared Configuration Module
Responsible for loading the .env file once for any bot process.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Find the project root (3 directories up from shared/core/config.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SERVER_DB_PATH = PROJECT_ROOT / "data" / "databases" / "cloudverse_server.db"

# Load .env explicitly from the project root
env_path = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=env_path)

# Shared environment variables can also be centralized here
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

def get_bot_db_path(bot_name: str) -> Path:
    """Return the database path for a given bot name to prevent cyclic imports."""
    bot = bot_name.lower()
    if bot in ["drive", "mega", "rclone"]:
        return PROJECT_ROOT / "data" / "databases" / f"cloudverse_{bot}.db"
    return SERVER_DB_PATH
