"""
check_imports.py — dependency sanity check for all three CloudVerse bots.
Run this if you're not sure whether required packages are installed.
"""

import sys
import shutil

def check(label: str, module: str):
    try:
        __import__(module)
        print(f"  [OK]  {label}")
    except ImportError as e:
        print(f"  [FAIL]  {label}: MISSING ({e})")


print("\n--- Shared / Drive Bot -----------------------------------------")
check("python-telegram-bot", "telegram")
check("google-api-python-client", "googleapiclient")
check("google-auth-oauthlib", "google_auth_oauthlib")
check("cryptography", "cryptography")
check("python-dotenv", "dotenv")
check("psutil", "psutil")
check("reportlab", "reportlab")
check("telethon", "telethon")
check("aiosqlite", "aiosqlite")
check("humanize", "humanize")
check("aiofiles", "aiofiles")
check("aiohttp", "aiohttp")

print("\n--- Mega Bot -----------------------------------------")
check("mega.py", "mega")

print("\n--- rclone Bot -------------------------------------------------")
rclone_path = shutil.which("rclone")
if rclone_path:
    print(f"  [OK]  rclone binary: {rclone_path}")
else:
    print("  [FAIL]  rclone binary: NOT FOUND on PATH (install from https://rclone.org/install/)")

print("\n--- Monorepo structure ---------------------------------")
packages = ["shared", "bots.drive", "bots.mega", "bots.rclone"]
sys.path.insert(0, ".")
for pkg in packages:
    try:
        __import__(pkg)
        print(f"  [OK]  {pkg}")
    except Exception as e:
        print(f"  [FAIL]  {pkg}: {e}")
