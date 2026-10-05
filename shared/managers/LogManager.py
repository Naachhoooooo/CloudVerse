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

async def get_scrubbed_log_content(log_name: str, provider_name: str) -> str:
    """
    Reads a specific local log file, scrubs it line by line for PII and secrets,
    and returns the scrubbed text content as a string.
    """
    if not provider_name:
        logger.error(f"Cannot get scrubbed log {log_name}: provider_name is missing")
        return None

    log_dir = Path(__file__).parent.parent.parent / "logs" / provider_name
    if not log_dir.exists():
        logger.error(f"Log directory not found: {log_dir}")
        return None
        
    log_file = log_dir / log_name
    if not log_file.exists() or not log_file.is_file():
        logger.error(f"Log file not found: {log_file}")
        return None
        
    try:
        scrubbed_content = []
        async with aiofiles.open(log_file, 'r', encoding='utf-8') as f:
            async for line in f:
                scrubbed_content.append(scrub_log_line(line))
                
        return "".join(scrubbed_content)
    except Exception as e:
        logger.error(f"Error scrubbing log {log_name} for provider {provider_name}: {e}")
        return None

# --- log_exporter: Telegram handlers for log export ---
# Removed as per new architecture (daily automated backups only)


# --- Automated Daily Backups ---


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
    if not topic_id or not chat_id:
        return
        
    providers = ['gdrive', 'mega', 'rclone']
    
    import zipfile
    import datetime
    
    now = datetime.datetime.now()
    timestamp_str = now.strftime('%Y%m%d_%H%M%S')
    
    import io
    
    async def create_and_send_zip(zip_filename, caption_title, add_files_func):
        zip_buffer = io.BytesIO()
        file_count = 0
        try:
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                file_count = await add_files_func(zipf)
            
            if file_count > 0 and len(zip_buffer.getvalue()) > 0:
                zip_buffer.seek(0)
                
                session_id = now.strftime('%Y%m%d %H%M%S')
                upload_date = now.strftime('%d %B %Y %H:%M')
                
                caption = (
                    f"📦 <b>CloudVerse {caption_title} — Session Log Archive</b>\n\n"
                    f"📅 <b>Session:</b> {session_id}\n"
                    f"📬 <b>Uploaded:</b> {upload_date}\n"
                    f"🗂 <b>Files:</b> {file_count} log files (scrubbed)"
                )
                
                await context.bot.send_document(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    document=zip_buffer,
                    filename=zip_filename,
                    caption=caption,
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Failed to auto-forward backup {zip_filename}: {e}", exc_info=True)
                    
    # 1. Bot specific zips (3 zips)
    for provider in providers:
        async def add_provider_files(zipf):
            count = 0
            log_dir = Path(__file__).parent.parent.parent / "logs" / provider
            if log_dir.exists():
                for log_file in os.listdir(log_dir):
                    if log_file.endswith('.log') or '.log.' in log_file:
                        if not (log_dir / log_file).is_file(): continue
                        scrubbed_content = await get_scrubbed_log_content(log_file, provider)
                        if scrubbed_content:
                            zipf.writestr(log_file, scrubbed_content)
                            count += 1
            db_name = f"cloudverse_{provider if provider != 'gdrive' else 'drive'}.db"
            db_file = Path(__file__).parent.parent.parent / "data" / "databases" / db_name
            if db_file.exists():
                zipf.write(db_file, arcname=db_name)
                count += 1
            return count
            
        prov_title = 'Gdrive' if provider == 'gdrive' else provider.capitalize()
        await create_and_send_zip(f"{prov_title}_Logs_{timestamp_str}.zip", prov_title, add_provider_files)
        
    # 2. Server and Administrator specific zip
    async def add_server_files(zipf):
        count = 0
        server_db_path = Path(__file__).parent.parent.parent / "data" / "databases" / "cloudverse_server.db"
        if server_db_path.exists():
            zipf.write(server_db_path, arcname="cloudverse_server.db")
            count += 1
            
        root_logs_dir = Path(__file__).parent.parent.parent / "logs"
        
        # Process administrator logs
        admin_log_dir = root_logs_dir / "administrator"
        if admin_log_dir.exists():
            for log_file in os.listdir(admin_log_dir):
                if log_file.endswith('.log') or '.log.' in log_file:
                    if not (admin_log_dir / log_file).is_file(): continue
                    scrubbed_content = await get_scrubbed_log_content(log_file, "administrator")
                    if scrubbed_content:
                        zipf.writestr(f"administrator/{log_file}", scrubbed_content)
                        count += 1
                        
        # Process root system logs
        for root_log in ['database.log', 'errors.log', 'system_actions.log']:
            # For root logs we might have rotated files too, let's grab all matching
            for f in os.listdir(root_logs_dir):
                if f.startswith(root_log.replace('.log', '')) and (f.endswith('.log') or '.log.' in f):
                    log_file_path = root_logs_dir / f
                    if log_file_path.is_file():
                        scrubbed_content = []
                        async with aiofiles.open(log_file_path, 'r', encoding='utf-8') as lf:
                            async for line in lf:
                                scrubbed_content.append(scrub_log_line(line))
                        if scrubbed_content:
                            zipf.writestr(f, "".join(scrubbed_content))
                            count += 1
        return count
        
    await create_and_send_zip(f"ServerAdmin_Logs_{timestamp_str}.zip", "Server & Admin", add_server_files)
    
    # 3. Cleanup old logs (older than 3 days)
    import time
    three_days_ago = time.time() - (3 * 24 * 60 * 60)
    root_logs_dir = Path(__file__).parent.parent.parent / "logs"
    
    def cleanup_dir(dir_path):
        if not dir_path.exists():
            return
        for item in dir_path.iterdir():
            if item.is_file() and (item.name.endswith('.log') or '.log.' in item.name):
                if item.stat().st_mtime < three_days_ago:
                    try:
                        item.unlink()
                        logger.info(f"Deleted old log file: {item.name}")
                    except Exception as e:
                        logger.error(f"Failed to delete old log file {item.name}: {e}")
            elif item.is_dir():
                cleanup_dir(item)

    cleanup_dir(root_logs_dir)
def setup_unified_daily_backup(application: Application, chat_id: int, topic_id: int):
    if not chat_id or not topic_id:
        logger.warning("Missing chat_id or topic_id for unified daily backups.")
        return
    
    job_queue = application.job_queue
    if not job_queue:
        logger.warning("JobQueue not enabled. Unified backups will not run.")
        return
        
    # Schedule for everyday midnight IST
    import datetime
    from datetime import timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    job_queue.run_daily(
        consolidated_daily_backup,
        time=datetime.time(hour=0, minute=0, second=0, tzinfo=ist),
        data={'chat_id': chat_id, 'topic_id': topic_id},
        name="unified_daily_backup"
    )
    logger.info(f"Unified daily backup scheduled to topic {topic_id}")

# Handlers removed
