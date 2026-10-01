"""
ConfigHelper — per-user rclone config context manager.

Each user's rclone .conf file is stored as an AES-256 encrypted blob in rclone.db.
Before every rclone command, we must:
  1. Decrypt the blob from the DB
  2. Write it to a secure NamedTemporaryFile
  3. Pass the path to rclone via --config
  4. Delete the temp file immediately after (guaranteed by context manager)

Never persist decrypted config. Never log config contents.
Tags: [RCLONE][AUTH]
"""

import os
import tempfile
from contextlib import asynccontextmanager
from typing import Optional, AsyncGenerator
from shared.core.Logger import get_logger

logger = get_logger(__name__)


def _write_temp_config(config_text: str) -> str:
    fd, tmp_path = tempfile.mkstemp(suffix=".conf", prefix="rclone_")
    try:
        os.chmod(tmp_path, 0o600)  # Owner read/write only
        with os.fdopen(fd, 'w') as f:
            f.write(config_text)
        return tmp_path
    except Exception as e:
        logger.error(f"[RCLONE][AUTH] Failed to write temp config file: {e}", exc_info=True)
        os.close(fd)
        raise

@asynccontextmanager
async def user_config(credential_repo, telegram_id: str) -> AsyncGenerator[str, None]:
    config_text: Optional[str] = None
    tmp_path: Optional[str] = None

    try:
        logger.debug(f"[RCLONE][AUTH] Decrypting config for user {telegram_id}")
        config_text = await credential_repo.get_config_text(str(telegram_id))
        if not config_text:
            raise RuntimeError(
                f"[RCLONE] No config found for user {telegram_id}. "
                "Have them upload a .conf file via /login."
            )

        tmp_path = _write_temp_config(config_text)
        logger.debug(f"[RCLONE][AUTH] Config written to temp file for user {telegram_id}")
        yield tmp_path

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                with open(tmp_path, 'w') as f:
                    f.write('\0' * 128)  # Overwrite with nulls
                os.remove(tmp_path)
                logger.debug(f"[RCLONE][AUTH] Temp config deleted for user {telegram_id}")
            except Exception as e:
                logger.warning(f"[RCLONE][AUTH] Failed to delete temp config for {telegram_id}: {e}")
        config_text = None


async def validate_user_config(credential_repo, telegram_id: str) -> bool:
    """
    Validate that a user's stored config is usable by rclone.
    Decrypts and writes to temp, then runs rclone listremotes.
    Returns True if valid, False otherwise.
    """
    from bots.rclone.services.RcloneService import validate_config
    try:
        async with user_config(credential_repo, telegram_id) as config_path:
            return await validate_config(config_path)
    except RuntimeError:
        return False  # No config stored
    except Exception as e:
        logger.error(
            f"[RCLONE][AUTH] Config validation failed for {telegram_id}: {e}",
            exc_info=True
        )
        return False
