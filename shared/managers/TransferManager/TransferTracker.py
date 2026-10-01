"""
TransferTracker — core transfer orchestration engine.

Manages lane-based concurrency, per-user transfer limits, adaptive capacity
scaling, queue registration, and the actual file/URL transfer pipelines.
"""
from typing import Dict, Optional
from datetime import datetime
from time import time
from telegram import Update
from telegram.ext import ContextTypes

from shared.core.Logger import get_logger
from .SystemMonitor import SystemMonitor
from .QueueManager import QueueManager
from .TransferState import TransferState

logger = get_logger(__name__)

# ── Lane capacity constants ──────────────────────────────────────────────────

BASE_PUBLIC_LANE_MAX: int = 3
BASE_PRIVATE_LANE_MAX: int = 2

MAX_PUBLIC_LANE_MAX: int = 10
MAX_PRIVATE_LANE_MAX: int = 4

WAIT_TOKEN_TTL_SECONDS: int = 600
DEFAULT_AVG_TRANSFER_SECONDS: int = 300

# System monitoring thresholds
CPU_THRESHOLD_LOW: float = 30.0
CPU_THRESHOLD_HIGH: float = 60.0
MEMORY_THRESHOLD_LOW: float = 35.0
MEMORY_THRESHOLD_HIGH: float = 70.0
DISK_IO_THRESHOLD_HIGH: float = 60.0

PERFORMANCE_SAMPLE_SIZE: int = 30
CAPACITY_ADJUSTMENT_INTERVAL: int = 20

# ── Singleton accessors for internal subsystems ──────────────────────────────

_system_monitor = None
_queue_manager = None

def get_system_monitor() -> SystemMonitor:
    global _system_monitor
    if _system_monitor is None:
        _system_monitor = SystemMonitor()
    return _system_monitor

def get_queue_manager() -> QueueManager:
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = QueueManager(get_system_monitor())
    return _queue_manager


