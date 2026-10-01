"""
MemoryManager — environment-aware, traffic-adaptive memory management.

Each bot process (drive, mega, rclone, support) gets its own instance via
get_memory_manager(). The manager auto-detects the host profile at init and
adapts all thresholds, intervals, and cleanup aggressiveness accordingly.

Key design decisions:
  - Thresholds are % of *available* system RAM, not fixed GB values.
  - "laptop" profile (<=16 GB or on battery) uses tighter limits.
  - Active transfer tracking lets us defer non-critical GC during I/O spikes.
  - Emergency cleanup ignores traffic state and always fires.
"""

import gc
import glob
import os
import platform
import tempfile
import threading
import time
import tracemalloc
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

import psutil

from shared.core.Logger import get_logger

logger = get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

_BYTES_PER_GB = 1024 ** 3
_BYTES_PER_MB = 1024 ** 2

# Tracemalloc frame depth — enough for meaningful stack traces without bloat
_TRACEMALLOC_FRAMES = 10

# Transfer-count thresholds for traffic classification
_HIGH_TRAFFIC_THRESHOLD = 4
_IDLE_TRAFFIC_THRESHOLD = 0


# ── Enums & Data Classes ────────────────────────────────────────────────────

class MemoryPressureLevel(Enum):
    """Memory pressure levels for adaptive cleaning strategies."""
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class HostProfile(Enum):
    """Detected host environment classification."""
    LAPTOP = "laptop"
    SERVER = "server"


@dataclass
class MemoryMetrics:
    """Comprehensive memory usage metrics."""
    total_ram: int = 0
    available_ram: int = 0
    used_ram: int = 0
    ram_percentage: float = 0.0
    swap_total: int = 0
    swap_used: int = 0
    swap_percentage: float = 0.0
    process_ram: int = 0
    python_heap: int = 0
    python_objects: int = 0
    gc_collections: int = 0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def ram_usage_gb(self) -> float:
        return self.process_ram / _BYTES_PER_GB

    @property
    def available_ram_gb(self) -> float:
        return self.available_ram / _BYTES_PER_GB

    @property
    def total_ram_gb(self) -> float:
        return self.total_ram / _BYTES_PER_GB


@dataclass
class CleanupTask:
    """Represents a memory cleanup task."""
    name: str
    function: Callable
    priority: int = 1
    last_run: Optional[datetime] = None
    run_count: int = 0
    total_time: float = 0.0
    memory_freed: int = 0
    enabled: bool = True
    # Whether this task is safe to defer during high-traffic windows
    deferrable: bool = True


@dataclass
class HostEnvironment:
    """Immutable snapshot of the detected host environment."""
    profile: HostProfile
    total_ram_gb: float
    cpu_count: int
    on_battery: bool
    os_platform: str
    # Maximum fraction of system RAM this process should consume
    max_ram_fraction: float
    # Derived absolute cap in bytes
    max_process_bytes: int


# ── Host Detection ───────────────────────────────────────────────────────────

def _detect_battery_status() -> bool:
    """Return True if the machine is running on battery power."""
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            # No battery sensor → desktop / server
            return False
        return not battery.power_plugged
    except Exception as exc:
        logger.warning(f"Could not read battery status: {exc}")
        return False


def _detect_host_environment() -> HostEnvironment:
    """
    Probe the OS for RAM, CPU, battery, and classify the host.

    Classification heuristic:
      laptop  → <=16 GB RAM  OR  running on battery
      server  → >16 GB RAM  AND  on AC / no battery
    """
    mem = psutil.virtual_memory()
    total_gb = mem.total / _BYTES_PER_GB
    cpu_count = os.cpu_count() or 1
    on_battery = _detect_battery_status()

    is_laptop = total_gb <= 16.0 or on_battery
    profile = HostProfile.LAPTOP if is_laptop else HostProfile.SERVER

    # Fraction of *total* RAM this single bot process may use
    # Updated to 50% for laptop per user request, and 60% for servers
    max_fraction = 0.50 if is_laptop else 0.60
    max_bytes = int(mem.total * max_fraction)

    env = HostEnvironment(
        profile=profile,
        total_ram_gb=round(total_gb, 2),
        cpu_count=cpu_count,
        on_battery=on_battery,
        os_platform=platform.system(),
        max_ram_fraction=max_fraction,
        max_process_bytes=max_bytes,
    )

    logger.info(
        f"[SYSTEM] Host profile: {profile.value} | "
        f"RAM: {env.total_ram_gb}GB | CPUs: {cpu_count} | "
        f"Battery: {on_battery} | Max process RAM: "
        f"{max_bytes / _BYTES_PER_GB:.2f}GB ({max_fraction:.0%})"
    )
    return env


