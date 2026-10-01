"""
Drive Bot — Configuration

Reads environment variables from .env via python-dotenv.
Python variable names are kept stable so downstream imports are unaffected;
only the env var keys were normalised to SCREAMING_SNAKE_CASE in .env.example.

Old key names are accepted as fallbacks during the transition period.
"""
from pathlib import Path
import os
import os

# ── Telegram ──────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.getenv("DRIVE_BOT_TOKEN")
SUPER_ADMIN_ID = os.getenv("DRIVE_SUPER_ADMIN_ID") or os.getenv("GLOBAL_SUPER_ADMIN_ID")

# ── Admin Group & Topics ─────────────────────────────────────────────────────
# Accept new standardised key first; fall back to legacy key for smooth migration
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

# ── Encryption ────────────────────────────────────────────────────────────────
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("ENCRYPTION_KEY is not set. Run: python tests/generate_encryption_keys.py")

ENCRYPTION_SALT = os.getenv("ENCRYPTION_SALT")
if not ENCRYPTION_SALT:
    raise ValueError("ENCRYPTION_SALT is not set. Run: python tests/generate_encryption_keys.py")

# ── Google Drive OAuth ────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/drive"]

GDRIVE_CLIENT_ID     = os.getenv("GDRIVE_CLIENT_ID")
GDRIVE_CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET")
GDRIVE_AUTH_URI      = os.getenv("GDRIVE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth")
GDRIVE_TOKEN_URI     = os.getenv("GDRIVE_TOKEN_URI", "https://oauth2.googleapis.com/token")

if not GDRIVE_CLIENT_ID:
    raise ValueError("GDRIVE_CLIENT_ID is not set. Add it to your .env file.")
if not GDRIVE_CLIENT_SECRET:
    raise ValueError("GDRIVE_CLIENT_SECRET is not set. Add it to your .env file.")

# Dict consumed by InstalledAppFlow.from_client_config() — mirrors the credentials.json shape.
GDRIVE_CLIENT_CONFIG = {
    "installed": {
        "client_id":     GDRIVE_CLIENT_ID,
        "client_secret": GDRIVE_CLIENT_SECRET,
        "auth_uri":      GDRIVE_AUTH_URI,
        "token_uri":     GDRIVE_TOKEN_URI,
        "redirect_uris": ["http://localhost", "urn:ietf:wg:oauth:2.0:oob"],
    }
}

# ── Database ──────────────────────────────────────────────────────────────────
# Single independent database per bot — all tables live here
BOT_DB_PATH    = Path(__file__).parent.parent.parent / "data" / "databases" / "cloudverse_drive.db"

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# PROVIDER constant removed — drive.db implicitly encodes the provider.
DEFAULT_QUOTA = int(os.getenv('DEFAULT_QUOTA', 3))

