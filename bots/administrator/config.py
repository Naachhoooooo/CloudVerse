"""
Administrator Bot — Configuration

Reads environment variables from .env via python-dotenv.
"""
from pathlib import Path
import os

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("ADMIN_BOT_TOKEN") or os.getenv("SUPPORT_BOT_TOKEN")
SUPER_ADMIN_ID = os.getenv("SUPER_ADMIN_ID") # Global super admin

# ── Admin Group & Topics ─────────────────────────────────────────────────────
TeamCloudverse_GROUP_CHAT_ID = os.getenv("TEAM_CLOUDVERSE_GROUP_CHAT_ID") or os.getenv("TeamCloudverse_GROUP_CHAT_ID")
Access_TOPIC_ID = os.getenv("ACCESS_TOPIC_ID") or os.getenv("Access_TOPIC_ID")
Flags_TOPIC_ID = os.getenv("FLAGS_TOPIC_ID") or os.getenv("Flags_TOPIC_ID")
Broadcasts_TOPIC_ID = os.getenv("BROADCASTS_TOPIC_ID") or os.getenv("Broadcasts_TOPIC_ID")
Management_TOPIC_ID = os.getenv("MANAGEMENT_TOPIC_ID") or os.getenv("Management_TOPIC_ID")
Alerts_TOPIC_ID = os.getenv("ALERTS_TOPIC_ID") or os.getenv("Alerts_TOPIC_ID")
Bugs_TOPIC_ID = os.getenv("BUGS_TOPIC_ID") or os.getenv("Bugs_TOPIC_ID")
BACKUP_TOPIC_ID = os.getenv("BACKUP_TOPIC_ID")

# ── Encryption ────────────────────────────────────────────────────────────────
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("ENCRYPTION_KEY is not set. Run: python tests/generate_encryption_keys.py")

ENCRYPTION_SALT = os.getenv("ENCRYPTION_SALT")
if not ENCRYPTION_SALT:
    raise ValueError("ENCRYPTION_SALT is not set. Run: python tests/generate_encryption_keys.py")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

DEFAULT_QUOTA = int(os.getenv('DEFAULT_QUOTA', 3))