# ── Traffic State ────────────────────────────────────────────────────────────

class _TrafficTracker:
    """Thread-safe counter for in-flight transfers (uploads + downloads)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: int = 0

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    @property
    def is_high_traffic(self) -> bool:
        return self.active_count >= _HIGH_TRAFFIC_THRESHOLD

    @property
    def is_idle(self) -> bool:
        return self.active_count <= _IDLE_TRAFFIC_THRESHOLD

    def transfer_started(self) -> None:
        with self._lock:
            self._active += 1
        logger.debug(f"Transfer started — active transfers: {self._active}")

    def transfer_finished(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
        logger.debug(f"Transfer finished — active transfers: {self._active}")


# ── Threshold Builder ────────────────────────────────────────────────────────

def _build_pressure_thresholds(profile: HostProfile) -> Dict[MemoryPressureLevel, float]:
    """
    Pressure thresholds expressed as % of max allowed process RAM.

    Laptop uses lower trigger points so cleanup kicks in sooner.
    """
    if profile == HostProfile.LAPTOP:
        return {
            MemoryPressureLevel.NORMAL: 40.0,
            MemoryPressureLevel.WARNING: 55.0,
            MemoryPressureLevel.CRITICAL: 70.0,
            MemoryPressureLevel.EMERGENCY: 85.0,
        }
    return {
        MemoryPressureLevel.NORMAL: 50.0,
        MemoryPressureLevel.WARNING: 65.0,
        MemoryPressureLevel.CRITICAL: 80.0,
        MemoryPressureLevel.EMERGENCY: 92.0,
    }


def _build_cleanup_interval(profile: HostProfile) -> int:
    """Seconds between scheduled cleanup sweeps."""
    return 20 if profile == HostProfile.LAPTOP else 60


def _build_monitor_sleep(profile: HostProfile, emergency: bool) -> int:
    """Seconds the monitor thread sleeps between iterations."""
    if emergency:
        return 3
    return 10 if profile == HostProfile.LAPTOP else 30


# ── Core Manager ─────────────────────────────────────────────────────────────

class MemoryManager:
    """
    Environment-aware, traffic-adaptive memory management.

    Public interface kept backwards-compatible for all existing callers.
    """

    def __init__(
        self,
        cleanup_interval: Optional[int] = None,
        pressure_thresholds: Optional[Dict[MemoryPressureLevel, float]] = None,
        enable_tracemalloc: bool = True,
        max_memory_gb: Optional[float] = None,
    ) -> None:
        # ── Environment detection ─────────────────────────────────────
        self.host_env: HostEnvironment = _detect_host_environment()
        self.traffic = _TrafficTracker()

        # ── Thresholds (caller overrides win) ─────────────────────────
        self.pressure_thresholds = (
            pressure_thresholds
            or _build_pressure_thresholds(self.host_env.profile)
        )
        self.cleanup_interval = cleanup_interval or _build_cleanup_interval(
            self.host_env.profile
        )
        # Allow explicit GB cap, otherwise derive from host detection
        if max_memory_gb is not None:
            self.max_process_bytes = int(max_memory_gb * _BYTES_PER_GB)
        else:
            self.max_process_bytes = self.host_env.max_process_bytes

        self.enable_tracemalloc = enable_tracemalloc
        if self.enable_tracemalloc and not tracemalloc.is_tracing():
            tracemalloc.start(_TRACEMALLOC_FRAMES)

        self._init_state()
        self._register_default_tasks()

        logger.info(
            f"[SYSTEM] MemoryManager initialised | "
            f"profile={self.host_env.profile.value} | "
            f"max_process={self.max_process_bytes / _BYTES_PER_GB:.2f}GB | "
            f"interval={self.cleanup_interval}s"
        )

    def _init_state(self) -> None:
        """Initialise tracking stores and runtime flags."""
        self.cleanup_tasks: List[CleanupTask] = []
        self.memory_history: deque = deque(maxlen=500)
        self.performance_metrics: Dict[str, List[float]] = defaultdict(list)
        self.leak_detection_data: Dict[str, List[int]] = defaultdict(list)
        self.is_running = False
        self._stop_event = threading.Event()
        self.cleanup_thread: Optional[threading.Thread] = None
        self.last_cleanup = datetime.now()
        self.emergency_mode = False

    # ── Task Registration ─────────────────────────────────────────────

    def _register_default_tasks(self) -> None:
        """Register built-in cleanup tasks with appropriate priorities."""
        self.register_cleanup_task(
            "garbage_collection", self._garbage_collection,
            priority=5, deferrable=True,
        )
        self.register_cleanup_task(
            "clear_memory_history", self._clear_memory_history,
            priority=2, deferrable=True,
        )
        self.register_cleanup_task(
            "clear_performance_metrics", self._clear_performance_metrics,
            priority=2, deferrable=True,
        )
        self.register_cleanup_task(
            "clear_leak_detection", self._clear_leak_detection_data,
            priority=3, deferrable=True,
        )
        # Emergency is *never* deferrable
        self.register_cleanup_task(
            "emergency_cleanup", self._emergency_cleanup,
            priority=1, deferrable=False,
        )
        self.register_cleanup_task(
            "temp_files_cleanup", cleanup_temp_files,
            priority=4, deferrable=True,
        )
        self.register_cleanup_task(
            "old_logs_cleanup", cleanup_old_logs,
            priority=3, deferrable=True,
        )

    def register_cleanup_task(
        self, name: str, function: Callable,
        priority: int = 1, deferrable: bool = True,
    ) -> None:
        """Register a new cleanup task."""
        task = CleanupTask(
            name=name, function=function,
            priority=priority, deferrable=deferrable,
        )
        self.cleanup_tasks.append(task)
        self.cleanup_tasks.sort(key=lambda x: x.priority, reverse=True)
        logger.debug(f"Registered cleanup task: {name} (priority={priority}, deferrable={deferrable})")

    def unregister_cleanup_task(self, name: str) -> bool:
        """Unregister a cleanup task by name."""
        for i, task in enumerate(self.cleanup_tasks):
            if task.name == name:
                del self.cleanup_tasks[i]
                logger.debug(f"Unregistered cleanup task: {name}")
                return True
        logger.debug(f"Cleanup task not found for unregister: {name}")
        return False

    # ── Metrics Collection ────────────────────────────────────────────

    def get_memory_metrics(self) -> MemoryMetrics:
        """Collect comprehensive memory usage metrics."""
        try:
            return self._collect_metrics()
        except Exception as exc:
            logger.error(f"Error collecting memory metrics: {exc}", exc_info=True)
            return MemoryMetrics()

    def _collect_metrics(self) -> MemoryMetrics:
        """Internal metrics collection — separated so get_memory_metrics stays small."""
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        process_ram = psutil.Process().memory_info().rss

        python_heap = 0
        python_objects = 0
        if self.enable_tracemalloc and tracemalloc.is_tracing():
            python_heap, _ = tracemalloc.get_traced_memory()
            python_objects = len(gc.get_objects())

        metrics = MemoryMetrics(
            total_ram=memory.total,
            available_ram=memory.available,
            used_ram=memory.used,
            ram_percentage=memory.percent,
            swap_total=swap.total,
            swap_used=swap.used,
            swap_percentage=swap.percent,
            process_ram=process_ram,
            python_heap=python_heap,
            python_objects=python_objects,
            gc_collections=gc.get_count()[0],
        )
        self.memory_history.append(metrics)
        return metrics

    # ── Pressure Assessment ───────────────────────────────────────────

    def get_memory_pressure_level(self) -> MemoryPressureLevel:
        """Determine pressure level as % of this process's allowed RAM cap."""
        metrics = self.get_memory_metrics()
        if self.max_process_bytes <= 0:
            return MemoryPressureLevel.NORMAL

        usage_pct = (metrics.process_ram / self.max_process_bytes) * 100.0
        return self._classify_pressure(usage_pct)

    def _classify_pressure(self, usage_pct: float) -> MemoryPressureLevel:
        """Map a usage percentage to the correct pressure level."""
        if usage_pct >= self.pressure_thresholds[MemoryPressureLevel.EMERGENCY]:
            return MemoryPressureLevel.EMERGENCY
        if usage_pct >= self.pressure_thresholds[MemoryPressureLevel.CRITICAL]:
            return MemoryPressureLevel.CRITICAL
        if usage_pct >= self.pressure_thresholds[MemoryPressureLevel.WARNING]:
            return MemoryPressureLevel.WARNING
        return MemoryPressureLevel.NORMAL

    # ── Cleanup Tasks ─────────────────────────────────────────────────

    def _garbage_collection(self) -> int:
        """Full 3-generation GC sweep."""
        start = time.monotonic()
        initial = len(gc.get_objects())
        collected = sum(gc.collect(gen) for gen in range(3))
        gc.collect()
        freed = initial - len(gc.get_objects())
        elapsed = time.monotonic() - start

        logger.debug(
            f"GC sweep: collected={collected} freed={freed} "
            f"duration={elapsed:.3f}s"
        )
        return freed

    def _clear_memory_history(self) -> int:
        """Trim history deque to keep memory bounded."""
        keep = 30 if self.host_env.profile == HostProfile.LAPTOP else 100
        if len(self.memory_history) <= keep:
            return 0
        old = len(self.memory_history)
        while len(self.memory_history) > keep:
            self.memory_history.popleft()
        freed = old - len(self.memory_history)
        logger.debug(f"Cleared {freed} memory history entries")
        return freed

    def _clear_performance_metrics(self) -> int:
        """Trim performance metric lists."""
        keep = 15 if self.host_env.profile == HostProfile.LAPTOP else 50
        total = 0
        for key, values in self.performance_metrics.items():
            if len(values) > keep:
                old = len(values)
                self.performance_metrics[key] = values[-keep:]
                total += old - len(self.performance_metrics[key])
        if total:
            logger.debug(f"Cleared {total} performance metric entries")
        return total

    def _clear_leak_detection_data(self) -> int:
        """Trim leak detection history lists."""
        keep = 8 if self.host_env.profile == HostProfile.LAPTOP else 20
        total = 0
        for key, values in self.leak_detection_data.items():
            if len(values) > keep:
                old = len(values)
                self.leak_detection_data[key] = values[-keep:]
                total += old - len(self.leak_detection_data[key])
        if total:
            logger.debug(f"Cleared {total} leak detection entries")
        return total

    def _emergency_cleanup(self) -> int:
        """Aggressive cleanup — always runs regardless of traffic state."""
        logger.warning("[SYSTEM] Emergency memory cleanup triggered")
        total = 0
        for _ in range(3):
            total += self._garbage_collection()
        total += self._clear_memory_history()
        total += self._clear_performance_metrics()
        total += self._clear_leak_detection_data()
        logger.warning(f"[SYSTEM] Emergency cleanup freed {total} objects")
        return total

    # ── Cleanup Cycle (traffic-aware) ─────────────────────────────────

    def run_cleanup_cycle(self) -> Dict[str, int]:
        """
        Run cleanup tasks filtered by pressure level and traffic state.

        During high traffic only non-deferrable tasks execute (emergency).
        During idle an extra proactive GC pass runs.
        """
        start = time.monotonic()
        pressure = self.get_memory_pressure_level()
        high_traffic = self.traffic.is_high_traffic
        idle = self.traffic.is_idle

        tasks = self._select_tasks(pressure, high_traffic)

        logger.debug(
            f"Cleanup cycle: pressure={pressure.value} "
            f"traffic={'high' if high_traffic else ('idle' if idle else 'normal')} "
            f"tasks={len(tasks)}"
        )

        results = self._execute_tasks(tasks)

        # Proactive deep GC during idle periods at NORMAL pressure
        if idle and pressure == MemoryPressureLevel.NORMAL:
            proactive = self._garbage_collection()
            results["proactive_idle_gc"] = proactive

        cycle_time = time.monotonic() - start
        self.performance_metrics["cleanup_cycle_time"].append(cycle_time)
        total_freed = sum(results.values())
        self.performance_metrics["memory_freed_per_cycle"].append(total_freed)
        self.last_cleanup = datetime.now()
        self.emergency_mode = pressure == MemoryPressureLevel.EMERGENCY

        if total_freed > 0:
            logger.info(
                f"[SYSTEM] Cleanup cycle: freed={total_freed} "
                f"tasks={len(results)} duration={cycle_time:.3f}s "
                f"pressure={pressure.value}"
            )
        return results

    def _select_tasks(
        self, pressure: MemoryPressureLevel, high_traffic: bool,
    ) -> List[CleanupTask]:
        """Choose which tasks run based on pressure and traffic."""
        min_priority = self._min_priority_for_pressure(pressure)
        selected = []
        for task in self.cleanup_tasks:
            if not task.enabled:
                continue
            if task.priority < min_priority:
                continue
            # During high traffic, skip deferrable tasks unless emergency
            if high_traffic and task.deferrable:
                if pressure != MemoryPressureLevel.EMERGENCY:
                    continue
            selected.append(task)
        return selected

    def _min_priority_for_pressure(self, pressure: MemoryPressureLevel) -> int:
        """Map pressure level to minimum task priority threshold."""
        mapping = {
            MemoryPressureLevel.EMERGENCY: 1,   # run everything
            MemoryPressureLevel.CRITICAL: 2,
            MemoryPressureLevel.WARNING: 3,
            MemoryPressureLevel.NORMAL: 5,
        }
        return mapping.get(pressure, 5)

    def _execute_tasks(self, tasks: List[CleanupTask]) -> Dict[str, int]:
        """Execute a list of cleanup tasks, logging each result."""
        results: Dict[str, int] = {}
        for task in tasks:
            try:
                t0 = time.monotonic()
                freed = task.function()
                elapsed = time.monotonic() - t0

                task.last_run = datetime.now()
                task.run_count += 1
                task.total_time += elapsed
                task.memory_freed += freed
                results[task.name] = freed

                logger.debug(
                    f"Task '{task.name}': freed={freed} duration={elapsed:.3f}s"
                )
            except Exception as exc:
                logger.error(
                    f"Cleanup task '{task.name}' failed: {exc}", exc_info=True
                )
                results[task.name] = 0
        return results

    # ── Traffic API (called by upload/download handlers) ──────────────

    def notify_transfer_start(self) -> None:
        """Call when a file transfer begins."""
        self.traffic.transfer_started()

    def notify_transfer_end(self) -> None:
        """Call when a file transfer completes or fails."""
        self.traffic.transfer_finished()

    @property
    def active_transfer_count(self) -> int:
        return self.traffic.active_count

    # ── Monitoring Thread ─────────────────────────────────────────────

    def start_monitoring(self) -> None:
        """Start the background monitoring/cleanup thread."""
        if self.is_running:
            logger.warning("Memory monitoring already running")
            return
        self.is_running = True
        self._stop_event.clear()
        self.cleanup_thread = threading.Thread(
            target=self._monitoring_loop,
            name="MemoryManager-Monitor",
            daemon=True,
        )
        self.cleanup_thread.start()
        logger.info("[SYSTEM] Memory monitoring started")

    def stop_monitoring(self) -> None:
        """Stop the background monitoring thread."""
        self.is_running = False
        self._stop_event.set()
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=5)
        logger.info("[SYSTEM] Memory monitoring stopped")

    def _monitoring_loop(self) -> None:
        """Main loop: periodic cleanup + emergency guard."""
        while self.is_running:
            try:
                self._monitoring_tick()
            except Exception as exc:
                logger.error(f"Monitoring loop error: {exc}", exc_info=True)

            sleep = _build_monitor_sleep(
                self.host_env.profile, self.emergency_mode
            )
            self._stop_event.wait(timeout=sleep)
            if self._stop_event.is_set():
                break

    def _monitoring_tick(self) -> None:
        """Single iteration of the monitoring loop."""
        elapsed = (datetime.now() - self.last_cleanup).total_seconds()
        if elapsed >= self.cleanup_interval:
            self.run_cleanup_cycle()

        metrics = self.get_memory_metrics()
        if metrics.process_ram > self.max_process_bytes:
            msg = (f"[SYSTEM] Process RAM {metrics.ram_usage_gb:.2f}GB exceeds "
                   f"cap {self.max_process_bytes / _BYTES_PER_GB:.2f}GB — emergency cleanup")
            logger.critical(msg)
            
            try:
                from shared.managers.AlertManager import get_alert_manager
                import asyncio
                alert_coro = get_alert_manager().send_warning_notification(
                    warning_message=f"Process RAM {metrics.ram_usage_gb:.2f}GB exceeds cap {self.max_process_bytes / _BYTES_PER_GB:.2f}GB",
                    warning_type="High Memory Pressure"
                )
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(alert_coro)
                except RuntimeError:
                    asyncio.run(alert_coro)
            except Exception as e:
                logger.error(f"Failed to dispatch memory pressure alert: {e}")

            self._emergency_cleanup()
            self.emergency_mode = True

    # ── Leak Detection (FIXED: now populates leak_detection_data) ─────

    def detect_memory_leaks(self, threshold: int = 100) -> List[Dict]:
        """
        Detect potential memory leaks via tracemalloc snapshots.

        Each call records current allocation sizes keyed by source file
        into self.leak_detection_data so the cleanup trimmer has data to
        work with and trend analysis is possible.
        """
        if not self.enable_tracemalloc or not tracemalloc.is_tracing():
            logger.debug("Leak detection skipped — tracemalloc not active")
            return []
        try:
            return self._run_leak_detection(threshold)
        except Exception as exc:
            logger.error(f"Leak detection failed: {exc}", exc_info=True)
            return []

    def _run_leak_detection(self, threshold: int) -> List[Dict]:
        """Snapshot allocations, record history, and flag potential leaks."""
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics("lineno")

        leaks: List[Dict] = []
        for stat in top_stats[:20]:
            source_key = str(stat.traceback)

            # Persist allocation size so cleanup logic / trend analysis works
            self.leak_detection_data[source_key].append(stat.size)

            if stat.count > threshold:
                leaks.append({
                    "file": stat.traceback.format()[-1] if stat.traceback.format() else "unknown",
                    "size_mb": stat.size / _BYTES_PER_MB,
                    "count": stat.count,
                    "average_size": stat.size / max(stat.count, 1),
                })

        logger.debug(
            f"Leak detection: tracked {len(top_stats)} allocations, "
            f"flagged {len(leaks)} potential leaks"
        )
        return leaks

    # ── Memory Optimization ───────────────────────────────────────────

    def optimize_memory_usage(self) -> Dict[str, int]:
        """Apply memory optimisation heuristics."""
        results: Dict[str, int] = {}

        # Tune GC thresholds — lower gen-0 threshold on laptops for faster collection
        gen0 = 500 if self.host_env.profile == HostProfile.LAPTOP else 700
        gc.set_threshold(gen0, 10, 10)
        results["gc_optimization"] = 1

        collected = gc.collect()
        results["gc_collected"] = collected

        logger.info(
            f"[SYSTEM] Memory optimised: gen0_threshold={gen0} "
            f"gc_collected={collected}"
        )
        return results

    # ── Reporting ─────────────────────────────────────────────────────

    def get_performance_report(self) -> Dict:
        """Build a comprehensive performance/health report."""
        metrics = self.get_memory_metrics()
        pressure = self.get_memory_pressure_level()

        return {
            "current_metrics": self._report_current_metrics(metrics, pressure),
            "performance_metrics": self._report_perf_metrics(),
            "task_statistics": self._report_task_stats(),
            "system_info": self._report_system_info(metrics),
            "environment": self._report_environment(),
        }

    def _report_current_metrics(
        self, m: MemoryMetrics, p: MemoryPressureLevel,
    ) -> Dict:
        return {
            "ram_usage_gb": round(m.ram_usage_gb, 3),
            "ram_percentage": m.ram_percentage,
            "available_ram_gb": round(m.available_ram_gb, 3),
            "python_heap_mb": round(m.python_heap / _BYTES_PER_MB, 2),
            "python_objects": m.python_objects,
            "pressure_level": p.value,
        }

    def _report_perf_metrics(self) -> Dict:
        ct = self.performance_metrics.get("cleanup_cycle_time", [])
        mf = self.performance_metrics.get("memory_freed_per_cycle", [])
        return {
            "average_cycle_time": sum(ct) / len(ct) if ct else 0,
            "average_memory_freed": sum(mf) / len(mf) if mf else 0,
            "total_cleanup_cycles": len(ct),
            "emergency_mode": self.emergency_mode,
            "active_transfers": self.traffic.active_count,
        }

    def _report_task_stats(self) -> Dict:
        stats = {}
        for t in self.cleanup_tasks:
            if t.run_count > 0:
                stats[t.name] = {
                    "run_count": t.run_count,
                    "total_memory_freed": t.memory_freed,
                    "average_time": t.total_time / t.run_count,
                    "last_run": t.last_run.isoformat() if t.last_run else None,
                    "enabled": t.enabled,
                    "deferrable": t.deferrable,
                }
        return stats

    def _report_system_info(self, m: MemoryMetrics) -> Dict:
        return {
            "total_ram_gb": round(m.total_ram_gb, 2),
            "swap_usage_percentage": m.swap_percentage,
            "gc_collections": m.gc_collections,
        }

    def _report_environment(self) -> Dict:
        env = self.host_env
        return {
            "profile": env.profile.value,
            "total_ram_gb": env.total_ram_gb,
            "cpu_count": env.cpu_count,
            "on_battery": env.on_battery,
            "os_platform": env.os_platform,
            "max_ram_fraction": env.max_ram_fraction,
            "max_process_gb": round(env.max_process_bytes / _BYTES_PER_GB, 2),
        }


