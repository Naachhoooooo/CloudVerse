"""
Drive Bot — main entry point.

All shared UI handlers are imported directly from shared/components/.
Only Drive-specific wiring lives here:
  - DriveServiceProvider (connects TransferManager to Drive's upload/download pipeline)
  - URL transfer handler (Drive-only feature)
  - File/document reception → TransferManager
  - Inline query handler for Drive file search
  - All background manager startup (mirrors Mega/rclone pattern)
"""

import warnings

# ── TransferManager dependency injection (Drive-specific) ────────────────────
from shared.managers.TransferManager import TransferServiceProvider, set_service_provider  # noqa: E402
from shared.managers.TransferManager.TransferTracker import get_transfer_tracker  # noqa: E402
from shared.managers.TransferManager.TransferWorkers import (  # noqa: E402
    queue_admission_worker, upload_tracker_cleanup_task
)
from bots.drive.services.Uploader import get_upload_manager  # noqa: E402


_drive_download_manager = None

class DriveServiceProvider(TransferServiceProvider):
    """Wires Drive's concrete upload/download managers into the generic TransferManager."""

    def get_download_manager(self):
        global _drive_download_manager
        if _drive_download_manager is None:
            from bots.drive.config import BOT_DB_PATH
            from shared.managers.EncryptionManager import get_encryption_manager
            from shared.database.repositories.DriveTransferRepository import DriveTransferRepository
            from shared.managers.TransferManager.Downloader import DownloadManager
            _drive_download_manager = DownloadManager(
                db_path=str(BOT_DB_PATH),
                cipher=get_encryption_manager(),
                transfer_repo_class=DriveTransferRepository,
                log_tag="[DRIVE]"
            )
        return _drive_download_manager

    def get_upload_manager(self):
        return get_upload_manager()

    async def get_drive_service(self, telegram_id):
        from bots.drive.services.DriveService import get_drive_service
        return await get_drive_service(telegram_id)

    async def get_file_link(self, service, file_id):
        from bots.drive.services.DriveService import get_file_link
        return await get_file_link(service, file_id)

    async def delete_file(self, service, file_id):
        from bots.drive.services.DriveService import delete_file
        return await delete_file(service, file_id)


set_service_provider(DriveServiceProvider())
# ─────────────────────────────────────────────────────────────────────────────

from telegram.ext import (  # noqa: E402
    ApplicationBuilder, MessageHandler, filters,
    ContextTypes
)
from telegram import BotCommand, Update  # noqa: E402

from shared.core.Logger import get_logger, setup_logging  # noqa: E402
from shared.core.ErrorHandler import handle_errors  # noqa: E402
from shared.managers.AccessManager import access_required  # noqa: E402
import re
from shared.core.AsyncUtils import track_task  # noqa: E402
from bots.drive.config import (  # noqa: E402
    BOT_TOKEN, SUPER_ADMIN_ID, TeamCloudverse_GROUP_CHAT_ID
)
logger = get_logger(__name__)
warnings.filterwarnings("ignore", category=UserWarning, module="googleapiclient")

