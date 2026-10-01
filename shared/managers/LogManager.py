import re
import os
import aiofiles
import datetime
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application
from shared.core.Logger import get_logger
logger = get_logger(__name__)

from shared.utils.log_utils import scrub_log_line

async def get_scrubbed_log_file(log_name: str, provider_name: str) -> str:
    """
    Reads a specific local log file, scrubs it line by line for PII and secrets,
    and returns the path to the scrubbed text file.
    """
    if not provider_name:
        logger.error(f"Cannot get scrubbed log {log_name}: provider_name is missing")
        return None

    log_dir = Path(__file__).parent.parent.parent / "logs" / provider_name
    if not log_dir.exists():
        logger.error(f"Log directory not found: {log_dir}")
        return None
        
    export_dir = log_dir / "exports"
    export_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / log_name
    if not log_file.exists() or not log_file.is_file():
        logger.error(f"Log file not found: {log_file}")
        return None
        
    scrubbed_path = export_dir / f"scrubbed_{log_name}"
    
    try:
        scrubbed_content = []
        async with aiofiles.open(log_file, 'r', encoding='utf-8') as f:
            async for line in f:
                scrubbed_content.append(scrub_log_line(line))
                
        async with aiofiles.open(scrubbed_path, 'w', encoding='utf-8') as f:
            await f.write("".join(scrubbed_content))
            
        return str(scrubbed_path)
    except Exception as e:
        logger.error(f"Error scrubbing log {log_name} for provider {provider_name}: {e}")
        return None


# --- log_exporter: Telegram handlers for log export ---
# Removed as per new architecture (daily automated backups only)


# --- Automated Daily Backups ---

