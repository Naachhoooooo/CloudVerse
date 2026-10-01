"""
RcloneCredentialsRepository — manages cloudverse_credentials in rclone.db.
Stores the entire rclone .conf file as an AES-128 encrypted TEXT blob.
No email_address, no default_upload_location, no parallel_uploads (uses parallel_transfers).
"""
from typing import Optional, Dict
from shared.database.repositories.BaseProviderCredentialsRepository import BaseProviderCredentialsRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class RcloneCredentialsRepository(BaseProviderCredentialsRepository):
    def __init__(self, db_path: str, cipher):
        super().__init__(db_path, cipher, credential_col="rclone_credential", log_tag="RCLONE")

    async def upsert(self, telegram_id: str, username: Optional[str],
                     config_text: str) -> bool:
        """
        Store/update the full rclone .conf file content (encrypted).
        The entire .conf file is passed as config_text and encrypted at rest.
        """
        logger.info(f"[{self.log_tag}][AUTH] Upserting rclone config for {telegram_id}")
        encrypted = await self._encrypt(config_text, str(telegram_id))
        query = """
            INSERT INTO cloudverse_credentials
                (telegram_id, username, rclone_credential, last_updated, last_used)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                rclone_credential=excluded.rclone_credential,
                last_updated=CURRENT_TIMESTAMP,
                last_used=CURRENT_TIMESTAMP
        """
        await self.execute(query, (str(telegram_id), username, encrypted))
        return True

    async def get_config_text(self, telegram_id: str) -> Optional[str]:
        """
        Decrypt and return the rclone config text.
        Use: decrypt → write to secure temp file → pass path to rclone CLI → delete immediately.
        Never log the returned plaintext.
        """
        query = f"SELECT {self.credential_col} FROM cloudverse_credentials WHERE telegram_id = ?"
        try:
            row = await self.fetch_one(query, (str(telegram_id),))
            if row and row[self.credential_col]:
                # Update last_used timestamp
                await self.execute(
                    "UPDATE cloudverse_credentials SET last_used = CURRENT_TIMESTAMP WHERE telegram_id = ?",
                    (str(telegram_id),)
                )
                return await self._decrypt(row[self.credential_col], str(telegram_id), as_dict=False)
            return None
        except Exception as e:
            logger.error(f"[{self.log_tag}][AUTH] Error getting config for {telegram_id}: {e}", exc_info=True)
            raise

    async def get_full_credentials(self, telegram_id: str) -> Optional[Dict]:
        """Return the full credentials row, with rclone_credential decrypted."""
        query = "SELECT * FROM cloudverse_credentials WHERE telegram_id = ?"
        try:
            row = await self.fetch_one(query, (str(telegram_id),))
            if row:
                result = dict(row)
                if result.get(self.credential_col):
                    # Update last_used timestamp
                    await self.execute(
                        "UPDATE cloudverse_credentials SET last_used = CURRENT_TIMESTAMP WHERE telegram_id = ?",
                        (str(telegram_id),)
                    )
                    result[self.credential_col] = await self._decrypt(result[self.credential_col], str(telegram_id), as_dict=False)
                return result
            return None
        except Exception as e:
            logger.error(f"[{self.log_tag}][AUTH] Error getting credentials for {telegram_id}: {e}", exc_info=True)
            raise

    async def get_parallel_transfers(self, telegram_id: str) -> int:
        """Return configured parallel transfer count (default: 1)."""
        query = "SELECT parallel_transfers FROM cloudverse_credentials WHERE telegram_id = ?"
        try:
            row = await self.fetch_one(query, (str(telegram_id),))
            return list(row.values())[0] if row and list(row.values())[0] is not None else 1
        except Exception as e:
            logger.error(f"[{self.log_tag}][AUTH] Error getting parallel_transfers for {telegram_id}: {e}", exc_info=True)
            raise

    async def update_parallel_transfers(self, telegram_id: str, parallel: int) -> bool:
        query = """UPDATE cloudverse_credentials
                   SET parallel_transfers = ?, last_updated = CURRENT_TIMESTAMP
                   WHERE telegram_id = ?"""
        await self.execute(query, (parallel, str(telegram_id)))
        logger.info(f"[{self.log_tag}][AUTH] Updated parallel_transfers to {parallel} for {telegram_id}")
        return True
