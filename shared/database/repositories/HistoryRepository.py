"""
Shared HistoryRepository — manages cloudverse_history in shared.db.
Records all admin/system actions (account create, ban, promote, etc.).
"""
from typing import Optional, Dict, Any, List
from shared.database.repositories.BaseRepository import BaseRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class HistoryRepository(BaseRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "cloudverse_history"

    async def create(
        self,
        telegram_id: Optional[str] = None,
        username: Optional[str] = None,
        user_role: Optional[str] = None,
        user_role_after: Optional[str] = None,
        action_taken: Optional[str] = None,
        status: Optional[str] = None,
        admin_telegram_id: Optional[str] = None,
        admin_username: Optional[str] = None,
        admin_role: Optional[str] = None,
        related_message_id: Optional[str] = None,
        event_details: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """Insert a new history entry."""
        logger.info(f"[SYSTEM] Creating history entry: action={action_taken} for telegram_id={telegram_id}")
        query = """
            INSERT INTO cloudverse_history
                (telegram_id, username, user_role, user_role_after, action_taken, status,
                 admin_telegram_id, admin_username, admin_role,
                 related_message_id, event_details, notes, event_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        try:
            await self.execute(query, (
                str(telegram_id) if telegram_id else None,
                username, user_role, user_role_after, action_taken, status,
                str(admin_telegram_id) if admin_telegram_id else "SYSTEM",
                admin_username, admin_role,
                related_message_id, event_details, notes,
            ))
            return True
        except Exception as e:
            logger.error(f"[SYSTEM] Error creating history entry: {e}", exc_info=True)
            raise

    async def get(self, history_id: int) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_history WHERE id = ?"
        row = await self.fetch_one(query, (history_id,))
        return row

    async def get_all(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_history ORDER BY id DESC"
        rows = await self.fetch_all(query)
        return [r for r in rows]

    async def get_by_user(self, telegram_id: str) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_history WHERE telegram_id = ? ORDER BY id DESC"
        rows = await self.fetch_all(query, (str(telegram_id),))
        return [r for r in rows]

    async def safe_create(self, **kwargs) -> bool:
        """Safely log a history event, catching and reporting any database errors without crashing."""
        try:
            return await self.create(**kwargs)
        except Exception as e:
            logger.error(f"[SYSTEM] Failed to safely log history event: {e}", exc_info=True)
            return False
