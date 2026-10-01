"""
Mega Bot — main entry point.

Registers ALL shared component handlers (same UI as Drive bot) plus
Mega-specific handlers (upload via forwarded file, Mega login/logout).
Provider-specific logic lives in bots/mega/services/MegaProvider.py.
Mirrors Drive bot's startup pattern: all shared managers wired.
"""

import warnings
from telegram.ext import (
    ApplicationBuilder, MessageHandler, filters
)
from telegram import BotCommand, Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger, setup_logging
from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import access_required
from bots.mega.config import (
    MEGA_BOT_TOKEN, MEGA_SUPER_ADMIN_ID,
    ENCRYPTION_KEY, ENCRYPTION_SALT
)

from shared.managers.TransferManager import TransferServiceProvider, set_service_provider
from bots.mega.services.Uploader import get_upload_manager
from shared.core.AsyncUtils import track_task

_mega_download_manager = None

class MegaServiceProvider(TransferServiceProvider):
    def get_download_manager(self):
        global _mega_download_manager
        if _mega_download_manager is None:
            from bots.mega.config import BOT_DB_PATH
            from shared.managers.EncryptionManager import get_encryption_manager
            from shared.database.repositories.MegaTransferRepository import MegaTransferRepository
            from shared.managers.TransferManager.Downloader import DownloadManager
            _mega_download_manager = DownloadManager(
                db_path=str(BOT_DB_PATH),
                cipher=get_encryption_manager(),
                transfer_repo_class=MegaTransferRepository,
                log_tag="[MEGA]"
            )
        return _mega_download_manager

    def get_upload_manager(self):
        return get_upload_manager()

    def _get_provider(self):
        from shared.core.Provider import ProviderFactory
        return ProviderFactory.get_provider("mega")

    async def get_drive_service(self, telegram_id):
        provider = self._get_provider()
        return await provider.get_service(telegram_id)

    async def get_file_link(self, service, file_id):
        provider = self._get_provider()
        return await provider.get_file_link(service, file_id)

    async def delete_file(self, service, file_id):
        provider = self._get_provider()
        return await provider.delete_file(service, file_id)

set_service_provider(MegaServiceProvider())

logger = get_logger(__name__)
warnings.filterwarnings("ignore", category=UserWarning)

if not MEGA_BOT_TOKEN:
    raise ValueError("MEGA_BOT_TOKEN is not set. Check your .env file.")
if not MEGA_SUPER_ADMIN_ID:
    raise ValueError("MEGA_SUPER_ADMIN_ID is not set. Check your .env file.")


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
        BotCommand("start", "Start the Mega bot"),
        BotCommand("login", "Link your Mega.nz account"),
        BotCommand("profile", "View account profile"),
        BotCommand("filemanager", "Browse Mega files"),
        BotCommand("storage", "View Mega storage details"),
        BotCommand("settings", "Bot settings"),
        BotCommand("recyclebin", "Open recycle bin"),
        BotCommand("policy", "Policy"),
        BotCommand("queue", "View upload queue status"),
    ]
    await app.bot.set_my_commands(commands)
    