class TransferTracker:
    def __init__(self, account_repo=None):
       self._state = TransferState()
       self._system = get_system_monitor()
       self._queue = get_queue_manager()
       self._lock = self._state.get_lock()
       # Injected by bot startup via set_account_repo()
       self._account_repo = account_repo
       
       self.MAX_PARALLEL_TRANSFERS = 2

       # User role caching to reduce database calls
       self._user_role_cache: Dict[int, tuple] = {}
       self._role_cache_ttl = 300
       
       # Throttle updates to avoid FloodWait limits
       self._last_progress_update: Dict[str, float] = {}

    def set_account_repo(self, account_repo) -> None:
       """Late-bind account_repo after bot_data is available during startup."""
       self._account_repo = account_repo

    async def _get_user_lane(self, telegram_id: int) -> str:
       try:
           now = time()
           
           if telegram_id in self._user_role_cache:
               role, timestamp = self._user_role_cache[telegram_id]
               if now - timestamp < self._role_cache_ttl:
                   return "private" if role in ["admin", "super_admin"] else "public"
           
           if not self._account_repo:
               logger.debug("[TRANSFER] No account_repo configured, defaulting to public lane")
               return "public"

           is_admin = await self._account_repo.is_admin(telegram_id=telegram_id)
           is_super_admin = await self._account_repo.is_super_admin(telegram_id=telegram_id)
           
           if is_super_admin:
               self._user_role_cache[telegram_id] = ("super_admin", now)
               return "private"
           elif is_admin:
               self._user_role_cache[telegram_id] = ("admin", now)
               return "private"
           else:
               self._user_role_cache[telegram_id] = ("user", now)
               return "public"
               
       except Exception as e:
           logger.warning(f"[TRANSFER] Failed to determine user lane for {telegram_id}, defaulting to public: {e}")
           return "public"

    async def _update_system_metrics(self):
       await self._system.update_system_metrics()

    def _calculate_system_load_factor(self) -> float:
       return self._system.calculate_system_load_factor()

    def _calculate_performance_factor(self) -> float:
       return self._queue._calculate_performance_factor()

    async def _adjust_lane_capacities(self):
       await self._queue.adjust_lane_capacities()

    def _get_lane_limit(self, lane: str) -> int:
       return self._queue.get_lane_limit(lane)

    def get_lane_mode(self) -> str:
       return getattr(self._queue, '_lane_mode', 'auto')

    def set_lane_mode(self, mode: str):
       if hasattr(self._queue, '_lane_mode') and mode in ['auto', 'manual']:
           self._queue._lane_mode = mode

    def set_manual_lane_limit(self, lane: str, limit: int):
       if hasattr(self._queue, '_manual_lane_limits'):
           self._queue._manual_lane_limits[lane] = limit
           if self.get_lane_mode() == 'manual':
               self._queue._current_lane_limits[lane] = limit

    def get_manual_lane_limits(self) -> Dict[str, int]:
        if hasattr(self._queue, '_manual_lane_limits'):
            return self._queue._manual_lane_limits.copy()
        return {}

    async def can_start_transfer(self, telegram_id: int) -> bool:
       async with self._lock:
           await self._adjust_lane_capacities()
           
           user_transfers = self._state.get_user_transfers(telegram_id)
           load_factor = self._calculate_system_load_factor()
           max_user_transfers = max(1, int(self.MAX_PARALLEL_TRANSFERS * (1.0 - load_factor * 0.5)))
           if len(user_transfers) >= max_user_transfers:
               logger.debug(f"User {telegram_id} has {len(user_transfers)} transfers (limit: {max_user_transfers} due to system load)")
               return False

           lane = await self._get_user_lane(telegram_id)
           base_lane_limit = self._get_lane_limit(lane)
           adaptive_lane_limit = max(1, int(base_lane_limit * (1.0 - load_factor * 0.3)))
           current = self._queue.get_active_count(lane)
           if current >= adaptive_lane_limit:
               logger.debug(f"Lane {lane} at capacity: {current}/{adaptive_lane_limit} (base: {base_lane_limit}, load: {load_factor:.3f})")
               return False

           return True

    async def get_active_transfer_count(self, telegram_id: int) -> int:
        async with self._lock:
            return self._state.get_active_count(telegram_id)

    async def start_transfer(self, telegram_id: int, transfer_id: str, file_name: str,
                         file_size: Optional[int] = None, username: str = "Unknown", provider: str = "Unknown") -> bool:
        async with self._lock:
            await self._adjust_lane_capacities()
            
            load_factor = self._calculate_system_load_factor()
            if load_factor >= 0.9:
                logger.warning(f"System overload detected (load: {load_factor:.3f}) - rejecting new transfer for user {telegram_id}")
                return False
            
            user_transfers = self._state.get_user_transfers(telegram_id)
            lane = await self._get_user_lane(telegram_id)
            adaptive_lane_limit = self._queue.get_adaptive_lane_limit(lane)
            current = self._queue.get_active_count(lane)

            max_user_transfers = self._queue.get_max_user_transfers()
            if len(user_transfers) >= max_user_transfers:
                logger.warning(f"TRANSFER REJECTED - User {telegram_id} per-user limit exceeded: {len(user_transfers)}/{max_user_transfers} (lane={lane})")
                return False

            if current >= adaptive_lane_limit:
                total_waiting = sum(len(q) for q in self._queue._lane_waiting_queues.values())
                logger.warning(f"TRANSFER REJECTED - Lane capacity full: {lane} lane {current}/{adaptive_lane_limit}. User {telegram_id} transfer queued. Total waiting: {total_waiting}")
                return False

            self._state.add_transfer(telegram_id, transfer_id, file_name, file_size)
            self._state._transfer_info[transfer_id]['start_time'] = datetime.now()
            self._state._transfer_info[transfer_id]['username'] = username
            self._state._transfer_info[transfer_id]['provider'] = provider
            self._queue.increment_active(lane, transfer_id)

            logger.info(f"TRANSFER STARTED - User {telegram_id} | Lane: {lane} ({current + 1}/{adaptive_lane_limit}) | File: '{file_name}' | ID: {transfer_id}")
            return True

    async def finish_transfer(self, transfer_id: str, success: bool = True):
        async with self._lock:
            transfer_info = self._state.get_transfer_info(transfer_id)
            if not transfer_info:
                logger.warning(f"Attempted to finish unknown transfer {transfer_id}")
                return

            telegram_id = transfer_info.get('telegram_id')
            self._queue.record_transfer_result(success, high_load=False)
            
            start_time = transfer_info.get('start_time')
            end_time = datetime.now()
            duration_seconds = (end_time - start_time).total_seconds() if start_time else 0
            
            lane = self._queue.decrement_active(transfer_id)
            self._state.remove_transfer(transfer_id)
            self._last_progress_update.pop(transfer_id, None)

            file_name = transfer_info.get('file_name', 'unknown')
            file_size_str = f"{transfer_info.get('file_size')/1024/1024:.1f}MB" if transfer_info.get('file_size') else "unknown size"
            
            status_str = "SUCCESS" if success else "FAILED"
            logger.info(
                f"TRANSFER {status_str} - User {telegram_id} | Lane: {lane} "
                f"(now {self._queue.get_active_count(lane)}/{self._get_lane_limit(lane)}) | "
                f"File: '{file_name}' ({file_size_str}) | Duration: {duration_seconds:.1f}s | "
                f"ID: {transfer_id}"
            )
            self._queue.trigger_queue_check()

    async def cancel_transfer(self, transfer_id: str):
        await self.finish_transfer(transfer_id, success=False)

    async def update_transfer_progress(
        self,
        transfer_id: str,
        current_bytes: int,
        total_bytes: int,
        phase_label: str,
        update_message_func,
        phase_start_time=None,
        bot_username: str = "@CloudVerseBot",
    ):
        """Edit the live progress message with accurate per-phase speed and ETA."""
        async with self._lock:
            transfer_info = self._state.get_transfer_info(transfer_id)
            if not transfer_info:
                return

            self._state.update_progress(transfer_id, current_bytes, total_bytes, phase_label)

            percent = (current_bytes / total_bytes) * 100 if total_bytes else 0
            t0 = phase_start_time or transfer_info.get('start_time', datetime.now())
            elapsed = (datetime.now() - t0).total_seconds()
            
            # 5-Second Throttle Logic (Except for 100% completion)
            now = time()
            last_update = self._last_progress_update.get(transfer_id, 0)
            if percent < 100 and (now - last_update) < 5.0:
                return
            self._last_progress_update[transfer_id] = now
            
            telegram_id = transfer_info.get('telegram_id', 'unknown_id')
            username = transfer_info.get('username', 'Unknown')
            provider = transfer_info.get('provider', 'Unknown').capitalize()

            speed_mbs = (current_bytes / elapsed / (1024 * 1024)) if elapsed > 0 else 0
            remaining_bytes = max(total_bytes - current_bytes, 0)
            eta_raw_sec = int(remaining_bytes / (speed_mbs * 1024 * 1024)) if speed_mbs > 0 else 0
            
            if eta_raw_sec >= 60:
                eta_str = f"{eta_raw_sec // 60}m {eta_raw_sec % 60}s"
            else:
                eta_str = f"{eta_raw_sec}s"
                
            logger.info(f"[{phase_label.capitalize()}] [{provider}] {telegram_id} @{username} {current_bytes} of {total_bytes} bytes - {int(percent)} % | Time: {int(elapsed)}s | ETA: {eta_str}")

            filled = int(percent / 10)
            bar = '🟢' * filled + '⚪' * (10 - filled)
            
            def format_size(size_bytes):
                if size_bytes >= 1024 * 1024 * 1024:
                    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
                return f"{size_bytes / (1024 * 1024):.2f} MB"
            
            current_str = format_size(current_bytes)
            total_str = format_size(total_bytes)
            
            full_message = (
                f"<b>{phase_label}:</b> {percent:.2f}%\n"
                f"{bar}\n"
                f"<b>Size:</b> {current_str} of {total_str}\n"
                f"<b>Speed:</b> {speed_mbs:.2f} MB/sec\n"
                f"<b>ETA:</b> {eta_str}\n\n"
                f"<i>Thanks for using {bot_username}</i>"
            )
            
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_upload")]])
            
        # External API call OUTSIDE the lock to prevent blocking state updates
        try:
            await update_message_func(full_message, reply_markup=reply_markup)
        except Exception as e:
            # Catch temporary telegram issues (e.g. unmodified message, transient floodwait)
            logger.error(f"Failed to update progress message for {transfer_id}: {e}", exc_info=True)

    async def get_user_transfers(self, telegram_id: int) -> Dict[str, Dict]:
        async with self._lock:
            user_transfers = self._state.get_user_transfers(telegram_id)
            return {uid: self._state.get_transfer_info(uid) for uid in user_transfers}

    async def get_transfer_info(self, transfer_id: str) -> Optional[Dict]:
        async with self._lock:
            return self._state.get_transfer_info(transfer_id)

    async def cleanup_stale_transfers(self, max_age_hours: int = 4):
        """Removes transfers that have been stuck or abandoned for too long."""
        async with self._lock:
            now = datetime.now()
            max_age_seconds = max_age_hours * 3600
            
            stale_transfer_ids = []
            
            for transfer_id, info in self._state._transfer_info.items():
                start_time = info.get('start_time')
                if start_time:
                    age_seconds = (now - start_time).total_seconds()
                    if age_seconds > max_age_seconds:
                        stale_transfer_ids.append(transfer_id)
            
            for transfer_id in stale_transfer_ids:
                logger.warning(f"[TRANSFER] Cleaning up stale transfer {transfer_id}")
                
                # Decrement lane count in QueueManager
                lane = self._queue.decrement_active(transfer_id)
                # Remove from TransferState
                self._state.remove_transfer(transfer_id)
                self._last_progress_update.pop(transfer_id, None)
                
                if lane:
                    logger.info(f"[TRANSFER] Restored capacity in lane {lane} from stale transfer {transfer_id}")
                    self._queue.trigger_queue_check()
                    try:
                        from shared.managers.AlertManager import get_alert_manager
                        alert_mgr = get_alert_manager()
                        if alert_mgr:
                            import asyncio
                            asyncio.create_task(alert_mgr.send_warning_notification(
                                f"Purged stale transfer {transfer_id} in lane {lane} (stuck for >{max_age_hours}h). Slot restored.",
                                "Stale Transfer Purge",
                                "LOW"
                            ))
                    except Exception as e:
                        logger.debug(f"Failed to send stale purge alert: {e}")

    async def get_system_stats(self) -> Dict:
        async with self._lock:
            return {
                'total_active_transfers': sum(self._queue._lane_active_counts.values()),
                'max_parallel_per_user': self.MAX_PARALLEL_TRANSFERS,
                'tracked_transfers': len(self._state._transfer_info)
            }

    async def get_max_parallel_per_user(self) -> int:
        async with self._lock:
            return self.MAX_PARALLEL_TRANSFERS

    # Transfer orchestration methods
    # Transfer orchestration methods
    async def handle_file_transfer(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
        """Handle complete file transfer from Telegram using centralized modules."""
        from .TransferExecutor import TransferExecutor
        return await TransferExecutor.handle_file_transfer(self, update, ctx)

    async def handle_url_transfer(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
        """Handle complete URL transfer using centralized modules."""
        from .TransferExecutor import TransferExecutor
        return await TransferExecutor.handle_url_transfer(self, update, ctx)

    async def admit_waiting_users(self) -> list[dict]:
       admitted: list[dict] = []
       async with self._lock:
           for lane in ("private", "public"):
               lane_limit = self._queue.get_lane_limit(lane)
               current = self._queue.get_active_count(lane)
               
               while current < lane_limit:
                   item = self._queue.pop_next_waiting(lane)
                   if not item:
                       break
                   admitted.append({
                       'telegram_id': item['telegram_id'],
                       'lane': lane,
                       'transfer_data': item['transfer_data']
                   })
                   current += 1
       return admitted

    def _prune_waiting(self, lane: str):
       self._queue.peek_next_waiting(lane)

    def _get_avg_transfer_seconds(self, lane: str) -> int:
       return DEFAULT_AVG_TRANSFER_SECONDS

    async def get_lane_status_for_user(self, telegram_id: int) -> Dict:
       async with self._lock:
           lane = await self._get_user_lane(telegram_id)
           lane_limit = self._queue.get_lane_limit(lane)
           current = self._queue.get_active_count(lane)
           
           q = self._queue._lane_waiting_queues[lane]
           position = None
           for idx, item in enumerate(q):
               if item['telegram_id'] == telegram_id:
                   position = idx + 1
                   break
           avg_sec = DEFAULT_AVG_TRANSFER_SECONDS
           eta_sec = (position or 0) * avg_sec if position else 0
           return {
               'lane': lane,
               'active_in_lane': current,
               'lane_capacity': lane_limit,
               'waiting_queue_len': len(q),
               'your_position': position,
               'avg_transfer_seconds': avg_sec,
               'eta_seconds': eta_sec,
           }

    async def register_waiting(self, telegram_id: int, transfer_data: Optional[Dict] = None) -> Dict:
       async with self._lock:
           lane = await self._get_user_lane(telegram_id)
           lane_limit = self._queue.get_lane_limit(lane)
           current = self._queue.get_active_count(lane)
           
           idx_map = self._queue._lane_waiting_index_by_user[lane]
           token = idx_map.get(telegram_id)
           now_ts = time()
           
           if token is None:
               token = f"w_{lane}_{telegram_id}_{int(now_ts)}"
               self._queue.add_to_waitlist(telegram_id, transfer_data, lane, token)
               self._queue.trigger_queue_check()
               
           position = None
           q = self._queue._lane_waiting_queues[lane]
           for idx, item in enumerate(q):
               if item['telegram_id'] == telegram_id:
                   position = idx + 1
                   break
                   
           avg_sec = DEFAULT_AVG_TRANSFER_SECONDS
           eta_sec = (position or 1) * avg_sec
           
           return {
               'lane': lane,
               'active_in_lane': current,
               'lane_capacity': lane_limit,
               'waiting_queue_len': len(q),
               'your_position': position,
               'token': token,
               'avg_transfer_seconds': avg_sec,
               'eta_seconds': eta_sec,
           }

    async def get_global_lane_stats(self) -> Dict:
       async with self._lock:
           return {
               "public": {
                   "active": self._queue.get_active_count("public"),
                   "capacity": self._queue.get_lane_limit("public"),
                   "waiting": len(self._queue._lane_waiting_queues["public"])
               },
               "private": {
                   "active": self._queue.get_active_count("private"),
                   "capacity": self._queue.get_lane_limit("private"),
                   "waiting": len(self._queue._lane_waiting_queues["private"])
               }
           }

    async def get_smart_system_stats(self) -> Dict:
       async with self._lock:
           await self._system.update_system_metrics()
           load_factor = self._system.calculate_system_load_factor()
           performance_factor = self._queue._calculate_performance_factor()
           
           total_active = sum(self._queue._lane_active_counts.values())
           total_capacity = sum(self._queue._current_lane_limits.values())
           utilization = (total_active / max(1, total_capacity)) * 100
           
           return {
               'system_metrics': dict(self._system._system_metrics),
               'performance_metrics': {
                   'load_factor': load_factor,
                   'performance_factor': performance_factor,
                   'success_rate': 100.0,
                   'total_completed': self._queue._total_completed_transfers,
                   'total_failed': self._queue._total_failed_transfers,
                   'avg_transfer_time_seconds': DEFAULT_AVG_TRANSFER_SECONDS
               },
               'capacity_metrics': {
                   'total_active_transfers': total_active,
                   'total_capacity': total_capacity,
                   'utilization_percent': utilization,
               },
               'queue_metrics': {
                   'total_waiting': sum(len(q) for q in self._queue._lane_waiting_queues.values()),
               }
           }

    async def log_system_summary(self, force: bool = False):
       async with self._lock:
           await self._system.update_system_metrics()
           total_active = sum(self._queue._lane_active_counts.values())
           total_capacity = sum(self._queue._current_lane_limits.values())
           total_waiting = sum(len(q) for q in self._queue._lane_waiting_queues.values())
           
           logger.info(
               f"SYSTEM SUMMARY - Active: {total_active}/{total_capacity}, "
               f"Waiting: {total_waiting}"
           )


_transfer_tracker = None

def get_transfer_tracker() -> TransferTracker:
   global _transfer_tracker
   if _transfer_tracker is None:
       _transfer_tracker = TransferTracker()
   return _transfer_tracker