if not all([BOT_TOKEN, TeamCloudverse_GROUP_CHAT_ID, SUPER_ADMIN_ID]):
    raise ValueError("Required configuration variables are not set. Check your .env file.")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global PTB error handler — logs, notifies maintenance topic, notifies user."""
    error_str = str(context.error)

    # These are benign Telegram API responses — not real failures.
    # Never alert or message the user for these.
    BENIGN_ERRORS = (
        "Message is not modified",
        "Query is too old",
        "Message can't be deleted",
        "MESSAGE_ID_INVALID",
    )
    if any(phrase in error_str for phrase in BENIGN_ERRORS):
        logger.warning(f"[BOT] Suppressed benign Telegram error: {context.error}")
        return

    logger.error(f"[BOT] Unhandled PTB error: {context.error}", exc_info=context.error)
    try:
        from shared.managers.ServerManager import get_server_manager
        server_manager = get_server_manager()
        if server_manager.app and context.error:
            await server_manager.send_error_notification(error_str, "Bot Handler Error")
    except Exception as e:
        logger.error(f"[BOT] Failed to send error notification: {e}")
    try:
        if update and hasattr(update, "effective_chat") and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ An error occurred. Please try again or type /start.",
            )
    except Exception as e:
        logger.error(f"[BOT] Failed to send error reply: {e}")


async def set_bot_commands(app):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("login", "Login to Google Drive"),
        BotCommand("profile", "View account profile"),
        BotCommand("filemanager", "Open file manager"),
        BotCommand("storage", "View storage details"),
        BotCommand("recyclebin", "Open recycle bin"),
        BotCommand("settings", "Open settings"),
        BotCommand("policy", "Policy"),
        BotCommand("queue", "View upload queue status"),
    ]
    await app.bot.set_my_commands(commands)


# ── Drive-specific handlers ──────────────────────────────────────────────

@handle_errors
@access_required
async def handle_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Receives forwarded files/documents and routes them based on state."""
    
    await get_transfer_tracker().handle_file_transfer(update, ctx)


@handle_errors
@access_required
async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Receives a URL pasted by the user and downloads it to Drive (Drive-only)."""
    await get_transfer_tracker().handle_url_transfer(update, ctx)


@handle_errors
@access_required
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Text message router.
    - If the message is a URL → hand off to handle_url (Drive only).
    - Otherwise → shared handle_user_input (state machine for all bots).
    """
    from shared.core.InputRouter import handle_user_input
    text = update.message.text if update.message else ""
    if text and re.match(r'^https?://', text):
        await handle_url(update, ctx)
        return
    await handle_user_input(update, ctx)


def _initialize_dependencies(app):
    from bots.drive.config import BOT_DB_PATH
    from shared.managers.EncryptionManager import get_encryption_manager
    from shared.core.Config import SERVER_DB_PATH
    from shared.database.repositories.DriveCredentialsRepository import DriveCredentialsRepository
    from shared.database.repositories.DriveTransferRepository import DriveTransferRepository
    from shared.database.repositories.BroadcastRepository import BroadcastRepository
    from shared.database.repositories.SessionRepository import SessionRepository
    from shared.database.repositories.HistoryRepository import HistoryRepository
    from shared.database.repositories.AccountRepository import AccountRepository

    app.bot_data['credential_repo'] = DriveCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
    app.bot_data['transfer_repo'] = DriveTransferRepository(str(BOT_DB_PATH))
    app.bot_data['broadcast_repo'] = BroadcastRepository(str(SERVER_DB_PATH), log_tag="DRIVE")
    app.bot_data['session_repo'] = SessionRepository(str(SERVER_DB_PATH), get_encryption_manager())
    history_repo = HistoryRepository(str(BOT_DB_PATH))
    app.bot_data['history_repo'] = history_repo
    app.bot_data['account_repo'] = AccountRepository(str(BOT_DB_PATH), history_repo=history_repo)
    logger.info("[BOT] All repositories injected for drive.db")
    
    from shared.core.ComponentInitializer import init_shared_components
    init_shared_components(app.bot_data['config'])
    
    from shared.managers.DomainManager import init_domain_manager
    from pathlib import Path
    _assets_dir = Path(__file__).parent.parent.parent.parent / "shared" / "assets"
    init_domain_manager(str(_assets_dir / "AllowedDomains.md"))


def _register_handlers(app):
    # ── Import shared component handlers ────────────────────────────────────────────────
    from shared.core.ComponentInitializer import register_shared_handlers
    register_shared_handlers(app, include_bin=True, include_telethon=True)

    # Message handlers — order matters (PTB processes in registration order)
    # 1. Forwarded/sent files → State Check → Drive upload / Broadcast
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO) & ~filters.COMMAND,
        handle_file
    ))
    # 2. Text → URL detection + state machine
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)
    
    from telegram import Update
    from telegram.ext import TypeHandler
    from shared.core.Tracer import get_trace_middleware
    app.add_handler(get_trace_middleware(), group=-2)


