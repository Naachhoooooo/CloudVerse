import asyncio
from datetime import datetime, timedelta
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
        self.start_daily_reset_schedule()

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

    def start_daily_reset_schedule(self):
        self._shutdown_flag = False
        track_task(self._daily_reset_loop())

    async def _daily_reset_loop(self):
        logger.info("[QUOTA] Started efficient daily quota reset schedule")
        while not self._shutdown_flag:
            try:
                now = datetime.utcnow()
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                seconds_until_midnight = (tomorrow - now).total_seconds()
                
                # Sleep exactly until midnight UTC
                await asyncio.sleep(seconds_until_midnight)
                
                if self._shutdown_flag:
                    break
                    
                if self.usage_repo:
                    # Fetch top users for daily analytics BEFORE reset
                    top_users = await self.usage_repo.get_top_users_daily(10)
                    
                    from shared.managers.AlertManager import get_alert_manager
                    alert_mgr = get_alert_manager()
                    if alert_mgr:
                        # Will fire safely if ALERTS_TOPIC_ID is configured
                        await alert_mgr.send_daily_analytics_notification(top_users)
                        
                    try:
                        await self.usage_repo.reset_daily_all()
                        if alert_mgr:
                            await alert_mgr.send_warning_notification(
                                warning_message="Global daily quotas and bandwidth limits have been successfully reset.",
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
                # Small buffer to avoid double-triggering right at 00:00:00
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[QUOTA] Error in daily reset loop: {e}")
                await asyncio.sleep(60)

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
