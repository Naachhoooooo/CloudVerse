import asyncio
import time
from functools import partial
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

from shared.database.DatabaseConnectionManager import get_db_manager
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.managers.AlertManager import get_alert_manager
from shared.core.AsyncUtils import track_task

# Note: DB_PATH config injection should point directly to the bot's db instances like other singletons
DB_PATH = None

def set_db_path(path):
    global DB_PATH
    DB_PATH = path

logger = get_logger(__name__)

class MaintenanceManager:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(MaintenanceManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._status_cache = {}
        self._last_checked = {}
        self._notice_cache = {}
        self._notice_last_checked = {}
        self.CACHE_TTL = 10  # seconds

    # Database logic
    def _get_repo(self, bot_name: str = None):
        db_path = str(DB_PATH)
        if bot_name:
            from shared.core.config import get_bot_db_path
            db_path = str(get_bot_db_path(bot_name))
        if not db_path:
            raise ValueError("No database path configured for maintenance manager")
        from shared.database.repositories.MaintenanceRepository import MaintenanceRepository
        return MaintenanceRepository(db_path)

    async def get_custom_notice(self, bot_name: str = None):
        cache_key = bot_name or "default"
        now = time.time()
        
        if cache_key in self._notice_cache and (now - self._notice_last_checked.get(cache_key, 0)) < self.CACHE_TTL:
            return self._notice_cache[cache_key]
            
        repo = self._get_repo(bot_name)
        notice = await repo.get_custom_notice()
        
        self._notice_cache[cache_key] = notice
        self._notice_last_checked[cache_key] = now
        return notice

    async def set_custom_notice(self, notice: str, bot_name: str = None):
        repo = self._get_repo(bot_name)
        res = await repo.set_custom_notice(notice)
        
        cache_key = bot_name or "default"
        self._notice_cache[cache_key] = notice
        self._notice_last_checked[cache_key] = time.time()
        return res

    async def initialize(self, db_path: str):
        set_db_path(db_path)
        repo = self._get_repo()
        await repo.initialize_table()

    async def get_status(self, bot_name: str = None) -> bool:
        """Check if maintenance mode is currently enabled (with caching)."""
        if not DB_PATH and not bot_name:
            return False
            
        cache_key = bot_name or "default"
        now = time.time()
        
        if cache_key in self._status_cache and (now - self._last_checked.get(cache_key, 0)) < self.CACHE_TTL:
            return self._status_cache[cache_key]
            
        repo = self._get_repo(bot_name)
        status = await repo.is_maintenance_enabled()
        
        self._status_cache[cache_key] = status
        self._last_checked[cache_key] = now
        return status

    async def get_history(self, bot_name: str = None):
        """Get comprehensive maintenance status info."""
        if not DB_PATH and not bot_name:
            return None
        repo = self._get_repo(bot_name)
        return await repo.get_history()

    async def enable_maintenance(self, enabled_by_telegram_id, enabled_by_username, bot_name: str = None) -> bool:
        """Enable maintenance mode."""
        repo = self._get_repo(bot_name)
        res = await repo.enable_maintenance(enabled_by_telegram_id, enabled_by_username)
        
        cache_key = bot_name or "default"
        self._status_cache[cache_key] = True
        self._last_checked[cache_key] = time.time()
        return res

    async def disable_maintenance(self, disabled_by_telegram_id, disabled_by_username, bot_name: str = None) -> bool:
        """Disable maintenance mode."""
        repo = self._get_repo(bot_name)
        res = await repo.disable_maintenance(disabled_by_telegram_id, disabled_by_username)
        
        cache_key = bot_name or "default"
        self._status_cache[cache_key] = False
        self._last_checked[cache_key] = time.time()
        return res

    async def log_maintenance_alert(self, sent_by_telegram_id, sent_by_username, message_text):
        repo = self._get_repo()
        await repo.log_alert(sent_by_telegram_id, sent_by_username, message_text)

    async def notify_team(self, event_type: str, username: str):
        """Use AlertManager to notify Team CloudVerse of maintenance start/end."""
        alert_manager = get_alert_manager()
        message = f"Maintenance mode has been **{event_type}** by @{username}."
        # Assuming AlertManager has a way to send standard alerts to the admin/team group
        # Alternatively, we just use the raw send via bot if we must, but standard alert is better.
        await alert_manager.send_warning_notification(
            warning_message=message, 
            warning_type="MAINTENANCE STATUS", 
            severity="HIGH" if event_type == "ACTIVATED" else "LOW"
        )

    async def check_access(self, telegram_id, account_repo) -> tuple[bool, str]:
        """Check if a user can access the bot during maintenance."""
        is_maintenance = await self.get_status()
        if not is_maintenance:
            return True, ""
            
        if not account_repo:
            return False, "Maintenance mode active."

        if await account_repo.is_super_admin(telegram_id=telegram_id):
            return True, ""
            
        custom_notice = await self.get_custom_notice()
        if custom_notice:
            notice = custom_notice
        else:
            notice = (
                "🔧 **CloudVerse is currently under maintenance**\n\n"
                "We're performing scheduled updates to improve your experience.\n"
                "Please try again shortly.\n\n"
                "🛡️ **Team CloudVerse**"
            )
        return False, notice

    @handle_errors
    async def broadcast_alert(self, users: list, message_text: str, sender_telegram_id: str, sender_username: str, user_type_name: str, send_func):
        """Rate limited broadcast to specific user groups yielding progress."""
        footer = "🛡️ <b>Team CloudVerse</b>"
        alert_message = f"{message_text}\n\n{footer}"
        
        total_users = len(users)
        successful_sends = 0
        failed_sends = 0
        
        for i, user in enumerate(users):
            try:
                if isinstance(user, dict):
                    user_telegram_id = user.get('telegram_id')
                else:
                    user_telegram_id = user[0]  
                
                await send_func(user_telegram_id, alert_message)
                successful_sends += 1
            except Exception as send_error:
                logger.debug(f"Failed to send alert to user {user_telegram_id}: {send_error}")
                failed_sends += 1
                
            yield {
                "total": total_users,
                "successful": successful_sends,
                "failed": failed_sends,
                "current": i + 1,
                "user_type_name": user_type_name
            }
                
            await asyncio.sleep(0.05) # Rate limit (20 msgs / sec)
                
        await self.log_maintenance_alert(sender_telegram_id, sender_username, f"[{user_type_name}] {message_text[:50]}...")

def get_maintenance_manager() -> MaintenanceManager:
    return MaintenanceManager()
