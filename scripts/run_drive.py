"""
run_drive.py — Drive Bot Entrypoint

Thin wrapper that runs pre-flight checks then starts the Google Drive bot.
For running all bots together use run_all.py.
"""

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.core.Startup import run_bot  # noqa: E402


def get_db_path():
    from bots.drive.config import BOT_DB_PATH
    return BOT_DB_PATH


def main():
    required_packages = {
        'telegram': 'telegram',
        'google-api-python-client': 'googleapiclient',
        'google-auth-oauthlib': 'google_auth_oauthlib',
        'cryptography': 'cryptography',
        'python-dotenv': 'dotenv',
        'psutil': 'psutil',
        'nest_asyncio': 'nest_asyncio',
        'telethon': 'telethon',
    }

    from bots.drive.config import (
        BOT_TOKEN, TeamCloudverse_GROUP_CHAT_ID, SUPER_ADMIN_ID,
        CLOUDVERSE_SUPPORT_GROUP_ID, LOCAL_FILES_GROUP_ID,
        ENCRYPTION_KEY, ENCRYPTION_SALT, Access_TOPIC_ID,
        Flags_TOPIC_ID, Broadcasts_TOPIC_ID, Management_TOPIC_ID, Alerts_TOPIC_ID, Bugs_TOPIC_ID, BACKUP_TOPIC_ID,
        GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET,
    )

    required_vars = {
        'BOT_TOKEN': BOT_TOKEN,
        'TeamCloudverse_GROUP_CHAT_ID': TeamCloudverse_GROUP_CHAT_ID,
        'CLOUDVERSE_SUPPORT_GROUP_ID': CLOUDVERSE_SUPPORT_GROUP_ID,
        'LOCAL_FILES_GROUP_ID': LOCAL_FILES_GROUP_ID,
        'SUPER_ADMIN_ID': SUPER_ADMIN_ID,
        'ENCRYPTION_KEY': ENCRYPTION_KEY,
        'ENCRYPTION_SALT': ENCRYPTION_SALT,
        'GDRIVE_CLIENT_ID': GDRIVE_CLIENT_ID,
        'GDRIVE_CLIENT_SECRET': GDRIVE_CLIENT_SECRET,
        'Access_TOPIC_ID': Access_TOPIC_ID,
        'Flags_TOPIC_ID': Flags_TOPIC_ID,
        'Broadcasts_TOPIC_ID': Broadcasts_TOPIC_ID,
        'Management_TOPIC_ID': Management_TOPIC_ID,
        'Alerts_TOPIC_ID': Alerts_TOPIC_ID,
        'Bugs_TOPIC_ID': Bugs_TOPIC_ID,
        'BACKUP_TOPIC_ID': BACKUP_TOPIC_ID,
    }

    from bots.drive.main import main as bot_main

    run_bot(
        bot_name="Drive",
        main_func=bot_main,
        required_packages=required_packages,
        required_vars=required_vars,
        db_path_getter=get_db_path,
    )


if __name__ == "__main__":
    main()