def _setup_lifecycle(app):
    # ── Startup — wire all background managers ──────────────────────────────
    async def _start_core_managers(application):
        from bots.drive.config import BOT_DB_PATH, TeamCloudverse_GROUP_CHAT_ID, Management_TOPIC_ID, SUPER_ADMIN_ID
        from shared.managers.EncryptionManager import get_encryption_manager
        from shared.managers.MaintenanceManager import get_maintenance_manager
        await get_maintenance_manager().initialize(str(application.bot_data['config']['BOT_DB_PATH']))

        from shared.managers.QuotaManager import initialize_quota_manager
        from shared.database.DatabaseConnectionManager import get_db_manager
        db_mgr = get_db_manager(str(application.bot_data['config']['BOT_DB_PATH']))
        initialize_quota_manager(db_mgr, default_limit=application.bot_data['config'].get('DEFAULT_QUOTA', 3))

        from shared.managers.ServerManager import get_server_manager
        server_manager = get_server_manager()
        server_manager.set_application(application)

        from shared.managers.AlertManager import get_alert_manager
        alert_manager = get_alert_manager()
        alert_manager.set_application(application)
        alert_manager.set_bot_name("CloudVerse Drive Bot")

        from shared.managers.AccessManager import start_ban_countdown_manager
        track_task(start_ban_countdown_manager(application, application.bot_data['account_repo'], application.bot_data['history_repo']))

        from shared.core.CallbackDataCache import start_cache_auto_save
        start_cache_auto_save()

        from shared.core.Config import SERVER_DB_PATH
        from shared.managers.SessionManager import initialize_session_manager
        session_manager = initialize_session_manager(
            db_path=str(SERVER_DB_PATH), cipher=get_encryption_manager(), group_chat_id=TeamCloudverse_GROUP_CHAT_ID,
            maintenance_topic_id=Management_TOPIC_ID, super_admin_id=SUPER_ADMIN_ID,
            session_name_prefix="CloudVerse GDriveBot",
        )
        await session_manager.initialize()
        track_task(session_manager.monitor_sessions(application))

        track_task(upload_tracker_cleanup_task())
        get_transfer_tracker().set_account_repo(application.bot_data['account_repo'])
        track_task(queue_admission_worker(application))

        from shared.managers.MemoryManager import start_memory_monitoring
        start_memory_monitoring()

        from shared.managers.LogManager import setup_daily_backups
        from bots.drive.config import BOT_DB_PATH
        setup_daily_backups(
            application, chat_id=application.bot_data['config']['GROUP_CHAT_ID'],
            topic_id=application.bot_data['config']['BACKUP_TOPIC_ID'], provider_name='gdrive',
            db_path=str(BOT_DB_PATH)
        )
        return alert_manager

    def _register_health_probes(application):
        from shared.managers.HealthManager import register_probe, start_health_monitoring
        async def db_probe() -> bool:
            try:
                await application.bot_data['account_repo'].execute_query("SELECT 1")
                return True
            except Exception:
                return False
        async def telegram_probe() -> bool:
            try:
                await application.bot.get_me()
                return True
            except Exception:
                return False
        async def drive_probe() -> bool:
            try:
                return application.bot_data['provider'] is not None
            except Exception:
                return False

        register_probe("drive_db_connection", db_probe, failures_threshold=3)
        register_probe("telegram_api", telegram_probe, failures_threshold=5)
        register_probe("drive_provider", drive_probe, failures_threshold=3)
        start_health_monitoring()

    async def on_startup(application):
        logger.info("Setting bot commands...")
        await set_bot_commands(application)
        import os
        await application.bot_data['account_repo'].sync_super_admins(os.getenv("SUPER_ADMIN_ID"))
        logger.info("✅ Super admins synced from .env")
        alert_manager = await _start_core_managers(application)
        _register_health_probes(application)
        logger.info("✅ Drive bot ready — all managers wired")
        await alert_manager.send_startup_notification()
        logger.info("AlertManager: startup notification sent")
        await application.bot_data['history_repo'].safe_create(
            action_taken="BOT_STARTUP", status="SUCCESS", event_details="Drive bot started successfully."
        )

    app.post_init = on_startup

    async def on_stop(application):
        from shared.managers.AlertManager import get_alert_manager
        # Send shutdown notification while Telegram bot loop / network is still active.
        await get_alert_manager().send_shutdown_notification(reason="Server manually stopped or restarted", initiated_by="System")

    app.post_stop = on_stop

    async def on_shutdown(application):
        logger.info("Shutting down — cleaning up resources...")
        from shared.managers.AccessManager import stop_access_manager
        await stop_access_manager()
        from shared.managers.HealthManager import stop_health_monitoring
        stop_health_monitoring()
        from shared.managers.MemoryManager import stop_memory_monitoring
        stop_memory_monitoring()
        await application.bot_data['history_repo'].safe_create(
            action_taken="BOT_SHUTDOWN", status="SUCCESS", event_details="Drive bot shut down cleanly."
        )
        from shared.database.DatabaseConnectionManager import get_db_manager
        from bots.drive.config import BOT_DB_PATH
        await get_db_manager(str(BOT_DB_PATH)).close_all_async_connections()
        logger.info("Shutdown complete.")

    app.post_shutdown = on_shutdown


