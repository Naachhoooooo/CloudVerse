"""
RcloneTransferRepository — manages cloudverse_transfers in rclone.db.
Cloud-to-cloud only: no file_id, no session_used, no method, no source_url.
Schema: job_id, file_count, transfer_size, transfer_source, transfer_destination,
         average_transfer_speed, transfer_duration, completed, last_updated.
"""
from typing import Optional, Dict, Any, List
from shared.database.repositories.BaseRepository import BaseRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class RcloneTransferRepository(BaseRepository):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "cloudverse_transfers"

    async def insert(
        self,
        telegram_id: str,
        username: Optional[str] = None,
        worker_id: Optional[str] = None,
        job_id: Optional[str] = None,
        file_count: Optional[int] = None,
        transfer_size: Optional[int] = None,
        status: str = 'pending',
        error_message: Optional[str] = None,
        transfer_source: Optional[str] = None,
        transfer_destination: Optional[str] = None,
        average_transfer_speed: Optional[float] = None,
        transfer_duration: Optional[float] = None,
        bytes_transferred: int = 0,
        retry_count: int = 0,
        started: Optional[str] = None,
        completed: Optional[str] = None,
    ) -> int:
        """Insert a new rclone transfer job record. Returns the new row id."""
        logger.info(
            f"[RCLONE][TRANSFER] Creating transfer record for {telegram_id} "
            f"— {transfer_source} → {transfer_destination}"
        )
        query = """
            INSERT INTO cloudverse_transfers
                (telegram_id, username, worker_id, job_id, file_count, transfer_size,
                 status, error_message, transfer_source, transfer_destination,
                 average_transfer_speed, transfer_duration, bytes_transferred, retry_count, started, completed, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        result = await self.execute(query, (
            str(telegram_id), username, worker_id, job_id, file_count, transfer_size,
            status, error_message, transfer_source, transfer_destination,
            average_transfer_speed, transfer_duration, bytes_transferred, retry_count, started, completed,
        ))
        row_id = result if isinstance(result, int) else getattr(result, 'lastrowid', 0)
        logger.info(f"[RCLONE][TRANSFER] Transfer record created: id={row_id} job_id={job_id}")
        return row_id

    async def update_status(self, transfer_id: int, status: str,
                             error_message: Optional[str] = None,
                             average_transfer_speed: Optional[float] = None,
                             transfer_duration: Optional[float] = None,
                             file_count: Optional[int] = None,
                             transfer_size: Optional[int] = None,
                             bytes_transferred: Optional[int] = None,
                             retry_count: Optional[int] = None,
                             completed: Optional[str] = None) -> bool:
        """Update job status and optional metrics. Sets completed/last_updated on terminal states."""
        logger.info(f"[RCLONE][TRANSFER] Updating transfer {transfer_id} status → {status}")
        completed_clause = "completed = CURRENT_TIMESTAMP, " if status in ('completed', 'failed') else ""
        if completed:
            completed_clause = f"completed = '{completed}', "  # explicit completed timestamp override
        query = f"""UPDATE cloudverse_transfers
                   SET status = ?, error_message = ?,
                       average_transfer_speed = COALESCE(?, average_transfer_speed),
                       transfer_duration = COALESCE(?, transfer_duration),
                       file_count = COALESCE(?, file_count),
                       transfer_size = COALESCE(?, transfer_size),
                       bytes_transferred = COALESCE(?, bytes_transferred),
                       retry_count = COALESCE(?, retry_count),
                       {completed_clause}last_updated = CURRENT_TIMESTAMP
                   WHERE id = ?"""  # nosec B608
        await self.execute(query, (
            status, error_message,
            average_transfer_speed, transfer_duration,
            file_count, transfer_size,
            bytes_transferred, retry_count,
            transfer_id
        ))
        return True

    async def update_job_id(self, transfer_id: int, job_id: str) -> bool:
        """Link the rclone-generated job_id to an existing transfer record."""
        query = """UPDATE cloudverse_transfers
                   SET job_id = ?, last_updated = CURRENT_TIMESTAMP
                   WHERE id = ?"""
        await self.execute(query, (job_id, transfer_id))
        return True

    async def get(self, transfer_id: int) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_transfers WHERE id = ?"
        return await self.fetch_one(query, (transfer_id,))

    async def get_by_job_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Look up a transfer by rclone job_id (for status polling and cancellation)."""
        query = "SELECT * FROM cloudverse_transfers WHERE job_id = ?"
        return await self.fetch_one(query, (job_id,))

    async def get_all(self, telegram_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_transfers"
        params: list = []
        if telegram_id:
            query += " WHERE telegram_id = ?"
            params.append(str(telegram_id))
        query += " ORDER BY completed DESC"
        return await self.fetch_all(query, params)

    async def get_recent(self, telegram_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        query = """SELECT * FROM cloudverse_transfers
                   WHERE telegram_id = ? ORDER BY completed DESC LIMIT ?"""
        return await self.fetch_all(query, (str(telegram_id), limit))

    async def delete(self, transfer_id: int) -> bool:
        query = "DELETE FROM cloudverse_transfers WHERE id = ?"
        await self.execute(query, (transfer_id,))
        logger.info(f"[RCLONE][TRANSFER] Deleted transfer record {transfer_id}")
        return True


