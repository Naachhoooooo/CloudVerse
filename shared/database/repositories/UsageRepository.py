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
        from datetime import timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        
        last_updated_str = list(row.values())[0]
        if last_updated_str:
            if ' ' in last_updated_str:
                last_updated_str = last_updated_str.replace(' ', 'T')
            last_updated_utc = datetime.fromisoformat(last_updated_str)
            if last_updated_utc.tzinfo is None:
                last_updated_utc = last_updated_utc.replace(tzinfo=timezone.utc)
            last_updated_ist = last_updated_utc.astimezone(ist)
        else:
            last_updated_ist = datetime.min.replace(tzinfo=ist)
            
        now_ist = datetime.now(ist)

        reset_fields = []
        if last_updated_ist.year != now_ist.year:
            reset_fields.extend(["transferred_this_year = 0", "transfer_count_this_year = 0",
                                  "transferred_this_month = 0", "transfer_count_this_month = 0",
                                  "transferred_this_week = 0", "transfer_count_this_week = 0",
                                  "transferred_today = 0", "transfer_count_today = 0",
                                  "daily_quota_used = 0", "last_reset = CURRENT_TIMESTAMP"])
        elif last_updated_ist.month != now_ist.month:
            reset_fields.extend(["transferred_this_month = 0", "transfer_count_this_month = 0",
                                  "transferred_this_week = 0", "transfer_count_this_week = 0",
                                  "transferred_today = 0", "transfer_count_today = 0",
                                  "daily_quota_used = 0", "last_reset = CURRENT_TIMESTAMP"])
        elif last_updated_ist.isocalendar()[1] != now_ist.isocalendar()[1]:
            reset_fields.extend(["transferred_this_week = 0", "transfer_count_this_week = 0",
                                  "transferred_today = 0", "transfer_count_today = 0",
                                  "daily_quota_used = 0", "last_reset = CURRENT_TIMESTAMP"])
        elif last_updated_ist.date() != now_ist.date():
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

    async def reset_all_periods(self, weekly: bool = False, monthly: bool = False, yearly: bool = False) -> bool:
        """Scheduled eager reset for all users based on period."""
        reset_fields = [
            "daily_quota_used = 0", 
            "transferred_today = 0", 
            "transfer_count_today = 0",
            "last_reset = CURRENT_TIMESTAMP"
        ]
        if weekly:
            reset_fields.extend(["transferred_this_week = 0", "transfer_count_this_week = 0"])
        if monthly:
            reset_fields.extend(["transferred_this_month = 0", "transfer_count_this_month = 0"])
        if yearly:
            reset_fields.extend(["transferred_this_year = 0", "transfer_count_this_year = 0"])

        query = f"UPDATE cloudverse_usage SET {', '.join(reset_fields)}"
        await self.execute(query)
        logger.info(f"[{self.log_tag}] Executed global usage reset (weekly={weekly}, monthly={monthly}, yearly={yearly}).")
        return True

    async def get_advanced_analytics(self) -> dict:
        """Fetch advanced analytics: user growth and transfer metrics."""
        # User Growth
        user_metrics = await self.fetch_one("""
            SELECT 
                COUNT(*) as total_users,
                SUM(CASE WHEN requested_at >= datetime('now', '-1 day') THEN 1 ELSE 0 END) as daily_new,
                SUM(CASE WHEN requested_at >= datetime('now', '-7 days') THEN 1 ELSE 0 END) as weekly_new,
                SUM(CASE WHEN requested_at >= datetime('now', '-30 days') THEN 1 ELSE 0 END) as monthly_new,
                SUM(CASE WHEN requested_at >= datetime('now', '-365 days') THEN 1 ELSE 0 END) as yearly_new
            FROM cloudverse_accounts
        """)
        
        # Transfers
        # For transfers, since QuotaManager resets transferred_today to 0, 
        # we can just sum up transferred_today BEFORE it gets reset!
        transfer_metrics = await self.fetch_one("""
            SELECT 
                SUM(transferred_today) as total_size_today,
                SUM(transfer_count_today) as total_count_today,
                SUM(transferred_this_week) as total_size_week,
                SUM(transferred_this_month) as total_size_month,
                SUM(transferred_this_year) as total_size_year,
                SUM(transferred_lifetime) as total_size_lifetime
            FROM cloudverse_usage
        """)
        
        # Failures
        # We need to query cloudverse_transfers for failed status in the last 24h
        failures = await self.fetch_one("""
            SELECT COUNT(*) as failure_count
            FROM cloudverse_transfers
            WHERE status IN ('failed', 'error') AND created_at >= datetime('now', '-1 day')
        """)
        
        return {
            'users': dict(user_metrics) if user_metrics else {},
            'transfers': dict(transfer_metrics) if transfer_metrics else {},
            'failures': dict(failures) if failures else {}
        }

    async def get_monthly_analytics(self) -> dict:
        """Fetch advanced monthly enterprise analytics."""
        # Top 10 users for the month
        top_users = await self.fetch_all("""
            SELECT u.telegram_id, u.username, u.transferred_this_month, u.transfer_count_this_month, a.role
            FROM cloudverse_usage u
            LEFT JOIN cloudverse_accounts a ON u.telegram_id = a.telegram_id
            WHERE u.transferred_this_month > 0
            ORDER BY u.transferred_this_month DESC 
            LIMIT 10
        """)
        
        # User retention/participation
        active_users = await self.fetch_one("""
            SELECT COUNT(*) as active_count FROM cloudverse_usage WHERE transferred_this_month > 0
        """)
        
        # Total metrics for the month
        metrics = await self.fetch_one("""
            SELECT 
                SUM(transferred_this_month) as total_size,
                SUM(transfer_count_this_month) as total_count,
                AVG(transferred_this_month) as avg_size
            FROM cloudverse_usage
            WHERE transferred_this_month > 0
        """)
        
        # Failures in the last 30 days
        failures = await self.fetch_one("""
            SELECT COUNT(*) as failure_count
            FROM cloudverse_transfers
            WHERE status IN ('failed', 'error') AND created_at >= datetime('now', '-30 days')
        """)
        
        return {
            'top_users': top_users,
            'active_users': active_users['active_count'] if active_users else 0,
            'metrics': dict(metrics) if metrics else {},
            'failures': failures['failure_count'] if failures else 0
        }

    async def get_yearly_analytics(self) -> dict:
        """Fetch advanced yearly enterprise analytics."""
        # Top 10 users for the year
        top_users = await self.fetch_all("""
            SELECT u.telegram_id, u.username, u.transferred_this_year, u.transfer_count_this_year, a.role
            FROM cloudverse_usage u
            LEFT JOIN cloudverse_accounts a ON u.telegram_id = a.telegram_id
            WHERE u.transferred_this_year > 0
            ORDER BY u.transferred_this_year DESC 
            LIMIT 10
        """)
        
        # User retention/participation
        active_users = await self.fetch_one("""
            SELECT COUNT(*) as active_count FROM cloudverse_usage WHERE transferred_this_year > 0
        """)
        
        # Total metrics for the year
        metrics = await self.fetch_one("""
            SELECT 
                SUM(transferred_this_year) as total_size,
                SUM(transfer_count_this_year) as total_count,
                AVG(transferred_this_year) as avg_size
            FROM cloudverse_usage
            WHERE transferred_this_year > 0
        """)
        
        # Failures in the last 365 days
        failures = await self.fetch_one("""
            SELECT COUNT(*) as failure_count
            FROM cloudverse_transfers
            WHERE status IN ('failed', 'error') AND created_at >= datetime('now', '-365 days')
        """)
        
        return {
            'top_users': top_users,
            'active_users': active_users['active_count'] if active_users else 0,
            'metrics': dict(metrics) if metrics else {},
            'failures': failures['failure_count'] if failures else 0
        }
