import asyncio
import os
import psutil
import time
from pathlib import Path
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.database.DatabaseConnectionManager import get_db_manager
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)

__all__ = ["ServerManager", "get_server_manager"]

SERVER_DB_PATH = str(Path(__file__).parent.parent.parent / "data" / "databases" / "cloudverse_server.db")

from shared.database.database import server_init_db
server_init_db(SERVER_DB_PATH)

_bandwidth_cache = {}
_cache_last_updated = 0
_cache_ttl = 60

_server_stats_cache = {}
_server_stats_last_updated = 0
_server_stats_ttl = 3

class ServerStatsExtension:
    async def get_current_bandwidth_usage(self, interface=None, sample_duration=1.0):
        try:
            io_counters1 = psutil.net_io_counters(pernic=bool(interface), nowrap=True)
            await asyncio.sleep(sample_duration)
            io_counters2 = psutil.net_io_counters(pernic=bool(interface), nowrap=True)
            
            if interface:
                if interface not in io_counters1 or interface not in io_counters2:
                    logger.warning(f"Interface '{interface}' not found")
                    return 0.0
                bytes_sent = io_counters2[interface].bytes_sent - io_counters1[interface].bytes_sent
                bytes_recv = io_counters2[interface].bytes_recv - io_counters1[interface].bytes_recv
            else:
                bytes_sent = io_counters2.bytes_sent - io_counters1.bytes_sent
                bytes_recv = io_counters2.bytes_recv - io_counters1.bytes_recv
            
            total_bytes = bytes_sent + bytes_recv
            bits_per_second = (total_bytes * 8) / sample_duration
            mbps = bits_per_second / (1024 * 1024)
            return round(mbps, 2)
        except Exception as e:
            logger.error(f"Error measuring bandwidth usage: {str(e)}")
            return 0.0

    async def get_daily_bandwidth_usage(self):
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            from shared.database.repositories.ServerRepository import ServerRepository
            repo = ServerRepository(SERVER_DB_PATH)
            return await repo.get_daily_bandwidth(today)
        except Exception as e:
            logger.error(f"Error reading daily bandwidth usage: {e}")
            return 0.0

    async def update_bandwidth_usage(self, bytes_transferred, bot_name: str = 'unknown', direction: str = 'upload', active_users_increment: int = 0):
        global _cache_last_updated
        try:
            now = time.time()
            today = datetime.now().strftime('%Y-%m-%d')
            mb_transferred = bytes_transferred / (1024 * 1024)
            
            bot = bot_name.lower()
            if bot == 'drive':
                field = 'drive_uploaded' if direction == 'upload' else 'drive_downloaded'
                count_field = 'drive_transfers'
                active_users_field = 'drive_active_users'
            elif bot == 'mega':
                field = 'mega_uploaded' if direction == 'upload' else 'mega_downloaded'
                count_field = 'mega_transfers'
                active_users_field = 'mega_active_users'
            elif bot == 'rclone':
                field = 'rclone_transferred'
                count_field = 'rclone_transfers'
                active_users_field = 'rclone_active_users'
            else:
                return
                
            from shared.database.repositories.ServerRepository import ServerRepository
            repo = ServerRepository(SERVER_DB_PATH)
            await repo.update_bandwidth(today, field, count_field, active_users_field, mb_transferred, active_users_increment)
            
            if now - _cache_last_updated >= 86400:
                await repo.cleanup_old_bandwidth_records(30)
                _cache_last_updated = now

        except Exception as e:
            logger.error(f"Error updating bandwidth usage: {e}")

    async def get_server_stats(self):
        global _server_stats_cache, _server_stats_last_updated
        now = time.time()
        if now - _server_stats_last_updated < _server_stats_ttl and _server_stats_cache:
            return _server_stats_cache
        
        from shared.utils.system_utils import get_cpu_percent, get_memory_percent, get_load_average, get_uptime, get_server_temperature
        
        bandwidth_today = await self.get_daily_bandwidth_usage()
        live_bandwidth = await self.get_current_bandwidth_usage()
        cpu = get_cpu_percent()
        mem = get_memory_percent()
        load = get_load_average()
        uptime = get_uptime()
        temperature = get_server_temperature()
        response_time = self.get_average_latency()
        stats = {"live_bandwidth": live_bandwidth, "bandwidth_today": bandwidth_today, "load": load, "cpu": cpu, "mem": mem, "uptime": uptime, "temperature": temperature, "response_time": response_time}
        _server_stats_cache = stats
        _server_stats_last_updated = now
        return stats

    async def get_bot_stats(self):
        try:
            from shared.managers.TransferManager.TransferTracker import get_transfer_tracker
            tracker = get_transfer_tracker()
            lane_stats = await tracker.get_global_lane_stats()
            
            total_users = 0
            super_admins = 0
            admins = 0
            whitelisted = 0
            blacklisted = 0
            pending = 0
            registered_today = 0
            try:
                from shared.database.DatabaseConnectionManager import _db_managers
                for db_path, db_mgr in _db_managers.items():
                    if "cloudverse_server.db" in db_path or "cloudverse_support.db" in db_path:
                        continue
                    try:
                        row = await db_mgr.execute_async_query(
                            """SELECT 
                                COUNT(*) as total,
                                SUM(CASE WHEN role = 'super_admin' THEN 1 ELSE 0 END) as super_admins,
                                SUM(CASE WHEN role = 'admin' THEN 1 ELSE 0 END) as admins,
                                SUM(CASE WHEN role = 'whitelisted' THEN 1 ELSE 0 END) as whitelisted,
                                SUM(CASE WHEN role = 'blacklisted' THEN 1 ELSE 0 END) as blacklisted,
                                SUM(CASE WHEN role = 'pending' THEN 1 ELSE 0 END) as pending,
                                SUM(CASE WHEN date(requested_at) = date('now') THEN 1 ELSE 0 END) as today
                               FROM cloudverse_accounts
                            """, fetch_one=True)
                        if row:
                            total_users += row['total'] or 0
                            super_admins += row['super_admins'] or 0
                            admins += row['admins'] or 0
                            whitelisted += row['whitelisted'] or 0
                            blacklisted += row['blacklisted'] or 0
                            pending += row['pending'] or 0
                            registered_today += row['today'] or 0
                    except Exception:
                        pass
            except Exception as repo_err: 
                logger.debug(f"Could not fetch total users for stats: {repo_err}")

            return {
                "total_users": total_users,
                "super_admins": super_admins,
                "admins": admins,
                "whitelisted": whitelisted,
                "blacklisted": blacklisted,
                "pending": pending,
                "registered_today": registered_today,
                "lanes": {
                    "public": lane_stats.get("public", {"active": 0, "capacity": 0, "waiting": 0}),
                    "private": lane_stats.get("private", {"active": 0, "capacity": 0, "waiting": 0}),
                }
            }
        except Exception:
            return {"total_users": 0, "super_admins": 0, "admins": 0, "whitelisted": 0, "blacklisted": 0, "pending": 0, "registered_today": 0, "lanes": {"public": {"active": 0, "capacity": 10, "waiting": 0}, "private": {"active": 0, "capacity": 6, "waiting": 0}}}

    def record_latency(self, latency_ms: float):
        if not hasattr(self, '_latency_history'):
            from collections import deque
            self._latency_history = deque(maxlen=50)
        self._latency_history.append(latency_ms)

    def get_average_latency(self) -> float:
        if not hasattr(self, '_latency_history') or not self._latency_history:
            return 0.0
        return sum(self._latency_history) / len(self._latency_history)

