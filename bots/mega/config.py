"""
Central configuration for the Mega Bot instance.
Loads environment variables, initializes encryption components,
and provides legacy key fallbacks for backwards compatibility.
"""
from pathlib import Path
import os

MEGA_BOT_TOKEN = os.getenv("MEGA_BOT_TOKEN")
MEGA_SUPER_ADMIN_ID = os.getenv("MEGA_SUPER_ADMIN_ID") or os.getenv("GLOBAL_SUPER_ADMIN_ID")

TeamCloudverse_GROUP_CHAT_ID = (
    os.getenv("TEAM_CLOUDVERSE_GROUP_CHAT_ID")
    or os.getenv("TeamCloudverse_GROUP_CHAT_ID")
)
CLOUDVERSE_SUPPORT_GROUP_ID = os.getenv("CLOUDVERSE_SUPPORT_GROUP_ID")
LOCAL_FILES_GROUP_ID = os.getenv("LOCAL_FILES_GROUP_ID")
Access_TOPIC_ID = (
    os.getenv("ACCESS_TOPIC_ID")
    or os.getenv("Access_TOPIC_ID")
)
Flags_TOPIC_ID = (
    os.getenv("FLAGS_TOPIC_ID")
    or os.getenv("Flags_TOPIC_ID")
)
Broadcasts_TOPIC_ID = (
    os.getenv("BROADCASTS_TOPIC_ID")
    or os.getenv("Broadcasts_TOPIC_ID")
)
Management_TOPIC_ID = (
    os.getenv("MANAGEMENT_TOPIC_ID")
    or os.getenv("Management_TOPIC_ID")
)
Alerts_TOPIC_ID = (
    os.getenv("ALERTS_TOPIC_ID")
    or os.getenv("Alerts_TOPIC_ID")
)
Bugs_TOPIC_ID = (
    os.getenv("BUGS_TOPIC_ID")
    or os.getenv("Bugs_TOPIC_ID")
)
BACKUP_TOPIC_ID = (
    os.getenv("BACKUP_TOPIC_ID")
    or os.getenv("BACKUP_TOPIC_ID")
)

if not MEGA_BOT_TOKEN:
    raise ValueError("MEGA_BOT_TOKEN (or BOT_TOKEN) is not set in .env")
if not MEGA_SUPER_ADMIN_ID:
    raise ValueError("MEGA_SUPER_ADMIN_ID (or SUPER_ADMIN_ID) is not set")
if not TeamCloudverse_GROUP_CHAT_ID:
    raise ValueError("TeamCloudverse_GROUP_CHAT_ID (or GROUP_CHAT_ID) is not set")

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("ENCRYPTION_KEY is not set. Please set this environment variable.")

ENCRYPTION_SALT = os.getenv("ENCRYPTION_SALT")
if not ENCRYPTION_SALT:
    raise ValueError("ENCRYPTION_SALT is not set. Please set this environment variable.")

# Single independent database per bot — all tables live here
BOT_DB_PATH    = Path(__file__).parent.parent.parent / "data" / "databases" / "cloudverse_mega.db"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# PROVIDER constant removed — mega.db implicitly encodes the provider.
DEFAULT_QUOTA = int(os.getenv('DEFAULT_QUOTA', 3))