# ── Singleton Access ─────────────────────────────────────────────────────────

_memory_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """Get or create the global MemoryManager singleton."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager


# ── Module-Level Public API (imported by bots) ───────────────────────────────

def start_memory_monitoring() -> None:
    """Start global memory monitoring."""
    get_memory_manager().start_monitoring()


def stop_memory_monitoring() -> None:
    """Stop global memory monitoring."""
    if _memory_manager:
        _memory_manager.stop_monitoring()


def get_memory_pressure_level() -> MemoryPressureLevel:
    """Get current memory pressure level."""
    return get_memory_manager().get_memory_pressure_level()


def get_memory_metrics() -> MemoryMetrics:
    """Get current memory metrics."""
    return get_memory_manager().get_memory_metrics()


def run_memory_cleanup() -> Dict[str, int]:
    """Run a memory cleanup cycle."""
    return get_memory_manager().run_cleanup_cycle()


def get_memory_report() -> Dict:
    """Get comprehensive memory report."""
    return get_memory_manager().get_performance_report()


def register_cleanup_task(
    name: str, function: Callable, priority: int = 1,
) -> None:
    """Register a cleanup task with the global memory manager."""
    get_memory_manager().register_cleanup_task(name, function, priority)


def detect_memory_leaks(threshold: int = 100) -> List[Dict]:
    """Detect memory leaks using the global memory manager."""
    return get_memory_manager().detect_memory_leaks(threshold)


def optimize_memory() -> Dict[str, int]:
    """Optimize memory usage using the global memory manager."""
    return get_memory_manager().optimize_memory_usage()


def notify_transfer_start() -> None:
    """Notify the manager that a file transfer has started."""
    get_memory_manager().notify_transfer_start()


def notify_transfer_end() -> None:
    """Notify the manager that a file transfer has ended."""
    get_memory_manager().notify_transfer_end()


# ── Convenience Cleanup Functions (module-level, also used as tasks) ─────────

def cleanup_temp_files() -> int:
    """Remove CloudVerse temp files from the system temp directory."""
    temp_dir = tempfile.gettempdir()
    pattern = os.path.join(temp_dir, "cloudverse_*")
    removed = 0

    try:
        for path in glob.glob(pattern):
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    removed += 1
            except OSError as exc:
                logger.debug(f"Could not remove temp file {path}: {exc}")
    except Exception as exc:
        logger.error(f"Error scanning temp files: {exc}", exc_info=True)

    if removed:
        logger.debug(f"Removed {removed} temp files")
    return removed


def cleanup_old_logs() -> int:
    """Remove rotated log files older than 30 days (matches retention policy)."""
    logs_dir = Path(__file__).parent.parent.parent / "logs"
    if not logs_dir.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=30)
    removed = 0

    try:
        for log_file in logs_dir.rglob("*.log.*"):
            try:
                if log_file.stat().st_mtime < cutoff.timestamp():
                    log_file.unlink()
                    removed += 1
            except OSError as exc:
                logger.debug(f"Could not remove old log {log_file}: {exc}")
    except Exception as exc:
        logger.error(f"Error scanning old logs: {exc}", exc_info=True)

    if removed:
        logger.debug(f"Removed {removed} old log files")
    return removed