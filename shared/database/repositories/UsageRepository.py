"""
Drive-bot UsageRepository — manages cloudverse_usage in drive.db.
Uses last_reset TIMESTAMP and last_transfer TIMESTAMP per spec.
"""
from datetime import datetime
from typing import Optional, Dict, Any
from shared.database.repositories.BaseRepository import BaseRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class UsageRepository(BaseRepository):
    def __init__(self, db_path: str, log_tag: str = "SYSTEM"):
        super().__init__(db_path)
        self.table_name = "cloudverse_usage"
        self.log_tag = log_tag

    async def create_usage(self, telegram_id: str, username: Optional[str]) -> bool:
        query = """
            INSERT OR IGNORE INTO cloudverse_usage
            (telegram_id, username, daily_transfer_limit, daily_quota_used,
             transferred_today, transfer_count_today,
             transferred_this_week, transfer_count_this_week,
             transferred_this_month, transfer_count_this_month,
             transferred_this_year, transfer_count_this_year,
             transferred_lifetime, transfer_count_lifetime,
             last_transfer, last_reset, last_updated)
            VALUES (?, ?, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
        await self.execute(query, (telegram_id, username))
        return True

    async def get_usage(self, telegram_id: str) -> int:
        query = "SELECT daily_quota_used FROM cloudverse_usage WHERE telegram_id = ?"
        row = await self.fetch_one(query, (telegram_id,))
        return list(row.values())[0] if row else 0

    async def get_usage_details(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_usage WHERE telegram_id = ?"
        row = await self.fetch_one(query, (telegram_id,))
        return row

    async def get_limit(self, telegram_id: str) -> Optional[int]:
        query = "SELECT daily_transfer_limit FROM cloudverse_usage WHERE telegram_id = ?"
        row = await self.fetch_one(query, (telegram_id,))
        return list(row.values())[0] if row else 5

    async def update_limit(self, telegram_id: str, limit: Optional[int]) -> bool:
        query = "UPDATE cloudverse_usage SET daily_transfer_limit = ?, last_updated = CURRENT_TIMESTAMP WHERE telegram_id = ?"
        await self.execute(query, (limit, telegram_id))
        return True

    async def check_and_reset_usage(self, telegram_id: str):
        """Lazy-reset period counters if time has elapsed since last update."""
        query = "SELECT last_updated FROM cloudverse_usage WHERE telegram_id = ?"
        row = await self.fetch_one(query, (telegram_id,))
        if not row:
            return
        last_updated = datetime.fromisoformat(list(row.values())[0]) if list(row.values())[0] else datetime.min
        now = datetime.now()

        reset_fields = []
        if last_updated.year != now.year:
            reset_fields.extend(["transferred_this_year = 0", "transfer_count_this_year = 0",
                                  "transferred_this_month = 0", "transfer_count_this_month = 0",
                                  "transferred_this_week = 0", "transfer_count_this_week = 0",
                                  "transferred_today = 0", "transfer_count_today = 0",
                                  "daily_quota_used = 0", "last_reset = CURRENT_TIMESTAMP"])
        elif last_updated.month != now.month:
            reset_fields.extend(["transferred_this_month = 0", "transfer_count_this_month = 0",
                                  "transferred_this_week = 0", "transfer_count_this_week = 0",
                                  "transferred_today = 0", "transfer_count_today = 0",
                                  "daily_quota_used = 0", "last_reset = CURRENT_TIMESTAMP"])
        elif last_updated.isocalendar()[1] != now.isocalendar()[1]:
            reset_fields.extend(["transferred_this_week = 0", "transfer_count_this_week = 0",
                                  "transferred_today = 0", "transfer_count_today = 0",
                                  "daily_quota_used = 0", "last_reset = CURRENT_TIMESTAMP"])
        elif last_updated.date() != now.date():
            reset_fields.extend(["transferred_today = 0", "transfer_count_today = 0",
                                  "daily_quota_used = 0", "last_reset = CURRENT_TIMESTAMP"])

        if reset_fields:
            reset_query = f"UPDATE cloudverse_usage SET {', '.join(reset_fields)} WHERE telegram_id = ?"  # nosec B608
            await self.execute(reset_query, (telegram_id,))

    async def increment_usage(self, telegram_id: str, file_size: int) -> bool:
        """Reset stale counters then increment all period/lifetime counters."""
        await self.check_and_reset_usage(telegram_id)
        query = """
            UPDATE cloudverse_usage SET
            transferred_today = transferred_today + ?,
            transfer_count_today = transfer_count_today + 1,
            transferred_this_week = transferred_this_week + ?,
            transfer_count_this_week = transfer_count_this_week + 1,
            transferred_this_month = transferred_this_month + ?,
            transfer_count_this_month = transfer_count_this_month + 1,
            transferred_this_year = transferred_this_year + ?,
            transfer_count_this_year = transfer_count_this_year + 1,
            transferred_lifetime = transferred_lifetime + ?,
            transfer_count_lifetime = transfer_count_lifetime + 1,
            daily_quota_used = daily_quota_used + 1,
            last_transfer = CURRENT_TIMESTAMP,
            last_updated = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
        """
        await self.execute(query, (file_size, file_size, file_size, file_size, file_size, telegram_id))
        return True

    async def reset_daily(self, telegram_id: str) -> bool:
        """Admin-triggered manual daily reset."""
        query = """
            UPDATE cloudverse_usage SET
            daily_quota_used = 0, transferred_today = 0, transfer_count_today = 0,
            last_reset = CURRENT_TIMESTAMP, last_updated = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
        """
        await self.execute(query, (telegram_id,))
        logger.info(f"[{self.log_tag}] Reset daily usage for {telegram_id}")
    async def get_top_users_daily(self, limit: int = 10) -> list:
        """Fetch the top users by transferred_today for analytics."""
        query = """
            SELECT u.telegram_id, u.username, u.transferred_today, u.transfer_count_today, a.role
            FROM cloudverse_usage u
            LEFT JOIN cloudverse_accounts a ON u.telegram_id = a.telegram_id
            WHERE u.transferred_today > 0
            ORDER BY u.transferred_today DESC 
            LIMIT ?
        """
        return await self.fetch_all(query, (limit,))

    async def reset_daily_all(self) -> bool:
        """Scheduled daily reset for all users."""
        query = """
            UPDATE cloudverse_usage SET
            daily_quota_used = 0, transferred_today = 0, transfer_count_today = 0,
            last_reset = CURRENT_TIMESTAMP
        """
        await self.execute(query)
        logger.info(f"[{self.log_tag}] Executed global daily usage reset.")
        return True
