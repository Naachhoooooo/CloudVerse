from typing import Optional, Dict, Any, List
from shared.database.repositories.BaseRepository import BaseRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class BaseFileTransferRepository(BaseRepository):
    """
    Base repository for file-based transfers (Drive, Mega).
    Records full upload lifecycle: pending → uploading → completed/failed.
    """
    
    def __init__(self, db_path: str, log_tag: str):
        super().__init__(db_path)
        self.table_name = "cloudverse_transfers"
        self.log_tag = log_tag

    async def create(
        self,
        telegram_id: str,
        username: Optional[str] = None,
        worker_id: Optional[str] = None,
        file_id: Optional[str] = None,
        file_name: Optional[str] = None,
        file_type: Optional[str] = None,
        file_size: Optional[int] = None,
        status: str = 'pending',
        error_message: Optional[str] = None,
        method: Optional[str] = None,
        source_url: Optional[str] = None,
        session_used: Optional[str] = None,
        average_download_speed: Optional[float] = None,
        download_duration: Optional[float] = None,
        average_upload_speed: Optional[float] = None,
        upload_duration: Optional[float] = None,
        transfer_source: Optional[str] = None,
        bytes_transferred: int = 0,
        retry_count: int = 0,
        started: Optional[str] = None,
        completed: Optional[str] = None,
    ) -> int:
        """Insert a new transfer record. Returns the new row id."""
        logger.info(f"[{self.log_tag}][UPLOAD] Creating transfer record for {telegram_id} — {file_name}")
        query = f"""
            INSERT INTO {self.table_name}
                (telegram_id, username, worker_id, file_id, file_name, file_type, file_size,
                 status, error_message, method, source_url, session_used,
                 average_download_speed, download_duration,
                 average_upload_speed, upload_duration,
                 transfer_source, bytes_transferred, retry_count, started, completed, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        result = await self.execute(query, (
            str(telegram_id), username, worker_id, file_id, file_name, file_type, file_size,
            status, error_message, method, source_url, session_used,
            average_download_speed, download_duration,
            average_upload_speed, upload_duration,
            transfer_source, bytes_transferred, retry_count, started, completed,
        ))
        row_id = result if isinstance(result, int) else getattr(result, 'lastrowid', 0)
        logger.info(f"[{self.log_tag}][UPLOAD] Transfer record created: id={row_id} for {telegram_id}")
        return row_id

    async def update_status(self, transfer_id: int, status: str,
                             error_message: Optional[str] = None,
                             average_upload_speed: Optional[float] = None,
                             upload_duration: Optional[float] = None,
                             average_download_speed: Optional[float] = None,
                             download_duration: Optional[float] = None,
                             bytes_transferred: Optional[int] = None,
                             retry_count: Optional[int] = None) -> bool:
        """Update status and optional performance metrics."""
        logger.info(f"[{self.log_tag}][UPLOAD] Updating transfer {transfer_id} status → {status}")
        completed_clause = "completed = CURRENT_TIMESTAMP, " if status in ('completed', 'failed') else ""
        query = f"""UPDATE {self.table_name}
                   SET status = ?, error_message = ?,
                       average_upload_speed = COALESCE(?, average_upload_speed),
                       upload_duration = COALESCE(?, upload_duration),
                       average_download_speed = COALESCE(?, average_download_speed),
                       download_duration = COALESCE(?, download_duration),
                       bytes_transferred = COALESCE(?, bytes_transferred),
                       retry_count = COALESCE(?, retry_count),
                       {completed_clause}last_updated = CURRENT_TIMESTAMP
                   WHERE id = ?"""  # nosec B608
        await self.execute(query, (
            status, error_message,
            average_upload_speed, upload_duration,
            average_download_speed, download_duration,
            bytes_transferred, retry_count,
            transfer_id
        ))
        return True

    async def get(self, transfer_id: int) -> Optional[Dict[str, Any]]:
        query = f"SELECT * FROM {self.table_name} WHERE id = ?"  # nosec B608
        return await self.fetch_one(query, (transfer_id,))

    async def get_by_user(self, telegram_id: str,
                           status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = f"SELECT * FROM {self.table_name} WHERE telegram_id = ?"  # nosec B608
        params: list = [str(telegram_id)]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY completed DESC"
        return await self.fetch_all(query, params)

    async def get_completed(self, telegram_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = f"SELECT * FROM {self.table_name} WHERE status = 'completed'"  # nosec B608
        params: list = []
        if telegram_id:
            query += " AND telegram_id = ?"
            params.append(str(telegram_id))
        query += " ORDER BY completed DESC"
        return await self.fetch_all(query, params)

    async def get_recent(self, telegram_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        query = f"SELECT * FROM {self.table_name} WHERE telegram_id = ? ORDER BY completed DESC LIMIT ?"  # nosec B608
        return await self.fetch_all(query, (str(telegram_id), limit))

    async def delete(self, transfer_id: int) -> bool:
        query = f"DELETE FROM {self.table_name} WHERE id = ?"  # nosec B608
        await self.execute(query, (transfer_id,))
        logger.info(f"[{self.log_tag}][UPLOAD] Deleted transfer record {transfer_id}")
        return True


