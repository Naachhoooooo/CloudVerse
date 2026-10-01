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

async def archive_previous_session_logs(
    bot,
    chat_id: int,
    topic_id: int,
    provider_name: str,
    session_timestamp: str,
) -> None:
    """
    Called at startup. Finds all log files from the PREVIOUS session (i.e., any
    timestamped .log that is NOT the current session_timestamp), scrubs them,
    zips them, sends the zip to the backup topic, then deletes the scrubbed copies.

    The original timestamped files are kept until the 5-day cleanup prunes them.
    """
    if not chat_id or not topic_id:
        logger.warning("[SYSTEM] archive_previous_session_logs: missing chat_id or topic_id, skipping.")
        return

    import zipfile
    import datetime

    import io
    
    logs_root = Path(__file__).parent.parent.parent / "logs"
    provider_log_dir = logs_root / provider_name

    # Collect all timestamped log files that don't belong to this session
    prev_logs: list[Path] = []
    search_dirs = [logs_root, provider_log_dir]
    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.glob("*_????????_??????.log"):
            if f.is_file() and session_timestamp not in f.name:
                prev_logs.append(f)

    if not prev_logs:
        logger.info(f"[SYSTEM] No previous session logs to archive for {provider_name}.")
        return

    # Pick the most recent previous session timestamp from filenames
    import re
    ts_pattern = re.compile(r"_(\d{8}_\d{6})\.log")
    all_ts = set()
    for f in prev_logs:
        m = ts_pattern.search(f.name)
        if m:
            all_ts.add(m.group(1))

    if not all_ts:
        return

    archive_label = max(all_ts)  # latest previous session
    now = datetime.datetime.now()
    zip_filename = f"CloudVerse_{provider_name.capitalize()}_Logs_{archive_label}.zip"
    
    zip_buffer = io.BytesIO()

    try:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for log_file in prev_logs:
                # Scrub, then add
                try:
                    scrubbed_lines = []
                    with open(log_file, "r", encoding="utf-8", errors="replace") as lf:
                        for line in lf:
                            scrubbed_lines.append(scrub_log_line(line))
                    zf.writestr(f"{log_file.parent.name}/{log_file.name}", "".join(scrubbed_lines))
                except Exception as e:
                    logger.warning(f"[SYSTEM] Could not scrub {log_file.name}: {e}")

        if len(zip_buffer.getvalue()) == 0:
            logger.info(f"[SYSTEM] Archive empty for {provider_name}, skipping send.")
            return

        zip_buffer.seek(0)
        caption = (
            f"<b>📦 CloudVerse {provider_name.capitalize()} — Session Log Archive</b>\n\n"
            f"📅 <b>Session:</b> {archive_label.replace('_', ' ')}\n"
            f"📬 <b>Uploaded:</b> {now.strftime('%d %B %Y %H:%M')}\n"
            f"🗂 <b>Files:</b> {len(prev_logs)} log files (scrubbed)"
        )
        await bot.send_document(
            chat_id=chat_id,
            message_thread_id=topic_id,
            document=zip_buffer,
            filename=zip_filename,
            caption=caption,
            parse_mode="HTML",
        )
        logger.info(f"[SYSTEM] Session log archive sent to backup topic for {provider_name}.")
    except Exception as e:
        logger.error(f"[SYSTEM] Failed to archive/send session logs for {provider_name}: {e}", exc_info=True)


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
        has_files = False
        try:
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                has_files = await add_files_func(zipf)
            
            if has_files and len(zip_buffer.getvalue()) > 0:
                zip_buffer.seek(0)
                await context.bot.send_document(
                    chat_id=chat_id,
                    message_thread_id=topic_id,
                    document=zip_buffer,
                    filename=zip_filename,
                    caption=f"<b>{caption_title}</b>\n\n📅 <b>Date:</b> {now.strftime('%d %B %Y')}",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Failed to auto-forward backup {zip_filename}: {e}", exc_info=True)
                    
    # 1. Bot specific zips (3 zips)
    for provider in providers:
        async def add_provider_files(zipf):
            added = False
            log_dir = Path(__file__).parent.parent.parent / "logs" / provider
            if log_dir.exists():
                for log_file in os.listdir(log_dir):
                    if log_file.endswith('.log') or '.log.' in log_file:
                        if not (log_dir / log_file).is_file(): continue
                        scrubbed_content = await get_scrubbed_log_content(log_file, provider)
                        if scrubbed_content:
                            zipf.writestr(log_file, scrubbed_content)
                            added = True
            db_name = f"cloudverse_{provider if provider != 'gdrive' else 'drive'}.db"
            db_file = Path(__file__).parent.parent.parent / "data" / "databases" / db_name
            if db_file.exists():
                zipf.write(db_file, arcname=db_name)
                added = True
            return added
            
        await create_and_send_zip(f"Cloudverse_{provider.capitalize()}_{timestamp_str}.zip", f"CloudVerse {provider.capitalize()} Backup", add_provider_files)
        
    # 2. Server and Administrator specific zip
    async def add_server_files(zipf):
        added = False
        server_db_path = Path(__file__).parent.parent.parent / "data" / "databases" / "cloudverse_server.db"
        if server_db_path.exists():
            zipf.write(server_db_path, arcname="cloudverse_server.db")
            added = True
            
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
                        added = True
                        
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
                            added = True
        return added
        
    await create_and_send_zip(f"Cloudverse_ServerAdmin_{timestamp_str}.zip", "CloudVerse Server & Admin Backup", add_server_files)

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
