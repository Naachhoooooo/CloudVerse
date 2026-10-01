import asyncio
import sys
import time
from typing import Callable, Coroutine, Dict, Any, Optional

from shared.core.Logger import get_logger
from shared.managers.AlertManager import get_alert_manager
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)

__all__ = [
    "HealthManager", "get_health_manager",
    "register_probe", "start_health_monitoring", "stop_health_monitoring",
]

class ProbeStatus:
    def __init__(self, name: str, consecutive_failures_threshold: int):
        self.name = name
        self.consecutive_failures_threshold = consecutive_failures_threshold
        self.current_failures = 0
        self.last_check_time: float = 0.0
        self.last_status: bool = True
        self.error_message: str = ""

class HealthManager:
    """
    Active monitor that acts as a 'suicide switch' for the bot.
    Probes external dependencies periodically. If a dependency goes into a 'Zombie'
    state (failing continuously), the bot will gracefully alert the admins and 
    terminate itself (sys.exit(1)), allowing the process watchdog (run_all.py) to restart it.
    """
    def __init__(self, check_interval_seconds: int = 180):
        self.check_interval_seconds = check_interval_seconds
        self.is_running = False
        self._probes: Dict[str, Callable[[], Coroutine[Any, Any, bool]]] = {}
        self._probe_statuses: Dict[str, ProbeStatus] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self._alert_manager = get_alert_manager()
        
    def register_probe(self, name: str, probe_func: Callable[[], Coroutine[Any, Any, bool]], failures_threshold: int = 3):
        """
        Register an async probe function.
        
        Args:
            name: Identifier for the probe (e.g., 'database', 'telegram_api')
            probe_func: Async function returning True (healthy) or False (failing)
            failures_threshold: Number of consecutive failures before triggering suicide (default 3)
        """
        self._probes[name] = probe_func
        self._probe_statuses[name] = ProbeStatus(name, failures_threshold)
        logger.info(f"[HEALTH] Registered probe: {name} (Threshold: {failures_threshold} failures)")
        
    async def _execute_probe(self, name: str) -> bool:
        """Execute a single probe and update its status."""
        probe_func = self._probes[name]
        status_obj = self._probe_statuses[name]
        
        try:
            start_time = time.time()
            is_healthy = await probe_func()
            duration = time.time() - start_time
            
            status_obj.last_check_time = time.time()
            status_obj.last_status = is_healthy
            
            if is_healthy:
                if status_obj.current_failures > 0:
                    logger.info(f"[HEALTH] Probe '{name}' recovered after {status_obj.current_failures} failures.")
                status_obj.current_failures = 0
                status_obj.error_message = ""
            else:
                status_obj.current_failures += 1
                status_obj.error_message = f"Probe returned False. ({duration:.2f}s)"
                logger.warning(f"[HEALTH] Probe '{name}' failed (Failure {status_obj.current_failures}/{status_obj.consecutive_failures_threshold})")
                
            return is_healthy
            
        except Exception as e:
            status_obj.last_check_time = time.time()
            status_obj.last_status = False
            status_obj.current_failures += 1
            status_obj.error_message = str(e)
            logger.error(f"[HEALTH] Probe '{name}' threw exception: {e} (Failure {status_obj.current_failures}/{status_obj.consecutive_failures_threshold})")
            return False

    async def _suicide_sequence(self, failed_probe_name: str, error_details: str):
        """Execute the graceful termination sequence."""
        logger.critical(f"[HEALTH] INITIATING SUICIDE SEQUENCE. Triggered by '{failed_probe_name}'. Details: {error_details}")
        
        # Send Alert
        component = f"HealthCheck: {failed_probe_name}"
        issue = "Dependency became permanently unreachable (Zombie State)."
        details = (
            f"Error details: {error_details}\n"
            "Action: The bot is terminating itself (sys.exit) to allow the process watchdog (run_all.py) to perform a clean restart."
        )
        await self._alert_manager.send_critical_system_alert(component, issue, details)
        
        # Allow time for the alert to actually send before dying
        await asyncio.sleep(2)
        
        # Exit with error code 1 so the watchdog knows it was an abnormal exit and restarts it
        logger.critical("[HEALTH] System exiting now. Goodbye.")
        sys.exit(1)

    async def _monitoring_loop(self):
        """Continuous loop checking all registered probes."""
        logger.info(f"[HEALTH] Monitoring loop started. Interval: {self.check_interval_seconds}s")
        
        while self.is_running:
            await asyncio.sleep(self.check_interval_seconds)
            
            if not self._probes:
                logger.debug("[HEALTH] No probes registered. Skipping check cycle.")
                continue
                
            logger.debug(f"[HEALTH] Running health check cycle across {len(self._probes)} probes...")
            
            for name in list(self._probes.keys()):
                # Execute probe
                await self._execute_probe(name)
                
                # Check for suicide condition
                status_obj = self._probe_statuses[name]
                if status_obj.current_failures >= status_obj.consecutive_failures_threshold:
                    await self._suicide_sequence(name, status_obj.error_message)
                    # If we hit here, sys.exit was called. The loop ends permanently.

    def start_monitoring(self):
        """Start the async monitoring loop."""
        if self.is_running:
             logger.warning("[HEALTH] Monitoring is already running.")
             return
             
        self.is_running = True
        self._monitor_task = track_task(self._monitoring_loop(), name="HealthManager_Monitor")
        logger.info("[HEALTH] HealthManager activated.")
        
    def stop_monitoring(self):
        """Stop the monitoring loop gracefully."""
        self.is_running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        logger.info("[HEALTH] HealthManager deactivated.")

# Global instance
_health_manager_instance: Optional["HealthManager"] = None

def get_health_manager(check_interval_seconds: int = 180) -> HealthManager:
    """Get or create the global HealthManager instance."""
    global _health_manager_instance
    if _health_manager_instance is None:
        _health_manager_instance = HealthManager(check_interval_seconds)
    return _health_manager_instance

def register_probe(name: str, probe_func: Callable[[], Coroutine[Any, Any, bool]], failures_threshold: int = 3):
    """Convenience method to register a probe on the global instance."""
    manager = get_health_manager()
    manager.register_probe(name, probe_func, failures_threshold)

def start_health_monitoring():
    """Convenience method to start monitoring on the global instance."""
    manager = get_health_manager()
    manager.start_monitoring()

def stop_health_monitoring():
    """Convenience method to stop monitoring on the global instance."""
    if _health_manager_instance:
         _health_manager_instance.stop_monitoring()
