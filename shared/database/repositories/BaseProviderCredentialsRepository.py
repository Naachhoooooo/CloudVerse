import json
from shared.database.repositories.BaseRepository import BaseRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class BaseProviderCredentialsRepository(BaseRepository):
    """
    Base repository for provider credentials (Drive, Mega, Rclone).
    Provides generic encryption/decryption, existence checks, and deletion logic.
    Subclasses must implement specific upsert/get methods due to schema differences
    (e.g., email_address in Drive, rclone.conf blobs in Rclone).
    """
    
    def __init__(self, db_path: str, encryption_manager, credential_col: str, log_tag: str):
        super().__init__(db_path)
        self._encryption_manager = encryption_manager
        self.table_name = "cloudverse_credentials"
        self.credential_col = credential_col
        self.log_tag = log_tag

    async def _encrypt(self, data, telegram_id: str) -> str:
        import asyncio
        if isinstance(data, dict):
            data = json.dumps(data)
        return await asyncio.to_thread(
            self._encryption_manager.encrypt_credentials, data, str(telegram_id)
        )

    async def _decrypt(self, blob: str, telegram_id: str, as_dict: bool = True):
        import asyncio
        try:
            decrypted_data = await asyncio.to_thread(
                self._encryption_manager.decrypt_credentials, blob, str(telegram_id)
            )
            if as_dict:
                try:
                    if isinstance(decrypted_data, str):
                        return json.loads(decrypted_data)
                    return decrypted_data
                except Exception:
                    return {}
            return decrypted_data
        except Exception as e:
            logger.error(f"Failed to decrypt credentials: {e}")
            return {} if as_dict else ""

    async def has_credentials(self, telegram_id: str) -> bool:
        """Return True if user has stored credentials."""
        query = f"SELECT {self.credential_col} FROM {self.table_name} WHERE telegram_id = ?"  # nosec B608
        try:
            row = await self.fetch_one(query, (str(telegram_id),))
            return bool(row and list(row.values())[0])
        except Exception as e:
            logger.error(f"[{self.log_tag}][AUTH] Error checking credentials for {telegram_id}: {e}", exc_info=True)
            raise

    async def clear_credentials(self, telegram_id: str) -> bool:
        """Nullify credential blob, keep other settings intact."""
        query = f"UPDATE {self.table_name} SET {self.credential_col} = NULL, last_updated = CURRENT_TIMESTAMP WHERE telegram_id = ?"  # nosec B608
        await self.execute(query, (str(telegram_id),))
        logger.info(f"[{self.log_tag}][AUTH] Cleared credentials for {telegram_id}")
        return True

    async def delete(self, telegram_id: str) -> bool:
        """Delete the full credential row for a user."""
        query = f"DELETE FROM {self.table_name} WHERE telegram_id = ?"  # nosec B608
        await self.execute(query, (str(telegram_id),))
        logger.info(f"[{self.log_tag}][AUTH] Deleted credential row for {telegram_id}")
        return True