def main():
    setup_logging('gdrive')
    logger.info("🚀 Starting CloudVerse Drive Bot...")

    from bots.drive.config import (
        BOT_DB_PATH,
        Access_TOPIC_ID, Flags_TOPIC_ID, Broadcasts_TOPIC_ID, Management_TOPIC_ID, Alerts_TOPIC_ID, Bugs_TOPIC_ID, BACKUP_TOPIC_ID,
        GDRIVE_CLIENT_CONFIG, SCOPES, SUPER_ADMIN_ID, TeamCloudverse_GROUP_CHAT_ID, BOT_TOKEN, ENCRYPTION_KEY, ENCRYPTION_SALT
    )

    from shared.core.Config import SERVER_DB_PATH
    from shared.database.database import drive_init_db, server_init_db
    server_init_db(str(SERVER_DB_PATH))
    drive_init_db(str(BOT_DB_PATH), super_admin_id=SUPER_ADMIN_ID)
    
    from shared.managers.EncryptionManager import initialize_encryption_manager
    initialize_encryption_manager(ENCRYPTION_KEY, ENCRYPTION_SALT)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    from shared.core.Provider import ProviderFactory
    from bots.drive.services.DriveProvider import DriveProvider
    drive_provider = DriveProvider()
    ProviderFactory.register_provider("gdrive", drive_provider)
    app.bot_data['provider'] = drive_provider
    app.bot_data['provider_name'] = "gdrive"

    from bots.drive.config import DEFAULT_QUOTA
    app.bot_data['config'] = {
        'GROUP_CHAT_ID': TeamCloudverse_GROUP_CHAT_ID,
        'SUPER_ADMIN_ID': SUPER_ADMIN_ID,
        'ACCESS_TOPIC_ID': Access_TOPIC_ID,
        'FLAGS_TOPIC_ID': Flags_TOPIC_ID,
        'BROADCASTS_TOPIC_ID': Broadcasts_TOPIC_ID,
        'MANAGEMENT_TOPIC_ID': Management_TOPIC_ID,
        'ALERTS_TOPIC_ID': Alerts_TOPIC_ID,
        'BUGS_TOPIC_ID': Bugs_TOPIC_ID,
        'BACKUP_TOPIC_ID': BACKUP_TOPIC_ID,
        'BOT_DB_PATH': BOT_DB_PATH,
        'DEFAULT_QUOTA': DEFAULT_QUOTA,
    }
    app.bot_data['gdrive_client_config'] = GDRIVE_CLIENT_CONFIG
    app.bot_data['gdrive_scopes'] = SCOPES

    _initialize_dependencies(app)
    _register_handlers(app)
    _setup_lifecycle(app)

    logger.info("🤖 Drive bot polling started")
    app.run_polling()


if __name__ == "__main__":
    main()
