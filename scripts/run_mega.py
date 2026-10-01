"""
run_mega.py — Mega Bot Entrypoint

Pre-flight checks then starts the Mega bot process.
For running all bots together use run_all.py.
"""

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from shared.core.Startup import run_bot  # noqa: E402
from shared.core.Logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def get_db_path():
    from bots.mega.config import BOT_DB_PATH
    return BOT_DB_PATH


def main():
    required_packages = {
        'telegram': 'telegram',
        'cryptography': 'cryptography',
        'python-dotenv': 'dotenv',
        'psutil': 'psutil',
        'nest_asyncio': 'nest_asyncio',
        'telethon': 'telethon',
        'mega.py': 'mega',
    }

    from bots.mega.config import (
        MEGA_BOT_TOKEN, TeamCloudverse_GROUP_CHAT_ID, MEGA_SUPER_ADMIN_ID,
        CLOUDVERSE_SUPPORT_GROUP_ID, LOCAL_FILES_GROUP_ID,
        ENCRYPTION_KEY, ENCRYPTION_SALT, Access_TOPIC_ID,
        Flags_TOPIC_ID, Broadcasts_TOPIC_ID, Management_TOPIC_ID, Alerts_TOPIC_ID, Bugs_TOPIC_ID, BACKUP_TOPIC_ID,
    )

    required_vars = {
        'BOT_TOKEN': MEGA_BOT_TOKEN,
        'TeamCloudverse_GROUP_CHAT_ID': TeamCloudverse_GROUP_CHAT_ID,
        'CLOUDVERSE_SUPPORT_GROUP_ID': CLOUDVERSE_SUPPORT_GROUP_ID,
        'LOCAL_FILES_GROUP_ID': LOCAL_FILES_GROUP_ID,
        'SUPER_ADMIN_ID': MEGA_SUPER_ADMIN_ID,
        'ENCRYPTION_KEY': ENCRYPTION_KEY,
        'ENCRYPTION_SALT': ENCRYPTION_SALT,
        'Access_TOPIC_ID': Access_TOPIC_ID,
        'Flags_TOPIC_ID': Flags_TOPIC_ID,
        'Broadcasts_TOPIC_ID': Broadcasts_TOPIC_ID,
        'Management_TOPIC_ID': Management_TOPIC_ID,
        'Alerts_TOPIC_ID': Alerts_TOPIC_ID,
        'Bugs_TOPIC_ID': Bugs_TOPIC_ID,
        'BACKUP_TOPIC_ID': BACKUP_TOPIC_ID,
    }

    from bots.mega.main import main as bot_main

    run_bot(
        bot_name="Mega",
        main_func=bot_main,
        required_packages=required_packages,
        required_vars=required_vars,
        db_path_getter=get_db_path,
    )


if __name__ == "__main__":
    main()
