import psutil
import gc
import os
from time import time
from typing import Dict
from collections import deque

# Assume custom logger is injected or globally accessible via shared
from shared.core.Logger import get_logger

logger = get_logger(__name__)

# OPTIMIZED System Monitoring thresholds
CPU_THRESHOLD_LOW: float = 30.0
CPU_THRESHOLD_HIGH: float = 60.0
MEMORY_THRESHOLD_LOW: float = 35.0
MEMORY_THRESHOLD_HIGH: float = 70.0
DISK_IO_THRESHOLD_HIGH: float = 60.0
DISK_USAGE_THRESHOLD: float = 90.0

class SystemMonitor:
    def __init__(self, gc_interval: int = 300):
        self._last_system_check: float = 0
        self._last_disk_check: float = 0
        self._last_disk_io = None
        self._system_metrics: Dict = {
            'cpu_percent': 0.0,
            'memory_percent': 0.0,
            'disk_io_percent': 0.0,
            'disk_usage_percent': 0.0,
            'load_average': 0.0
        }
        self._system_load_history: deque = deque(maxlen=15)
        self._last_gc_run: float = time()
        self._gc_interval = gc_interval
        
    async def update_system_metrics(self) -> None:
        """Update system resource metrics"""
        try:
            now = time()
            if now - self._last_system_check < 5:  
                return
            
            self._last_system_check = now
            
            # Non-blocking cached CPU check
            self._system_metrics['cpu_percent'] = psutil.cpu_percent(interval=None)
            
            # Memory usage
            memory = psutil.virtual_memory()
            self._system_metrics['memory_percent'] = memory.percent
            
            # Disk space usage
            try:
                disk_usage = psutil.disk_usage('/')
                self._system_metrics['disk_usage_percent'] = disk_usage.percent
                if disk_usage.percent > DISK_USAGE_THRESHOLD:
                    if now - getattr(self, '_last_disk_alert', 0) > 3600:
                        from shared.managers.AlertManager import get_alert_manager
                        alert_mgr = get_alert_manager()
                        if alert_mgr:
                            import asyncio
                            asyncio.create_task(alert_mgr.send_warning_notification(
                                f"Disk Space Exhaustion Warning: {disk_usage.percent}% full on root partition!",
                                "System Resource Warning",
                                "HIGH"
                            ))
                        self._last_disk_alert = now
            except Exception as e:
                logger.debug(f"Could not read disk usage: {e}")
                self._system_metrics['disk_usage_percent'] = 0
            
            # Disk I/O computation
            try:
                disk_io = psutil.disk_io_counters()
                if self._last_disk_io is not None and self._last_disk_check > 0:
                    time_delta = now - self._last_disk_check
                    if time_delta > 0:
                        read_diff = disk_io.read_bytes - self._last_disk_io.read_bytes
                        write_diff = disk_io.write_bytes - self._last_disk_io.write_bytes
                        io_rate = (read_diff + write_diff) / (1024 * 1024 * time_delta) 
                        self._system_metrics['disk_io_percent'] = min(100, (io_rate / 30) * 100)
                self._last_disk_io = disk_io
                self._last_disk_check = now
            except Exception as e:
                logger.debug(f"Could not read disk I/O metrics: {e}")
                self._system_metrics['disk_io_percent'] = 0
                
            # Load averages for Unix-like
            try:
                load_avg = os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0
                cpu_count = psutil.cpu_count() or 1
                self._system_metrics['load_average'] = (load_avg / cpu_count) * 100
            except Exception as e:
                logger.debug(f"Could not read load average: {e}")
                self._system_metrics['load_average'] = 0
                
            # Aggregate system history 
            overall_load = (
                self._system_metrics['cpu_percent'] * 0.3 + 
                self._system_metrics['memory_percent'] * 0.4 + 
                self._system_metrics['disk_io_percent'] * 0.2 +
                self._system_metrics['load_average'] * 0.1
            )
            self._system_load_history.append(overall_load)
            
            # Perform scheduled automated garbage collection
            if now - self._last_gc_run > self._gc_interval:
                gc.collect()
                self._last_gc_run = now
                logger.debug("Performed automated garbage collection for memory stabilization")
                
            # Log critical warning states
            if self._system_metrics['cpu_percent'] >= CPU_THRESHOLD_HIGH:
                logger.warning(f"HIGH CPU LOAD - {self._system_metrics['cpu_percent']:.1f}%")
            elif self._system_metrics['memory_percent'] >= MEMORY_THRESHOLD_HIGH:
                logger.warning(f"HIGH MEMORY USAGE - {self._system_metrics['memory_percent']:.1f}%")
            elif self._system_metrics['disk_io_percent'] >= DISK_IO_THRESHOLD_HIGH:
                logger.warning(f"HIGH DISK I/O - {self._system_metrics['disk_io_percent']:.1f}%")
                
        except Exception as e:
            logger.warning(f"Failed to update system metrics internally: {e}")

    def calculate_system_load_factor(self) -> float:
        """Calculate system load factor (0.0=low load, 1.0=high load)"""
        try:
            cpu = self._system_metrics['cpu_percent']
            memory = self._system_metrics['memory_percent']
            
            cpu_factor = min(1.0, max(0.0, (cpu - CPU_THRESHOLD_LOW) / (CPU_THRESHOLD_HIGH - CPU_THRESHOLD_LOW)))
            cpu_factor = cpu_factor ** 1.5
            memory_factor = min(1.0, max(0.0, (memory - MEMORY_THRESHOLD_LOW) / (MEMORY_THRESHOLD_HIGH - MEMORY_THRESHOLD_LOW)))
            memory_factor = memory_factor ** 1.2
            
            trend_factor = 0.0
            if len(self._system_load_history) >= 3:
                recent_loads = list(self._system_load_history)[-3:]
                if recent_loads[-1] > recent_loads[0]:
                    trend_factor = 0.2
            return min(1.0, max(0.0, cpu_factor * 0.4 + memory_factor * 0.5 + trend_factor)) 
        except Exception as e:
            logger.debug(f"Could not calculate system load factor: {e}")
            return 0.5

    def get_metrics_snapshot(self) -> Dict:
        """Return the current metrics safely"""
        return dict(self._system_metrics)
