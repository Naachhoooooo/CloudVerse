"""
rclone Bot — main entry point.

Registers ALL shared component handlers (same UI as Drive and Mega bots) plus
rclone-specific handlers (RemoteManager, rclone config file upload/processing).
Provider-specific logic lives in bots/rclone/services/RcloneProvider.py.
Mirrors Drive bot's startup pattern: all shared managers wired.
"""

import warnings  # noqa: E402
from telegram.ext import (  # noqa: E402
    ApplicationBuilder, MessageHandler, filters
)
from telegram import Update  # noqa: E402
from telegram.ext import ContextTypes  # noqa: E402
from shared.core.Logger import get_logger, setup_logging  # noqa: E402
from shared.core.ErrorHandler import handle_errors  # noqa: E402
from bots.rclone.config import (  # noqa: E402
    RCLONE_BOT_TOKEN, RCLONE_SUPER_ADMIN_ID
)

logger = get_logger(__name__)
warnings.filterwarnings("ignore", category=UserWarning)

if not RCLONE_BOT_TOKEN:
    raise ValueError("RCLONE_BOT_TOKEN is not set. Check your .env file.")
if not RCLONE_SUPER_ADMIN_ID:
    raise ValueError("RCLONE_SUPER_ADMIN_ID is not set. Check your .env file.")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global PTB error handler — logs, notifies maintenance topic, notifies user."""
    error_str = str(context.error)
    # These are benign Telegram API responses or transient network errors — not real failures.
    # Never alert or message the user for these.
    BENIGN_ERRORS = (
        "Message is not modified",
        "Query is too old",
        "Message can't be deleted",
        "MESSAGE_ID_INVALID",
        "httpx.ReadError",
        "httpx.ConnectError",
        "httpx.WriteError",
        "httpx.TimeoutException",
        "NetworkError",
    )
    if any(phrase in error_str for phrase in BENIGN_ERRORS):
        logger.warning(f"[BOT] Suppressed benign Telegram error: {context.error}")
        return

    logger.error(f"[BOT] Unhandled PTB error: {context.error}", exc_info=context.error)
    try:
        from shared.managers.ServerManager import get_server_manager
        server_manager = get_server_manager()
        if server_manager.app and context.error:
            await server_manager.send_error_notification(str(context.error), "Bot Handler Error")
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






def _initialize_dependencies(app):
    from bots.rclone.config import (
        BOT_DB_PATH
    )
    from shared.managers.EncryptionManager import get_encryption_manager

    # ── Inject bot-specific repositories into bot_data ─────────────────────────
    from shared.database.repositories.RcloneCredentialsRepository import RcloneCredentialsRepository
    from shared.database.repositories.RcloneTransferRepository import RcloneTransferRepository
    from shared.database.repositories.BroadcastRepository import BroadcastRepository
    from shared.database.repositories.HistoryRepository import HistoryRepository
    from shared.database.repositories.AccountRepository import AccountRepository
    app.bot_data['credential_repo'] = RcloneCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
    app.bot_data['transfer_repo'] = RcloneTransferRepository(str(BOT_DB_PATH))
    app.bot_data['broadcast_repo'] = BroadcastRepository(str(BOT_DB_PATH), log_tag="RCLONE")
    history_repo = HistoryRepository(str(BOT_DB_PATH))
    app.bot_data['history_repo'] = history_repo
    app.bot_data['account_repo'] = AccountRepository(str(BOT_DB_PATH), history_repo=history_repo)
    from shared.core.Config import SERVER_DB_PATH
    from shared.managers.TicketManager import TicketManager
    from shared.database.DatabaseConnectionManager import get_db_manager
    app.bot_data['ticket_manager'] = TicketManager(get_db_manager(str(SERVER_DB_PATH)))
    if 'provider' in app.bot_data:
        app.bot_data['provider'].credential_repo = app.bot_data['credential_repo']
    logger.info("[BOT] All repositories injected for rclone.db")
    
    from shared.core.ComponentInitializer import init_shared_components
    init_shared_components(app.bot_data['config'])
    
    from shared.managers.DomainManager import init_domain_manager
    from pathlib import Path
    _assets_dir = Path(__file__).parent.parent.parent.parent / "shared" / "assets"
    init_domain_manager(str(_assets_dir / "AllowedDomains.md"))
    


from shared.managers.AccessManager import access_required  # noqa: E402
from telegram import Update  # noqa: E402
from telegram.ext import ContextTypes  # noqa: E402
from shared.core.AsyncUtils import track_task  # noqa: E402

@handle_errors
@access_required
async def handle_document_or_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from telegram.ext import ApplicationHandlerStop
    
    if update.message.document:
        from shared.core.UserState import UserStateEnum, UserState
        user_state: UserState = ctx.user_data.get("state") if ctx.user_data else None
        is_expecting_config = user_state and user_state.is_state(UserStateEnum.EXPECTING_RCLONE_CONFIG)
        file_name = update.message.document.file_name or ""
        
        if is_expecting_config or file_name.endswith(".conf"):
            file_name = update.message.document.file_name
            if not file_name.endswith(".conf"):
                await update.message.reply_text("Expected a .conf file. Please upload rclone.conf.")
                raise ApplicationHandlerStop()

            file_size = update.message.document.file_size
            if file_size and file_size > 50 * 1024:
                await update.message.reply_text("❌ Config file is too large. Maximum size is 50KB.")
                raise ApplicationHandlerStop()

            file = await update.message.document.get_file()
            file_bytes = await file.download_as_bytearray()
            try:
                conf_content = file_bytes.decode('utf-8')
            except UnicodeDecodeError:
                await update.message.reply_text("❌ Invalid file encoding. Please upload a valid UTF-8 rclone.conf file.")
                raise ApplicationHandlerStop()

            import re
            if not re.search(r'\[.+\]', conf_content) or 'type' not in conf_content.lower():
                await update.message.reply_text("❌ Invalid config format. Does not appear to be a valid rclone config.")
                raise ApplicationHandlerStop()
            
            telegram_id = str(update.effective_user.id)
            username = update.effective_user.username
            credential_repo = ctx.bot_data.get("credential_repo")
            if credential_repo:
                try:
                    await credential_repo.upsert(telegram_id, username, conf_content)
                    await update.message.reply_text("✅ rclone configuration saved successfully.")
                    if user_state:
                        user_state.set_state(UserStateEnum.MENU)
                except Exception as e:
                    logger.error(f"Failed to save rclone config: {e}")
                    await update.message.reply_text("❌ Error: could not save config.")
            else:
                await update.message.reply_text("❌ Database error: could not save config.")
            raise ApplicationHandlerStop()
        else:
            await update.message.reply_text("ℹ️ The rclone bot only supports cloud-to-cloud transfers via /copy. To upload files, use the Drive or Mega bot.")
            raise ApplicationHandlerStop()

    if update.message.video or update.message.audio or update.message.photo:
        await update.message.reply_text("ℹ️ The rclone bot only supports cloud-to-cloud transfers via /copy. To upload media, use the Drive or Mega bot.")
        raise ApplicationHandlerStop()

def _register_handlers(app):
    from shared.core.ComponentInitializer import register_shared_handlers
    register_shared_handlers(app, include_bin=False, include_telethon=False)
    
    from telegram.ext import CommandHandler, CallbackQueryHandler
    from bots.rclone.components.Config import handle_config_cmd, handle_config_callback
    from bots.rclone.components.RemotePicker import handle_rclone_transfer_cmd, handle_picker_callback
    
    app.add_handler(CommandHandler("copy", handle_rclone_transfer_cmd))
    app.add_handler(CommandHandler("config", handle_config_cmd))
    app.add_handler(CallbackQueryHandler(handle_config_callback, pattern=r"^(rclone_config_get|rclone_config_delete_confirm|rclone_config_delete_cancel|rclone_config_delete)$"))
    app.add_handler(CallbackQueryHandler(handle_picker_callback, pattern=r"^(rclone_picker|rclone_pick|rclone_browse|rclone_confirm|rclone_page).*$"))

    from shared.core.InputRouter import handle_user_input
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_input))
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO) & ~filters.COMMAND,
        handle_document_or_media
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
        alert_manager.set_bot_name("CloudVerse RClone Bot")
        
        from shared.managers.AccessManager import start_ban_countdown_manager
        track_task(start_ban_countdown_manager(application, application.bot_data['account_repo'], application.bot_data['history_repo']))

        from shared.core.CallbackDataCache import start_cache_auto_save
        start_cache_auto_save()

        from shared.managers.TransferManager.TransferWorkers import upload_tracker_cleanup_task, queue_admission_worker
        from shared.managers.TransferManager.TransferTracker import get_transfer_tracker
        track_task(upload_tracker_cleanup_task())
        get_transfer_tracker().set_account_repo(application.bot_data['account_repo'])
        track_task(queue_admission_worker(application))

        from shared.managers.MemoryManager import start_memory_monitoring
        start_memory_monitoring()

        from shared.managers.LogManager import setup_daily_backups
        from bots.rclone.config import BOT_DB_PATH
        setup_daily_backups(
            application, chat_id=application.bot_data['config']['GROUP_CHAT_ID'],
            topic_id=application.bot_data['config']['BACKUP_TOPIC_ID'], provider_name='rclone',
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
        async def rclone_probe() -> bool:
            from bots.rclone.services.RcloneService import check_binary
            return await check_binary()

        register_probe("rclone_db_connection", db_probe, failures_threshold=3)
        register_probe("telegram_api", telegram_probe, failures_threshold=5)
        register_probe("rclone_binary", rclone_probe, failures_threshold=3)
        start_health_monitoring()

    # Capture this session's timestamp at startup — matches the filenames in Logger.py
    import datetime as _dt
    _SESSION_TS = _dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    async def on_startup(application):

        import os
        await application.bot_data['account_repo'].sync_super_admins(
            os.getenv("RCLONE_SUPER_ADMIN_ID") or os.getenv("GLOBAL_SUPER_ADMIN_ID")
        )
        logger.info("✅ Super admins synced from .env")

        from bots.rclone.services.RcloneService import check_binary
        if not await check_binary():
            logger.error("[RCLONE] rclone binary not found or not executable. Check RCLONE_PATH in .env")
        else:
            logger.info("[RCLONE] rclone binary verified")

        alert_manager = await _start_core_managers(application)
        _register_health_probes(application)

        logger.info("✅ rclone bot ready — all managers wired")
        await alert_manager.send_startup_notification()
        logger.info("AlertManager: startup notification sent")
        await application.bot_data['history_repo'].safe_create(
            action_taken="BOT_STARTUP", status="SUCCESS", event_details="rclone bot started successfully."
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
            action_taken="BOT_SHUTDOWN", status="SUCCESS", event_details="rclone bot shut down cleanly."
        )
        from shared.database.DatabaseConnectionManager import get_db_manager
        from bots.rclone.config import BOT_DB_PATH
        await get_db_manager(str(BOT_DB_PATH)).close_all_async_connections()
        logger.info("Shutdown complete.")

    app.post_shutdown = on_shutdown

def main():
    setup_logging('rclone')
    logger.info("🚀 Starting CloudVerse RClone Bot...")

    from bots.rclone.config import (
        BOT_DB_PATH, RCLONE_SUPER_ADMIN_ID, ENCRYPTION_KEY, ENCRYPTION_SALT,
        TeamCloudverse_GROUP_CHAT_ID, Access_TOPIC_ID, Flags_TOPIC_ID,
        Broadcasts_TOPIC_ID, Management_TOPIC_ID, Alerts_TOPIC_ID, Bugs_TOPIC_ID, BACKUP_TOPIC_ID
    )
    logger.info("Initializing rclone.db")
    from shared.database.database import rclone_init_db
    rclone_init_db(str(BOT_DB_PATH), super_admin_id=RCLONE_SUPER_ADMIN_ID)

    logger.info("Initializing EncryptionManager")
    from shared.managers.EncryptionManager import initialize_encryption_manager
    initialize_encryption_manager(ENCRYPTION_KEY, ENCRYPTION_SALT)

    app = ApplicationBuilder().token(RCLONE_BOT_TOKEN).build()
    
    from shared.core.Provider import ProviderFactory
    from bots.rclone.services.RcloneProvider import RcloneProvider
    rclone_provider = RcloneProvider()
    ProviderFactory.register_provider("rclone", rclone_provider)
    app.bot_data['provider'] = rclone_provider
    app.bot_data['provider_name'] = "rclone"
    
    # Needs to be assigned after dependencies are initialized
    # So we'll assign it in _initialize_dependencies or here after _initialize_dependencies

    from shared.managers.TransferManager import TransferServiceProvider, set_service_provider
    class RcloneServiceProvider(TransferServiceProvider):
        def get_download_manager(self): return None
        def get_upload_manager(self): return None
        async def get_drive_service(self, telegram_id): return await rclone_provider.get_service(telegram_id)
        async def get_file_link(self, service, file_id): return await rclone_provider.get_file_link(service, file_id)
        async def get_file_metadata(self, service, file_id): return await rclone_provider.get_file_metadata(service, file_id)
        async def delete_file(self, service, file_id): return await rclone_provider.delete_file(service, file_id)

    set_service_provider(RcloneServiceProvider())

    from bots.rclone.config import DEFAULT_QUOTA
    app.bot_data['config'] = {
        'GROUP_CHAT_ID': TeamCloudverse_GROUP_CHAT_ID,
        'SUPER_ADMIN_ID': RCLONE_SUPER_ADMIN_ID,
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
    _register_handlers(app)
    _setup_lifecycle(app)

    logger.info("🤖 rclone bot polling started")
    app.run_polling()


if __name__ == "__main__":
    main()


