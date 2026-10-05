import asyncio
from datetime import datetime, timedelta, timezone
from shared.core.Logger import get_logger
from shared.database.repositories.UsageRepository import UsageRepository
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)

class QuotaManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(QuotaManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self.default_limit = 5
        self.usage_repo = None
        self._shutdown_flag = False

    def initialize(self, db_path: str, default_limit: int = 5):
        self.default_limit = default_limit
        self.usage_repo = UsageRepository(db_path)
        
        from shared.managers.ScheduleManager import get_schedule_manager
        sched = get_schedule_manager()
        sched.register_daily_task("00:00", self.execute_daily_maintenance)
        sched.start()

    async def get_transferred_bytes(self, telegram_id: str, period: str = 'day') -> int:
        """Get transferred bytes for a specific period."""
        if not self.usage_repo:
            return 0
            
        await self.usage_repo.check_and_reset_usage(telegram_id)
        usage = await self.usage_repo.get_usage_details(telegram_id)
        if not usage:
            return 0
            
        if period == 'day':
            return usage.get('transferred_today', 0)
        elif period == 'week':
            return usage.get('transferred_this_week', 0)
        elif period == 'month':
            return usage.get('transferred_this_month', 0)
        elif period == 'lifetime':
            return usage.get('transferred_lifetime', 0)
        return 0

    async def get_limit(self, telegram_id: str) -> int:
        if not self.usage_repo:
            return self.default_limit
        limit = await self.usage_repo.get_limit(telegram_id)
        return limit if limit is not None else self.default_limit

    async def get_usage(self, telegram_id: str) -> int:
        if not self.usage_repo:
            return 0
        await self.usage_repo.check_and_reset_usage(telegram_id)
        return await self.usage_repo.get_usage(telegram_id)

    async def check_quota(self, telegram_id: str, is_admin: bool = False) -> tuple[bool, str]:
        if is_admin:
            return True, ""
            
        limit = await self.get_limit(telegram_id)
        usage = await self.get_usage(telegram_id)
        
        if usage >= limit:
            return False, f"Quota Exceeded. You have reached your daily transfer limit of {limit}."
            
        return True, ""

    async def consume_quota(self, telegram_id: str, file_size: int) -> bool:
        if not self.usage_repo:
            return False
        return await self.usage_repo.increment_usage(telegram_id, file_size)

    async def execute_daily_maintenance(self):
        """Executed exactly at midnight IST by the ScheduleManager."""
        try:
            ist = timezone(timedelta(hours=5, minutes=30))
            if self.usage_repo:
                # Fetch top users for daily analytics BEFORE reset
                top_users = await self.usage_repo.get_top_users_daily(10)
                advanced_metrics = await self.usage_repo.get_advanced_analytics()
                
                # Fetch monthly stats if it's the 1st of the month
                is_first_of_month = datetime.now(ist).day == 1
                is_jan_first = is_first_of_month and datetime.now(ist).month == 1
                
                monthly_data = None
                if is_first_of_month:
                    monthly_data = await self.usage_repo.get_monthly_analytics()
                    
                yearly_data = None
                if is_jan_first:
                    yearly_data = await self.usage_repo.get_yearly_analytics()
                
                from shared.managers.AlertManager import get_alert_manager
                alert_mgr = get_alert_manager()
                if alert_mgr:
                    # Will fire safely if ANALYTICS_TOPIC_ID is configured
                    await alert_mgr.send_daily_analytics_notification(top_users, advanced_metrics)
                    
                    # Send monthly enterprise report if it's the 1st!
                    if is_first_of_month and monthly_data:
                        await alert_mgr.send_monthly_analytics_notification(monthly_data)
                        
                    # Send yearly enterprise report if it's Jan 1st!
                    if is_jan_first and yearly_data:
                        await alert_mgr.send_yearly_analytics_notification(yearly_data)
                    
                try:
                    is_monday = datetime.now(ist).weekday() == 0
                    
                    await self.usage_repo.reset_all_periods(
                        weekly=is_monday,
                        monthly=is_first_of_month,
                        yearly=is_jan_first
                    )
                    
                    if alert_mgr:
                        await alert_mgr.send_warning_notification(
                            warning_message=f"Global usage limits reset. (Daily: True, Weekly: {is_monday}, Monthly: {is_first_of_month})",
                            warning_type="QUOTA RESET SUCCESS",
                            severity="LOW"
                        )
                except Exception as reset_e:
                    logger.error(f"[QUOTA] Failed to reset global usages: {reset_e}")
                    if alert_mgr:
                        await alert_mgr.send_error_notification(
                            error_message=f"Failed to execute midnight quota wipe: {reset_e}",
                            error_type="QUOTA RESET FAILURE",
                            severity="HIGH"
                        )
        except Exception as e:
            logger.error(f"[QUOTA] Error in daily maintenance: {e}")

    def stop(self):
        self._shutdown_flag = True


_quota_manager_instance = None

def get_quota_manager() -> QuotaManager:
    global _quota_manager_instance
    if _quota_manager_instance is None:
        _quota_manager_instance = QuotaManager()
    return _quota_manager_instance

def initialize_quota_manager(db_manager, default_limit: int = 5):
    qm = get_quota_manager()
    qm.initialize(db_manager.db_path, default_limit)
    return qm