async def forward_daily_backups(context: ContextTypes.DEFAULT_TYPE):
    provider_name = context.job.data.get('provider_name')
    topic_id = context.job.data.get('topic_id')
    chat_id = context.job.data.get('chat_id')
    db_path = context.job.data.get('db_path')
    if not provider_name or not topic_id or not chat_id:
        return
        
    logs_to_export = ['bot.log', 'errors.log', 'events.log', 'auth.log', 'transfers.log', 'system_actions.log']
    scrubbed_paths = []
    
    for log_name in logs_to_export:
        scrubbed_path = await get_scrubbed_log_file(log_name, provider_name)
        if scrubbed_path and os.path.exists(scrubbed_path) and os.path.getsize(scrubbed_path) > 0:
            scrubbed_paths.append(scrubbed_path)
            
    import zipfile
    zip_dir = Path(__file__).parent.parent.parent / "logs" / provider_name / "exports"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / f"daily_backup_{provider_name}.zip"
    
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for spath in scrubbed_paths:
                zipf.write(spath, arcname=f"logs/{os.path.basename(spath)}")
                
            # Add database if exists
            if db_path and os.path.exists(db_path):
                zipf.write(db_path, arcname=f"database/{os.path.basename(db_path)}")
                
        import datetime
        now = datetime.datetime.now()
        timestamp_str = now.strftime('%Y%m%d')
        filename = f"CloudVerse_{provider_name.capitalize()}_Backup_{timestamp_str}.zip"
        
        with open(zip_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                message_thread_id=topic_id,
                document=f,
                filename=filename,
                caption=f"<b>CloudVerse {provider_name.capitalize()} Bot - Daily Backup</b>\n\n📅 <b>Date:</b> {now.strftime('%d %B %Y')}\n📦 <b>Contains:</b> Database & Logs",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Failed to auto-forward daily backup: {e}", exc_info=True)
    finally:
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        for spath in scrubbed_paths:
            if os.path.exists(spath):
                try:
                    os.remove(spath)
                except Exception:
                    pass

def setup_daily_backups(application: Application, chat_id: int, topic_id: int, provider_name: str, db_path: str):
    if not chat_id or not topic_id:
        logger.warning("Missing chat_id or topic_id for daily backups.")
        return
    
    job_queue = application.job_queue
    if not job_queue:
        logger.warning("JobQueue not enabled. Daily backups will not run.")
        return

# --- Automated Daily Backups ---

async def consolidated_daily_backup(context: ContextTypes.DEFAULT_TYPE):
    topic_id = context.job.data.get('topic_id')
    chat_id = context.job.data.get('chat_id')
    admin_db_path = context.job.data.get('db_path')
    if not topic_id or not chat_id:
        return
        
    providers = ['gdrive', 'mega', 'rclone']
    
    import zipfile
    import datetime
    
    now = datetime.datetime.now()
    timestamp_str = now.strftime('%Y%m%d%H%M%S')
    filename = f"Cloudverse_{timestamp_str}.zip"
    
    zip_dir = Path(__file__).parent.parent.parent / "logs" / "exports"
    zip_dir.mkdir(parents=True, exist_ok=True)
    zip_path = zip_dir / filename
    
    files_to_delete = []
    
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 1. Process Providers (drive, mega, rclone)
            for provider in providers:
                # Add logs
                log_dir = Path(__file__).parent.parent.parent / "logs" / provider
                if log_dir.exists():
                    for log_file in os.listdir(log_dir):
                        # Include active and rotated logs
                        if log_file.endswith('.log') or '.log.' in log_file:
                            # Skip directories
                            if not (log_dir / log_file).is_file():
                                continue
                            scrubbed_path = await get_scrubbed_log_file(log_file, provider)
                            if scrubbed_path and os.path.exists(scrubbed_path) and os.path.getsize(scrubbed_path) > 0:
                                zipf.write(scrubbed_path, arcname=f"{'drive' if provider == 'gdrive' else provider}/{log_file}")
                                files_to_delete.append(scrubbed_path)
                                
                # Add DB
                db_name = f"cloudverse_{provider if provider != 'gdrive' else 'drive'}.db"
                db_file = Path(__file__).parent.parent.parent / "data" / "databases" / db_name
                if db_file.exists():
                    zipf.write(db_file, arcname=f"{'drive' if provider == 'gdrive' else provider}/{db_name}")

            # 2. Add Server Tracking DB
            server_db_path = Path(__file__).parent.parent.parent / "data" / "databases" / "cloudverse_server.db"
            if server_db_path.exists():
                zipf.write(server_db_path, arcname="cloudverse_server.db")

            # 3. Add Server Logs (root logs)
            root_logs_dir = Path(__file__).parent.parent.parent / "logs"
            for root_log in ['database.log', 'errors.log', 'system_actions.log']:
                log_file_path = root_logs_dir / root_log
                if log_file_path.exists() and log_file_path.is_file():
                    scrubbed_content = []
                    async with aiofiles.open(log_file_path, 'r', encoding='utf-8') as f:
                        async for line in f:
                            scrubbed_content.append(scrub_log_line(line))
                    
                    scrubbed_root = root_logs_dir / f"scrubbed_{root_log}"
                    async with aiofiles.open(scrubbed_root, 'w', encoding='utf-8') as f:
                        await f.write("".join(scrubbed_content))
                        
                    zipf.write(scrubbed_root, arcname=root_log)
                    files_to_delete.append(str(scrubbed_root))

        with open(zip_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=chat_id,
                message_thread_id=topic_id,
                document=f,
                filename=filename,
                caption=f"<b>CloudVerse Unified System Backup</b>\n\n📅 <b>Date:</b> {now.strftime('%d %B %Y')}\n📦 <b>Contains:</b> All Bots Databases & Logs",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Failed to auto-forward unified backup: {e}", exc_info=True)
    finally:
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        for spath in files_to_delete:
            if os.path.exists(spath):
                try:
                    os.remove(spath)
                except Exception:
                    pass

def setup_unified_daily_backup(application: Application, chat_id: int, topic_id: int):
    if not chat_id or not topic_id:
        logger.warning("Missing chat_id or topic_id for unified daily backups.")
        return
    
    job_queue = application.job_queue
    if not job_queue:
        logger.warning("JobQueue not enabled. Unified backups will not run.")
        return
        
    # Schedule for everyday midnight
    import datetime
    job_queue.run_daily(
        consolidated_daily_backup,
        time=datetime.time(hour=0, minute=0, second=0),
        data={'chat_id': chat_id, 'topic_id': topic_id},
        name="unified_daily_backup"
    )
    logger.info(f"Unified daily backup scheduled to topic {topic_id}")

# Handlers removed
