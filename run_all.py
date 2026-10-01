"""
run_all.py — CloudVerse Multi-Bot Orchestrator

Starts all three bots (Drive, Mega, rclone) as isolated OS processes.
Each bot has its own event loop and crash domain — one bot crashing does not
affect the others.

Usage:
    python run_all.py                   # Start all bots
    python run_all.py --bots drive mega # Start only Drive + Mega bots
    python run_all.py --bots drive      # Start only Drive bot

SIGINT / SIGTERM on this orchestrator propagate terminate() to all children.
"""

import sys  # noqa: E402
import signal  # noqa: E402
import time  # noqa: E402
import argparse  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Dict, List  # noqa: E402

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(dotenv_path=project_root / ".env")

# Ensure required directories exist
for d in ['logs', 'data']:
    (project_root / d).mkdir(exist_ok=True)


from shared.core.Logger import get_logger  # noqa: E402
logger = get_logger(__name__)


BOT_REGISTRY: Dict[str, str] = {
    "administrator": "bots/administrator/main.py",
    "drive":  "scripts/run_drive.py",
    "mega":   "scripts/run_mega.py",
    "rclone": "scripts/run_rclone.py",
}

# Seconds to wait before restarting a crashed bot (exponential back-off applied)
RESTART_DELAY_BASE = 5
MAX_RESTART_DELAY = 120
MAX_CRASH_RETRIES = 5  # After this many crashes without recovery, stop restarting


console_lock = threading.Lock()

def _stream_output(proc: subprocess.Popen, ready_event: threading.Event = None):
    """Read output line-by-line and print it with a thread lock to avoid interleaving."""
    for line in iter(proc.stdout.readline, ''):
        with console_lock:
            try:
                sys.stdout.write(line)
            except UnicodeEncodeError:
                sys.stdout.write(line.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding))
            sys.stdout.flush()
        if ready_event and ("polling started" in line.lower() or "is running" in line.lower()):
            ready_event.set()
    proc.stdout.close()

def _start_process(bot_name: str, script: str, wait_for_ready: bool = False) -> subprocess.Popen:
    """Launch a bot script as a child process."""
    cmd = [sys.executable, str(project_root / script)]
    logger.info(f"[{bot_name}] Starting process: {' '.join(cmd)}")
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root) + (os.pathsep + env["PYTHONPATH"] if "PYTHONPATH" in env else "")
    env["PYTHONIOENCODING"] = "utf-8"

    import subprocess
    import os
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd, cwd=str(project_root),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, encoding='utf-8', errors='replace',
        env=env, **kwargs
    )
    
    ready_event = threading.Event() if wait_for_ready else None
    t = threading.Thread(target=_stream_output, args=(proc, ready_event), daemon=True)
    t.start()
    
    if wait_for_ready:
        start_time = time.time()
        while not ready_event.is_set() and time.time() - start_time < 30:
            if proc.poll() is not None:
                break
            time.sleep(0.5)
            
        if ready_event.is_set():
            logger.info(f"[{bot_name}] is fully ready.")
        elif proc.poll() is not None:
            logger.warning(f"[{bot_name}] exited prematurely with code {proc.poll()}.")
        else:
            logger.warning(f"[{bot_name}] took too long to become ready, proceeding to next bot.")
            
    return proc


def _terminate_all(processes: Dict[str, subprocess.Popen]):
    logger.info("Orchestrator shutting down — requesting graceful termination...")
    for name, proc in processes.items():
        if proc.poll() is None:
            logger.info(f"  Requesting shutdown for [{name}] (PID {proc.pid})")
            if sys.platform == "win32":
                import signal
                try:
                    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                except Exception as e:
                    logger.debug(f"Could not send CTRL_BREAK_EVENT: {e}")
            else:
                proc.terminate()
                
    # Give a grace period for them to handle SIGINT/KeyboardInterrupt and shut down
    end_time = time.time() + 15
    for name, proc in processes.items():
        while proc.poll() is None and time.time() < end_time:
            time.sleep(0.5)
            
    for name, proc in processes.items():
        if proc.poll() is None:
            logger.warning(f"  Force-killing [{name}] (PID {proc.pid})")
            proc.kill()
    logger.info("All bots terminated.")


