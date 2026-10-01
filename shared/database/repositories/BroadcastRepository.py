"""
Drive-bot BroadcastRepository — manages cloudverse_broadcasts in drive.db.
"""
import json
from typing import Optional, Dict, Any, List
from shared.database.repositories.BaseRepository import BaseRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class BroadcastRepository(BaseRepository):
    def __init__(self, db_path: str, log_tag: str = "SYSTEM"):
        from pathlib import Path
        server_db_path = str(Path(__file__).parent.parent.parent.parent / "data" / "databases" / "cloudverse_server.db")
        super().__init__(server_db_path)
        self.table_name = "cloudverse_broadcasts"
        self.log_tag = log_tag

    async def create(self, requester_telegram_id: str, requester_username: Optional[str],
                     group_message_id: Optional[str], message_text: Optional[str],
                     media_type: Optional[str] = None, media_file_id: Optional[str] = None,
                     target_count: int = 0, target_audience: str = 'all') -> int:
        query = """
            INSERT INTO cloudverse_broadcasts
            (requester_telegram_id, requester_username, group_message_id, message_text,
             media_type, media_file_id, approval_status, target_count, target_audience, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, CURRENT_TIMESTAMP)
        """
        result = await self.execute(
            query, (requester_telegram_id, requester_username, group_message_id,
                    message_text, media_type, media_file_id, target_count, target_audience))
        row_id = result if isinstance(result, int) else getattr(result, 'lastrowid', 0)
        logger.info(f"[{self.log_tag}] Created broadcast request {row_id}")
        return row_id

    async def get(self, request_id: int) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_broadcasts WHERE request_id = ?"
        row = await self.fetch_one(query, (request_id,))
        return row

    async def get_all(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_broadcasts ORDER BY request_id DESC"
        return await self.fetch_all(query)

    async def get_pending(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_broadcasts WHERE approval_status = 'pending'"
        return await self.fetch_all(query)

    async def update_status(self, request_id: int, status: str,
                             approved_by: Optional[str] = None) -> bool:
        query = """
            UPDATE cloudverse_broadcasts
            SET approval_status = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP,
                last_updated = CURRENT_TIMESTAMP
            WHERE request_id = ?
        """
        await self.execute(query, (status, approved_by, request_id))
        logger.info(f"[{self.log_tag}] Broadcast {request_id} status → {status}")
        return True

    async def update_approvers(self, request_id: int, approvers: List[str]) -> bool:
        approvers_json = json.dumps(approvers)
        query = """
            UPDATE cloudverse_broadcasts
            SET approved_by = ?, last_updated = CURRENT_TIMESTAMP
            WHERE request_id = ?
        """
        await self.execute(query, (approvers_json, request_id))
        return True

    async def update_group_message_id(self, request_id: int, group_message_id: int) -> bool:
        query = """
            UPDATE cloudverse_broadcasts
            SET group_message_id = ?, last_updated = CURRENT_TIMESTAMP
            WHERE request_id = ?
        """
        await self.execute(query, (group_message_id, request_id))
        return True

    async def delete(self, request_id: int) -> bool:
        query = "DELETE FROM cloudverse_broadcasts WHERE request_id = ?"
        await self.execute(query, (request_id,))
        logger.info(f"[{self.log_tag}] Deleted broadcast {request_id}")
        return True
