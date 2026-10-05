import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable, Coroutine, Dict, List
from shared.core.Logger import get_logger
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)

class ScheduleManager:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(ScheduleManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        # Format: "HH:MM" -> list of callbacks
        self._daily_jobs: Dict[str, List[Callable[[], Coroutine]]] = {}
        self._shutdown_flag = False
        self.ist = timezone(timedelta(hours=5, minutes=30))

    def register_daily_task(self, time_str: str, callback: Callable[[], Coroutine]):
        """Register an async callback to run daily at HH:MM (IST)."""
        if time_str not in self._daily_jobs:
            self._daily_jobs[time_str] = []
        self._daily_jobs[time_str].append(callback)
        logger.info(f"[SCHEDULE] Registered daily task at {time_str} IST for {callback.__name__}")

    def start(self):
        """Start the background scheduler loop."""
        self._shutdown_flag = False
        track_task(self._scheduler_loop())

    def stop(self):
        """Stop the background scheduler loop."""
        self._shutdown_flag = True

    async def _scheduler_loop(self):
        logger.info("[SCHEDULE] Global Enterprise ScheduleManager started")
        while not self._shutdown_flag:
            try:
                now_ist = datetime.now(self.ist)
                current_time_str = now_ist.strftime("%H:%M")
                
                # Trigger any jobs scheduled for this exact minute
                if current_time_str in self._daily_jobs:
                    for callback in self._daily_jobs[current_time_str]:
                        try:
                            # Fire and forget to not block the scheduler
                            track_task(callback())
                        except Exception as e:
                            logger.error(f"[SCHEDULE] Error dispatching task {callback.__name__}: {e}")
                            
                # Sleep until precisely the start of the next minute
                now_ist = datetime.now(self.ist)
                seconds_to_next_minute = 60 - now_ist.second - (now_ist.microsecond / 1_000_000.0)
                await asyncio.sleep(max(0.1, seconds_to_next_minute))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[SCHEDULE] Error in scheduler loop: {e}")
                await asyncio.sleep(60)

def get_schedule_manager() -> ScheduleManager:
    return ScheduleManager()
