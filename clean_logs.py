import os
import argparse

def clean_logs(apply=False, db=False):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(base_dir, "logs")
    data_dir = os.path.join(base_dir, "data")
    
    files_to_delete = []
    
    # 1. Gather log files
    if os.path.exists(logs_dir):
        for root, _, files in os.walk(logs_dir):
            for file in files:
                if file.endswith(".log"):
                    files_to_delete.append(os.path.join(root, file))
                    
    # 2. Gather db files
    if db:
        db_dir = os.path.join(data_dir, "databases")
        if os.path.exists(db_dir):
            for root, _, files in os.walk(db_dir):
                for file in files:
                    if file.endswith(".db") or file.endswith(".sqlite") or file.endswith(".db-journal"):
                        files_to_delete.append(os.path.join(root, file))
                        
    if not files_to_delete:
        print("No files found to clean.")
        return
        
    print(f"Found {len(files_to_delete)} files to delete:")
    for f in files_to_delete:
        print(f" - {os.path.relpath(f, base_dir)}")
        
    if apply:
        print("\n--- APPLY MODE: Deleting files ---")
        deleted = 0
        for f in files_to_delete:
            try:
                os.remove(f)
                deleted += 1
            except Exception as e:
                print(f"Failed to delete {f}: {e}")
        print(f"Successfully deleted {deleted} files.")
    else:
        print("\n--- DRY RUN: No files were deleted. Use --apply to delete. ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wipe log files and optionally database files.")
    parser.add_argument("--apply", action="store_true", help="Actually delete the files. Without this, it's a dry run.")
    parser.add_argument("--db", action="store_true", help="Also flush/delete the database files.")
    
    args = parser.parse_args()
    clean_logs(apply=args.apply, db=args.db)