@handle_errors
@access_required
async def handle_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Receives forwarded files/documents and routes them based on state."""
    
    from shared.managers.TransferManager.TransferTracker import get_transfer_tracker
    await get_transfer_tracker().handle_file_transfer(update, ctx)


def _initialize_dependencies(app):
    from bots.mega.config import (
        BOT_DB_PATH
    )
    from shared.managers.EncryptionManager import get_encryption_manager
    from shared.core.Config import SERVER_DB_PATH
    from shared.database.repositories.MegaCredentialsRepository import MegaCredentialsRepository
    from shared.database.repositories.MegaTransferRepository import MegaTransferRepository
    from shared.database.repositories.BroadcastRepository import BroadcastRepository
    from shared.database.repositories.SessionRepository import SessionRepository
    from shared.database.repositories.HistoryRepository import HistoryRepository
    from shared.database.repositories.AccountRepository import AccountRepository
    
    app.bot_data['credential_repo'] = MegaCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
    app.bot_data['transfer_repo'] = MegaTransferRepository(str(BOT_DB_PATH))
    app.bot_data['broadcast_repo'] = BroadcastRepository(str(SERVER_DB_PATH), log_tag="MEGA")
    app.bot_data['session_repo'] = SessionRepository(str(SERVER_DB_PATH), get_encryption_manager())
    history_repo = HistoryRepository(str(BOT_DB_PATH))
    app.bot_data['history_repo'] = history_repo
    app.bot_data['account_repo'] = AccountRepository(str(BOT_DB_PATH), history_repo=history_repo)
    logger.info("[BOT] All repositories injected for mega.db")

    from shared.core.ComponentInitializer import init_shared_components
    init_shared_components(app.bot_data['config'])

    from shared.managers.DomainManager import init_domain_manager
    from pathlib import Path
    _assets_dir = Path(__file__).parent.parent.parent.parent / "shared" / "assets"
    init_domain_manager(str(_assets_dir / "AllowedDomains.md"))

def _register_handlers(app):
    from shared.core.ComponentInitializer import register_shared_handlers
    register_shared_handlers(app, include_bin=True, include_telethon=True)

    from shared.core.InputRouter import handle_user_input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_input))
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO) & ~filters.COMMAND,
        handle_media
    ))

    app.add_error_handler(error_handler)
    from telegram.ext import TypeHandler
    from shared.core.Tracer import get_trace_middleware
    app.add_handler(get_trace_middleware(), group=-2)


def _setup_lifecycle(app):
    async def _start_core_managers(application):
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
        alert_manager.set_bot_name("CloudVerse Mega Bot")

        from shared.managers.AccessManager import start_ban_countdown_manager
        track_task(start_ban_countdown_manager(application, application.bot_data['account_repo'], application.bot_data['history_repo']))

        from shared.core.CallbackDataCache import start_cache_auto_save
        start_cache_auto_save()

        from shared.managers.SessionManager import initialize_session_manager
        from shared.core.Config import SERVER_DB_PATH
        from bots.mega.config import TeamCloudverse_GROUP_CHAT_ID, Management_TOPIC_ID, MEGA_SUPER_ADMIN_ID
        from shared.managers.EncryptionManager import get_encryption_manager
        session_manager = initialize_session_manager(
            db_path=str(SERVER_DB_PATH), cipher=get_encryption_manager(), group_chat_id=TeamCloudverse_GROUP_CHAT_ID,
            maintenance_topic_id=Management_TOPIC_ID, super_admin_id=MEGA_SUPER_ADMIN_ID,
            session_name_prefix="CloudVerse MegaBot",
        )
        await session_manager.initialize()
        track_task(session_manager.monitor_sessions(application))

        from shared.managers.TransferManager.TransferWorkers import upload_tracker_cleanup_task, queue_admission_worker
        from shared.managers.TransferManager.TransferTracker import get_transfer_tracker
        track_task(upload_tracker_cleanup_task())
        get_transfer_tracker().set_account_repo(application.bot_data['account_repo'])
        track_task(queue_admission_worker(application))

        from shared.managers.MemoryManager import start_memory_monitoring
        start_memory_monitoring()
        
        from shared.managers.LogManager import setup_daily_backups
        from bots.mega.config import BOT_DB_PATH
        setup_daily_backups(
            application, chat_id=application.bot_data['config']['GROUP_CHAT_ID'],
            topic_id=application.bot_data['config']['BACKUP_TOPIC_ID'], provider_name='mega',
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
        async def mega_probe() -> bool:
            try:
                return application.bot_data['provider'] is not None
            except Exception:
                return False

        register_probe("mega_db_connection", db_probe, failures_threshold=3)
        register_probe("telegram_api", telegram_probe, failures_threshold=5)
        register_probe("mega_provider", mega_probe, failures_threshold=3)
        start_health_monitoring()

    async def on_startup(application):
        logger.info("Setting Mega bot commands...")
        await set_bot_commands(application)
        import os
        await application.bot_data['account_repo'].sync_super_admins(os.getenv("SUPER_ADMIN_ID"))
        logger.info("✅ Super admins synced from .env")
        alert_manager = await _start_core_managers(application)
        _register_health_probes(application)
        logger.info("✅ Mega bot ready — all managers wired")
        await alert_manager.send_startup_notification()
        logger.info("AlertManager: startup notification sent")
        await application.bot_data['history_repo'].safe_create(
            action_taken="BOT_STARTUP", status="SUCCESS", event_details="Mega bot started successfully."
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
            action_taken="BOT_SHUTDOWN", status="SUCCESS", event_details="Mega bot shut down cleanly."
        )
        from shared.database.DatabaseConnectionManager import get_db_manager
        from bots.mega.config import BOT_DB_PATH
        await get_db_manager(str(BOT_DB_PATH)).close_all_async_connections()
        logger.info("Shutdown complete.")

    app.post_shutdown = on_shutdown

def main():
    setup_logging('mega')
    logger.info("🚀 Starting CloudVerse Mega Bot...")

    from bots.mega.config import (
        BOT_DB_PATH, MEGA_SUPER_ADMIN_ID,
        TeamCloudverse_GROUP_CHAT_ID, Access_TOPIC_ID, Flags_TOPIC_ID,
        Broadcasts_TOPIC_ID, Management_TOPIC_ID, Alerts_TOPIC_ID, Bugs_TOPIC_ID, BACKUP_TOPIC_ID
    )
    logger.info("Initializing mega.db")
    from shared.core.Config import SERVER_DB_PATH
    from shared.database.database import mega_init_db, server_init_db
    server_init_db(str(SERVER_DB_PATH))
    mega_init_db(str(BOT_DB_PATH), super_admin_id=MEGA_SUPER_ADMIN_ID)

    logger.info("Initializing EncryptionManager")
    from shared.managers.EncryptionManager import initialize_encryption_manager
    initialize_encryption_manager(ENCRYPTION_KEY, ENCRYPTION_SALT)

    app = ApplicationBuilder().token(MEGA_BOT_TOKEN).build()

    from shared.core.Provider import ProviderFactory
    from bots.mega.services.MegaProvider import MegaProvider
    mega_provider = MegaProvider()
    ProviderFactory.register_provider("mega", mega_provider)
    app.bot_data['provider'] = mega_provider
    app.bot_data['provider_name'] = "mega"
    

    from bots.mega.config import DEFAULT_QUOTA
    app.bot_data['config'] = {
        'GROUP_CHAT_ID': TeamCloudverse_GROUP_CHAT_ID,
        'SUPER_ADMIN_ID': MEGA_SUPER_ADMIN_ID,
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

    _initialize_dependencies(app)
    mega_provider.set_credential_repo(app.bot_data['credential_repo'])
    
    _register_handlers(app)
    _setup_lifecycle(app)

    logger.info("🤖 Mega bot polling started")
    app.run_polling()


if __name__ == "__main__":
    main()


