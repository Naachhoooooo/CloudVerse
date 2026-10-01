"""
DriveCredentialsRepository — manages cloudverse_credentials in drive.db.
Credentials are encrypted with CIPHER on write and decrypted on read.
Access via ctx.bot_data['credential_repo'] in handlers.
"""
from typing import Optional, Dict
from shared.database.repositories.BaseProviderCredentialsRepository import BaseProviderCredentialsRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class DriveCredentialsRepository(BaseProviderCredentialsRepository):
    def __init__(self, db_path: str, cipher):
        super().__init__(db_path, cipher, credential_col="drive_credential", log_tag="DRIVE")

    async def upsert(self, telegram_id: str, username: Optional[str],
                     email_address: str, credentials: dict) -> bool:
        """Insert or replace credentials for a user (encrypts credential blob)."""
        logger.info(f"[{self.log_tag}][AUTH] Upserting credentials for {telegram_id}")
        encrypted = await self._encrypt(credentials, str(telegram_id))
        query = """
            INSERT INTO cloudverse_credentials
                (telegram_id, username, email_address, drive_credential, last_updated)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                email_address=excluded.email_address,
                drive_credential=excluded.drive_credential,
                last_updated=CURRENT_TIMESTAMP
        """
        await self.execute(query, (str(telegram_id), username, email_address, encrypted))
        return True

    async def get(self, telegram_id: str) -> Optional[Dict]:
        """Return decrypted credentials dict or None if not found."""
        query = "SELECT * FROM cloudverse_credentials WHERE telegram_id = ?"
        try:
            row = await self.fetch_one(query, (str(telegram_id),))
            if row:
                result = dict(row)
                if result.get(self.credential_col):
                    result[self.credential_col] = await self._decrypt(result[self.credential_col], str(telegram_id))
                return result
            return None
        except Exception as e:
            logger.error(f"[{self.log_tag}][AUTH] Error getting credentials for {telegram_id}: {e}", exc_info=True)
            raise

    async def get_default_location(self, telegram_id: str) -> str:
        """Return default upload folder ID (defaults to 'root')."""
        query = "SELECT default_upload_location FROM cloudverse_credentials WHERE telegram_id = ?"
        try:
            row = await self.fetch_one(query, (str(telegram_id),))
            return list(row.values())[0] if row and list(row.values())[0] else 'root'
        except Exception as e:
            logger.error(f"[{self.log_tag}][AUTH] Error getting default location for {telegram_id}: {e}", exc_info=True)
            raise

    async def update_default_location(self, telegram_id: str, location: str) -> bool:
        query = """UPDATE cloudverse_credentials
                   SET default_upload_location = ?, last_updated = CURRENT_TIMESTAMP
                   WHERE telegram_id = ?"""
        await self.execute(query, (location, str(telegram_id)))
        logger.info(f"[{self.log_tag}][AUTH] Updated default location for {telegram_id}")
        return True

    async def get_parallel_uploads(self, telegram_id: str) -> int:
        query = "SELECT parallel_uploads FROM cloudverse_credentials WHERE telegram_id = ?"
        try:
            row = await self.fetch_one(query, (str(telegram_id),))
            return list(row.values())[0] if row else 1
        except Exception as e:
            logger.error(f"[{self.log_tag}][AUTH] Error getting parallel_uploads for {telegram_id}: {e}", exc_info=True)
            raise

    async def update_parallel_uploads(self, telegram_id: str, parallel: int) -> bool:
        query = """UPDATE cloudverse_credentials
                   SET parallel_uploads = ?, last_updated = CURRENT_TIMESTAMP
                   WHERE telegram_id = ?"""
        await self.execute(query, (parallel, str(telegram_id)))
        logger.info(f"[{self.log_tag}][AUTH] Updated parallel_uploads to {parallel} for {telegram_id}")
        return True
