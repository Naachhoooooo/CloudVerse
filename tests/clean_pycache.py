import os
import shutil
import sys
from pathlib import Path

# [REVIEWED] clean | 2026-03-03
def clean_pycache():
    """
    Recursively finds and removes all __pycache__ directories and .pyc files.
    Ensures the codebase remains lean and prevents stale bytecode issues.
    """
    # Project root is one level up from the tests/ directory
    project_root = Path(__file__).parent.parent.absolute()
    
    # Define targets
    targets = ['__pycache__', '.pytest_cache', '.pyc', '.pyo']
    
    print(f"🧹 Initializing codebase cleanup at: {project_root}")
    
    deleted_count = 0
    
    try:
        for root, dirs, files in os.walk(project_root):
            # 1. Remove cache directories
            for d in list(dirs):
                if d in targets:
                    dir_path = Path(root) / d
                    try:
                        shutil.rmtree(dir_path)
                        print(f"  [REMOVED] Directory: {dir_path.relative_to(project_root)}")
                        deleted_count += 1
                    except Exception as e:
                        print(f"  [ERROR] Could not remove {dir_path}: {e}")

            # 2. Remove stray compiled files
            for f in files:
                if any(f.endswith(ext) for ext in ['.pyc', '.pyo', '.pyd']):
                    file_path = Path(root) / f
                    try:
                        file_path.unlink()
                        print(f"  [REMOVED] File: {file_path.relative_to(project_root)}")
                        deleted_count += 1
                    except Exception as e:
                        print(f"  [ERROR] Could not remove {file_path}: {e}")

        print(f"\n✅ Cleanup complete. Total items purged: {deleted_count}")
        
    except KeyboardInterrupt:
        print("\n⚠️ Cleanup aborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Critical failure during cleanup: {e}")
        sys.exit(1)

if __name__ == "__main__":
    clean_pycache()