def main(selected_bots: List[str]):
    bots_to_run = {k: v for k, v in BOT_REGISTRY.items() if k in selected_bots}
    if not bots_to_run:
        logger.error(f"No valid bots selected. Choose from: {list(BOT_REGISTRY.keys())}")
        sys.exit(1)

    logger.info(f"🚀 CloudVerse Orchestrator starting bots: {list(bots_to_run.keys())}")

    processes: Dict[str, subprocess.Popen] = {}
    crash_counts: Dict[str, int] = {name: 0 for name in bots_to_run}
    restart_delays: Dict[str, int] = {name: RESTART_DELAY_BASE for name in bots_to_run}

    # Start all bots sequentially, waiting for each to become ready
    for name, script in bots_to_run.items():
        processes[name] = _start_process(name, script, wait_for_ready=True)

    def _signal_handler(sig, frame):
        _terminate_all(processes)
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Per-bot deferred restart timestamps: name → time.time() after which to restart
    pending_restarts: dict = {}

    # Supervision loop — restart crashed bots with non-blocking back-off
    while True:
        time.sleep(2)

        # Check for pending restarts that are ready
        for name in list(pending_restarts.keys()):
            if time.time() >= pending_restarts[name]:
                del pending_restarts[name]
                processes[name] = _start_process(name, bots_to_run[name])
                logger.info(f"[{name}] Restarted (PID {processes[name].pid})")

        for name, proc in list(processes.items()):
            if name in pending_restarts:
                continue  # Already queued for deferred restart
            ret = proc.poll()
            if ret is None:
                continue  # Still running — healthy

            if ret == 0:
                logger.info(f"[{name}] exited gracefully (code 0). Not restarting.")
                del processes[name]
                continue

            crash_counts[name] += 1
            logger.warning(
                f"[{name}] exited with code {ret} "
                f"(crash #{crash_counts[name]}/{MAX_CRASH_RETRIES})"
            )

            if crash_counts[name] >= MAX_CRASH_RETRIES:
                logger.error(
                    f"[{name}] exceeded max crash retries ({MAX_CRASH_RETRIES}). "
                    "Not restarting. Fix the bot and restart the orchestrator."
                )
                del processes[name]
                continue

            delay = min(restart_delays[name], MAX_RESTART_DELAY)
            logger.info(f"[{name}] Scheduling restart in {delay}s...")
            pending_restarts[name] = time.time() + delay
            # Exponential back-off: 5 → 10 → 20 → 40 → 80 → cap at 120
            restart_delays[name] = min(delay * 2, MAX_RESTART_DELAY)

        if not processes and not pending_restarts:
            if any(count >= MAX_CRASH_RETRIES for count in crash_counts.values()):
                logger.error("All bots have exited or exceeded crash limits. Orchestrator exiting.")
                sys.exit(1)
            else:
                logger.info("All active bots have terminated gracefully. Orchestrator exiting.")
                sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CloudVerse Multi-Bot Orchestrator")
    parser.add_argument(
        "--bots",
        nargs="+",
        choices=list(BOT_REGISTRY.keys()),
        help="Which bots to run (legacy list format)",
    )
    for bot in BOT_REGISTRY.keys():
        parser.add_argument(f"--{bot}", action="store_true", help=f"Run the {bot} bot")
        
    args = parser.parse_args()
    
    selected_bots = []
    if args.bots:
        selected_bots.extend(args.bots)
        
    for bot in BOT_REGISTRY.keys():
        if getattr(args, bot):
            selected_bots.append(bot)
            
    selected_bots = list(set(selected_bots))
    
    if not selected_bots:
        selected_bots = list(BOT_REGISTRY.keys())
        
    main(selected_bots)
