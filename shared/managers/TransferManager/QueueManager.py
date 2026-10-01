from typing import Dict, Optional
from collections import deque
from time import time
import asyncio

from shared.core.Logger import get_logger

logger = get_logger(__name__)

# Base lane capacities 
BASE_PUBLIC_LANE_MAX: int = 3  
BASE_PRIVATE_LANE_MAX: int = 2         

# Maximum lane capacities 
MAX_PUBLIC_LANE_MAX: int = 10   
MAX_PRIVATE_LANE_MAX: int = 4          

# Wait Token details
WAIT_TOKEN_TTL_SECONDS: int = 600
DEFAULT_AVG_TRANSFER_SECONDS: int = 300 

class QueueManager:
    def __init__(self, system_monitor):
        self._system_monitor = system_monitor
        self._lane_active_counts: Dict[str, int] = {"public": 0, "private": 0}
        self._transfer_lane: Dict[str, str] = {}
        
        self._lane_waiting_queues: Dict[str, deque] = {"public": deque(), "private": deque()}
        self._lane_waiting_index_by_user: Dict[str, Dict[int, str]] = {"public": {}, "private": {}}
        self._queued_transfers: Dict[str, Dict] = {}
        
        # Dynamic capacity management
        self._current_lane_limits: Dict[str, int] = {
            "public": BASE_PUBLIC_LANE_MAX,
            "private": BASE_PRIVATE_LANE_MAX
        }
        self._lane_mode: str = "auto"
        self._manual_lane_limits: Dict[str, int] = {
            "public": BASE_PUBLIC_LANE_MAX,
            "private": BASE_PRIVATE_LANE_MAX
        }
        
        self._last_capacity_adjustment: float = time()
        self._last_queue_alert: Dict[str, float] = {"public": 0, "private": 0}
        self._queue_alert_threshold: int = 3  
        self.MAX_PARALLEL_TRANSFERS = 2  
        
        self._total_completed_transfers: int = 0
        self._total_failed_transfers: int = 0
        self._queue_event: Optional[asyncio.Event] = None

    @property
    def queue_event(self) -> asyncio.Event:
        if self._queue_event is None:
            self._queue_event = asyncio.Event()
        return self._queue_event
        
    def trigger_queue_check(self) -> None:
        """Trigger the queue worker to run immediately"""
        self.queue_event.set()

    async def adjust_lane_capacities(self) -> None:
        """Dynamically adjust lane capacities based on system load and performance"""
        if self._lane_mode == 'manual':
            for lane in ["public", "private"]:
                self._current_lane_limits[lane] = self._manual_lane_limits.get(
                    lane, BASE_PUBLIC_LANE_MAX if lane == 'public' else BASE_PRIVATE_LANE_MAX
                )
            return

        now = time()
        if now - self._last_capacity_adjustment < 20: # CAPACITY_ADJUSTMENT_INTERVAL
            return
        
        self._last_capacity_adjustment = now
        await self._system_monitor.update_system_metrics()
        
        load_factor = self._system_monitor.calculate_system_load_factor()
        performance_factor = self._calculate_performance_factor()
        
        adjustment_factor = (1.0 - load_factor) * performance_factor
        capacity_changes = {}
        
        for lane in ["public", "private"]:
            base_capacity = BASE_PUBLIC_LANE_MAX if lane == "public" else BASE_PRIVATE_LANE_MAX
            max_capacity = MAX_PUBLIC_LANE_MAX if lane == "public" else MAX_PRIVATE_LANE_MAX
            
            capacity_range = max_capacity - base_capacity
            new_capacity = int(base_capacity + (capacity_range * adjustment_factor))
            
            current_capacity = self._current_lane_limits[lane]
            if new_capacity > current_capacity:
                self._current_lane_limits[lane] = min(max_capacity, current_capacity + 1)
                if self._current_lane_limits[lane] > current_capacity:
                    capacity_changes[lane] = f"+1 ({self._current_lane_limits[lane]})"
            elif new_capacity < current_capacity:
                self._current_lane_limits[lane] = max(base_capacity, current_capacity - 1)
                if self._current_lane_limits[lane] < current_capacity:
                    capacity_changes[lane] = f"-1 ({self._current_lane_limits[lane]})"
        
        if capacity_changes:
            change_details = ", ".join([f"{lane}: {change}" for lane, change in capacity_changes.items()])
            logger.info(
                f"CAPACITY ADJUSTMENT - Load: {load_factor:.3f}, Perf: {performance_factor:.3f}, "
                f"Adj: {adjustment_factor:.3f} | {change_details}"
            )

    def _calculate_performance_factor(self) -> float:
        """Calculate performance factor based on transfer success rates"""
        if not self._total_completed_transfers and not self._total_failed_transfers:
            return 1.0
        
        total_transfers = self._total_completed_transfers + self._total_failed_transfers
        if total_transfers < 10:
            return 1.0
        
        success_rate = self._total_completed_transfers / total_transfers
        if success_rate >= 0.95:
            return 1.0
        elif success_rate >= 0.85:
            return 0.8
        elif success_rate >= 0.70:
            return 0.6
        return 0.4
        
    def get_lane_limit(self, lane: str) -> int:
        """Get current dynamic lane limit"""
        return self._current_lane_limits.get(lane, BASE_PUBLIC_LANE_MAX)
        
    def increment_active(self, lane: str, transfer_id: str) -> None:
        self._transfer_lane[transfer_id] = lane
        self._lane_active_counts[lane] = self._lane_active_counts.get(lane, 0) + 1
        
    def decrement_active(self, transfer_id: str) -> Optional[str]:
        lane = self._transfer_lane.pop(transfer_id, None)
        if lane is not None and lane in self._lane_active_counts:
            self._lane_active_counts[lane] = max(0, self._lane_active_counts[lane] - 1)
        return lane
        
    def get_active_count(self, lane: str) -> int:
        return self._lane_active_counts.get(lane, 0)

    def get_max_user_transfers(self) -> int:
        load_factor = self._system_monitor.calculate_system_load_factor()
        return max(1, int(self.MAX_PARALLEL_TRANSFERS * (1.0 - load_factor * 0.5)))
        
    def get_adaptive_lane_limit(self, lane: str) -> int:
        load_factor = self._system_monitor.calculate_system_load_factor()
        base_limit = self.get_lane_limit(lane)
        return max(1, int(base_limit * (1.0 - load_factor * 0.3)))

    def add_to_waitlist(self, telegram_id: int, transfer_data: Dict, lane: str, token: str) -> int:
        """Add user to waitlist and return position"""
        queue = self._lane_waiting_queues[lane]
        index_map = self._lane_waiting_index_by_user[lane]
        
        if telegram_id in index_map:
            old_token = index_map[telegram_id]
            for i, wait_entry in enumerate(queue):
                if wait_entry['token'] == old_token:
                    del queue[i]
                    break
                    
        entry = {
            'token': token,
            'telegram_id': telegram_id,
            'timestamp': time(),
            'transfer_data': transfer_data
        }
        
        queue.append(entry)
        index_map[telegram_id] = token
        self._queued_transfers[token] = entry
        
        return len(queue)
        
    def peek_next_waiting(self, lane: str) -> Optional[Dict]:
        """Look at next user in queue without removing"""
        queue = self._lane_waiting_queues[lane]
        if not queue:
            return None
            
        current_time = time()
        while queue:
            entry = queue[0]
            if current_time - entry['timestamp'] > WAIT_TOKEN_TTL_SECONDS:
                expired = queue.popleft()
                self._remove_from_indexes(lane, expired)
            else:
                return entry
        return None
        
    def pop_next_waiting(self, lane: str) -> Optional[Dict]:
        """Remove and return next user in queue"""
        entry = self.peek_next_waiting(lane)
        if entry:
            self._lane_waiting_queues[lane].popleft()
            self._remove_from_indexes(lane, entry)
            return entry
        return None
        
    def _remove_from_indexes(self, lane: str, entry: Dict):
        telegram_id = entry['telegram_id']
        token = entry['token']
        if self._lane_waiting_index_by_user[lane].get(telegram_id) == token:
            del self._lane_waiting_index_by_user[lane][telegram_id]
        self._queued_transfers.pop(token, None)

    def record_transfer_result(self, success: bool, high_load: bool = False):
        if success:
            self._total_completed_transfers += 1.5 if high_load else 1
        else:
            self._total_failed_transfers += 1.5 if high_load else 1