class ServerManager(ServerStatsExtension):    
    def __init__(self):
        self.app = None
        self.startup_message_id = None
        self.is_shutting_down = False
        
    def set_application(self, app):
        """
        Set the Telegram application instance
            """
        self.app = app
        
    async def send_error_notification(self, error_message: str, error_type: str = "Runtime Error", severity: str = "HIGH"):
        """
        Delegate error notifications to AlertManager to support legacy callers.
            """
        from shared.managers.AlertManager import get_alert_manager
        return await get_alert_manager().send_error_notification(error_message, error_type, severity)
        
    async def send_startup_notification(self):
        """Delegate startup notifications to AlertManager to support legacy callers."""
        from shared.managers.AlertManager import get_alert_manager
        return await get_alert_manager().send_startup_notification()

    async def _perform_shutdown(self, initiated_by: str):
        try:
            logger.warning(f"Performing server shutdown initiated by {initiated_by}")
            
            # Use AlertManager to store the initiator
            from shared.managers.AlertManager import get_alert_manager
            get_alert_manager().shutdown_initiated_by = initiated_by
            
            # Wait a moment to ensure the message is sent
            await asyncio.sleep(2)
            
            # Set shutdown flag
            self.is_shutting_down = True
            
            # Use PTB's graceful stop mechanism instead of os.kill
            if self.app:
                logger.info("Stopping Telegram application gracefully...")
                self.app.stop_running()
            
            logger.info("Server shutdown complete")
            
            # (Note: send_shutdown_notification and other alerts have been moved to AlertManager.py)
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            # Force exit if normal shutdown fails
            os._exit(1)

_server_manager = None

def get_server_manager():
    global _server_manager
    if _server_manager is None:
        _server_manager = ServerManager()
    return _server_manager


