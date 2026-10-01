import os
import sys
import time
import subprocess
from pathlib import Path

def get_mtimes(root_dir):
    mtimes = {}
    for root, dirs, files in os.walk(root_dir):
        # Skip hidden directories and common non-code folders to save CPU
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('venv', '__pycache__', 'logs', 'scratch')]
        for file in files:
            if file.endswith('.py'):
                path = os.path.join(root, file)
                try:
                    mtimes[path] = os.stat(path).st_mtime
                except OSError:
                    pass
    return mtimes

def main():
    root_dir = Path(__file__).parent
    print("Starting Development Auto-Reloader...")
    print("Any changes to .py files will instantly restart the bots.\n")
    
    current_mtimes = get_mtimes(root_dir)
    process = None

    def start_bot():
        nonlocal process
        print("\n[DEV] Starting python run_all.py...")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        process = subprocess.Popen([sys.executable, "run_all.py"], cwd=str(root_dir), env=env)

    def stop_bot():
        nonlocal process
        if process:
            print("\n[DEV] File change detected! Restarting bots...")
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process = None

    start_bot()

    try:
        while True:
            time.sleep(1) # Poll every 1 second
            new_mtimes = get_mtimes(root_dir)
            
            # Check if any .py file has changed
            if new_mtimes != current_mtimes:
                current_mtimes = new_mtimes
                stop_bot()
                start_bot()
            
            # If the bot crashed, don't auto-quit. Wait for the user to edit a file.
            if process and process.poll() is not None:
                process = None
    except KeyboardInterrupt:
        print("\n[DEV] Exiting Auto-Reloader...")
        stop_bot()

if __name__ == "__main__":
    main()
