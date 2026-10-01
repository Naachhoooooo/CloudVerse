import sys
from pathlib import Path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import logging
from telegram import BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from bots.administrator.config import BOT_TOKEN
from shared.core.Logger import get_logger, setup_logging

setup_logging("administrator")
logger = get_logger(__name__)

async def post_init(application):
    commands = [
        BotCommand("superadmin", "Manage Super Admins"),
        BotCommand("admin", "Manage Admins"),
        BotCommand("whitelisted", "Manage Whitelisted Users"),
        BotCommand("blacklisted", "Manage Blacklisted Users"),
        BotCommand("pending", "Manage Pending Requests"),
        BotCommand("quota", "Manage Quotas"),
        BotCommand("broadcast", "Broadcast Messages"),
        BotCommand("server", "Server Performance"),
        BotCommand("records", "Delete Records"),
        BotCommand("domain", "Link Domains"),
        BotCommand("session", "Telethon Sessions"),
        BotCommand("maintenance", "Maintenance Mode"),
        BotCommand("queue", "Live Traffic Queue"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands updated.")
    
    # Set a default account repo for the admin_required decorator checks
    from bots.administrator.utils.db_utils import get_account_repo
    application.bot_data['account_repo'] = get_account_repo('drive')

    from shared.managers.AlertManager import get_alert_manager
    alert_manager = get_alert_manager()
    alert_manager.set_application(application)
    alert_manager.set_bot_name("CloudVerse Administrator Bot")
    await alert_manager.send_startup_notification()

def main():
    if not BOT_TOKEN:
        logger.error("ADMIN_BOT_TOKEN is not set. Exiting.")
        return

    logger.info("Starting Administrator Bot...")
    
    from shared.managers.EncryptionManager import initialize_encryption_manager
    from bots.administrator.config import ENCRYPTION_KEY, ENCRYPTION_SALT
    initialize_encryption_manager(ENCRYPTION_KEY, ENCRYPTION_SALT)

    from shared.managers.SessionManager import initialize_session_manager
    from shared.core.Config import SERVER_DB_PATH
    from bots.administrator.config import TeamCloudverse_GROUP_CHAT_ID, Management_TOPIC_ID, SUPER_ADMIN_ID
    from shared.managers.EncryptionManager import get_encryption_manager
    
    initialize_session_manager(
        db_path=str(SERVER_DB_PATH),
        cipher=get_encryption_manager(),
        group_chat_id=TeamCloudverse_GROUP_CHAT_ID,
        maintenance_topic_id=Management_TOPIC_ID,
        super_admin_id=SUPER_ADMIN_ID,
        session_name_prefix="CloudVerse AdminBot"
    )

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    from bots.administrator.config import (
        Access_TOPIC_ID, Flags_TOPIC_ID, Broadcasts_TOPIC_ID, Alerts_TOPIC_ID, Bugs_TOPIC_ID, BACKUP_TOPIC_ID, DEFAULT_QUOTA
    )
    
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
        'BOT_DB_PATH': str(SERVER_DB_PATH),
        'DEFAULT_QUOTA': DEFAULT_QUOTA,
    }

    from telegram.ext import TypeHandler, ApplicationHandlerStop
    from telegram import Update
    from telegram.ext import ContextTypes
    
    async def check_global_authorization(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
            
        chat = update.effective_chat
        if chat:
            from bots.administrator.config import TeamCloudverse_GROUP_CHAT_ID
            allowed_chat = chat.type == 'private'
            if not allowed_chat and TeamCloudverse_GROUP_CHAT_ID:
                try:
                    if chat.id == int(TeamCloudverse_GROUP_CHAT_ID):
                        allowed_chat = True
                except ValueError:
                    pass
            if not allowed_chat:
                raise ApplicationHandlerStop()
        elif update.inline_query:
            inline_chat_type = update.inline_query.chat_type
            if inline_chat_type and inline_chat_type not in ('sender', 'private'):
                # Not in a private chat/PM, block the inline query
                raise ApplicationHandlerStop()
                
            
        from bots.administrator.utils.db_utils import get_account_repo
        
        is_admin = False
        for bot_name in ['drive', 'mega', 'rclone']:
            repo = get_account_repo(bot_name)
            if await repo.is_admin(user.id) or await repo.is_super_admin(user.id):
                is_admin = True
                break
                
        if not is_admin:
            raise ApplicationHandlerStop()
            
    app.add_handler(TypeHandler(Update, check_global_authorization), group=-1)

    # Access Control routes mapping to new modular components
    from bots.administrator.components.ManageSuperadmin import manage_super_admins
    from bots.administrator.components.ManageAdmin import manage_admins
    from bots.administrator.components.ManageWhitelisted import manage_whitelist
    from bots.administrator.components.ManageBlacklisted import manage_blacklist
    from bots.administrator.components.ManageRequest import handle_pending_requests
    
    app.add_handler(CommandHandler("superadmin", manage_super_admins))
    app.add_handler(CommandHandler("admin", manage_admins))
    app.add_handler(CommandHandler("whitelisted", manage_whitelist))
    app.add_handler(CommandHandler("blacklisted", manage_blacklist))
    app.add_handler(CommandHandler("pending", handle_pending_requests))
    
    from bots.administrator.components.Search import handle_search_user, handle_inline_search, handle_search_callback
    from telegram.ext import InlineQueryHandler, CallbackQueryHandler
    app.add_handler(CommandHandler('user', handle_search_user))
    app.add_handler(CommandHandler('search', handle_search_user))
    app.add_handler(InlineQueryHandler(handle_inline_search))
    app.add_handler(CallbackQueryHandler(handle_search_callback, pattern=r"^(search_manage:.*|noop)$"))
    
    app.add_handler(CallbackQueryHandler(manage_admins, pattern='^manage_admins$'))
    app.add_handler(CallbackQueryHandler(manage_super_admins, pattern='^manage_super_admins$'))
    app.add_handler(CallbackQueryHandler(manage_whitelist, pattern='^manage_whitelist$'))
    app.add_handler(CallbackQueryHandler(manage_blacklist, pattern='^manage_blacklist$'))
    app.add_handler(CallbackQueryHandler(handle_pending_requests, pattern='^(pending_requests|pending_select:.*|pending_approve_permanent:.*|pending_approve_limited:.*|pending_reject:.*|pending_prev_page|pending_next_page|pending_approve_all|pending_reject_all)$'))
    
    async def handle_access_control_actions(update, ctx):
        from bots.administrator.handlers.ActionDispatcher import dispatch
        return await dispatch(update, ctx)
        
    app.add_handler(CallbackQueryHandler(handle_access_control_actions, pattern='^(add_admin|remove_admin:.*|add_whitelist|remove_whitelist:.*|set_limit:.*|remove_limit:.*|promote_admin:.*|demote_admin:.*|demote_super_admin_to_admin:.*|demote_super_admin_to_whitelist:.*|admin_prev_page|admin_next_page|super_admin_prev_page|super_admin_next_page|whitelist_prev_page|whitelist_next_page|blacklist_prev_page|blacklist_next_page|requests_prev_page|requests_next_page|back_to_access|back_to_whitelist|admin_select:.*|super_admin_select:.*|whitelist_select:.*|blacklist_select:.*|promote_whitelist_to_admin:.*|ban_whitelist_user:.*|set_whitelist_limit:.*|modify_whitelist_limit:.*|remove_whitelist_limit:.*|promote_blacklist_to_whitelist:.*|set_restriction:.*|remove_blacklist:.*|modify_blacklist_restriction:.*|unrestrict_blacklist:.*|edit_blacklist:.*|edit_blacklist_type:.*|duration_.*|limit_.*|demote_admin_to_whitelist:.*|toggle_bot_filter)$'))

    # New granular modules
    from bots.administrator.components.Quota import handle_quota_command, handle_quota_callback, _handle_quota_input
    from bots.administrator.components.Server import handle_server_command, handle_server_callback
    from bots.administrator.components.Records import handle_records_command, handle_records_callback
    from bots.administrator.components.Maintenance import handle_maintenance_command, handle_maintenance_callback
    from bots.administrator.components.Broadcast import handle_broadcast_command, handle_broadcast_callback, handle_broadcast_media_message
    from bots.administrator.components.Domain import handle_domain_command, handle_domain_callback
    from bots.administrator.components.Session import handle_session_command, handle_session_callback
    from bots.administrator.components.Help import handle_help_command
    from bots.administrator.components.Start import handle_start
    from bots.administrator.components.Queue import handle_queue_command, handle_queue_callback

    # Commands
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("quota", handle_quota_command))
    app.add_handler(CommandHandler("server", handle_server_command))
    app.add_handler(CommandHandler("records", handle_records_command))
    app.add_handler(CommandHandler("maintenance", handle_maintenance_command))
    app.add_handler(CommandHandler("broadcast", handle_broadcast_command))
    app.add_handler(CommandHandler("domain", handle_domain_command))
    app.add_handler(CommandHandler("session", handle_session_command))
    app.add_handler(CommandHandler("help", handle_help_command))
    
    from shared.utils.topics import handle_topic_command
    app.add_handler(CommandHandler("topic", handle_topic_command))
    app.add_handler(CommandHandler("queue", handle_queue_command))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(handle_quota_callback, pattern=r"^(manage_user_quota(:.*)?|quota_.*|edit_quota:.*|set_quota:.*|reset_usage:.*|flag_user:.*|flag_ban_perm:.*|flag_ban_temp:.*)$"))
    app.add_handler(CallbackQueryHandler(handle_server_callback, pattern=r"^(server_.*|terminate_bot:.*|confirm_term_bot:.*|cancel_term_bot|terminate_server|cancel_terminate|confirm_terminate:.*)$"))
    app.add_handler(CallbackQueryHandler(handle_records_callback, pattern=r"^(handle_delete_records|delete_records_.*|delete_user_.*)$"))
    app.add_handler(CallbackQueryHandler(handle_maintenance_callback, pattern=r"^(maintenance_.*)$"))
    app.add_handler(CallbackQueryHandler(handle_broadcast_callback, pattern=r"^(broadcast_.*|approve_broadcast:.*|reject_broadcast:.*)$"))
    app.add_handler(CallbackQueryHandler(handle_domain_callback, pattern=r"^(domain_.*)$"))
    app.add_handler(CallbackQueryHandler(handle_session_callback, pattern=r"^(session_.*)$"))
    app.add_handler(CallbackQueryHandler(handle_queue_callback, pattern=r"^(refresh_queue|queue_page:.*|queue_details:.*|queue_kill:.*)$"))
    
    # Text handlers for inputs
    async def global_text_handler(update, ctx):
        if ctx.user_data.get("awaiting_quota_input"):
            from bots.administrator.utils.db_utils import get_active_bot
            from shared.core.Config import get_bot_db_path
            from shared.database.repositories.UsageRepository import UsageRepository
            bot_name = get_active_bot(ctx) or 'drive'
            usage_repo = UsageRepository(get_bot_db_path(bot_name))
            await _handle_quota_input(update.message, ctx, usage_repo)
        elif ctx.user_data.get("awaiting_broadcast_message"):
            await handle_broadcast_media_message(update, ctx)
        elif ctx.user_data.get("awaiting_delete_typein"):
            await handle_records_callback(update, ctx)
        elif ctx.user_data.get("awaiting_maintenance_notice"):
            from bots.administrator.components.Maintenance import handle_maintenance_notice_input
            await handle_maintenance_notice_input(update.message, ctx)
        elif ctx.user_data.get("awaiting_new_domain"):
            from bots.administrator.components.Domain import handle_new_domain_input
            await handle_new_domain_input(update.message, ctx)
        elif any(ctx.user_data.get(k) for k in [
            'awaiting_update_api_id', 'awaiting_update_api_hash'
        ]):
            from bots.administrator.components.Session import handle_update_api_credentials
            await handle_update_api_credentials(update, ctx)
        elif any(ctx.user_data.get(k) for k in [
            'awaiting_api_id', 'awaiting_api_hash', 'awaiting_telethon_phone',
            'awaiting_telethon_code', 'awaiting_telethon_2fa'
        ]):
            from bots.administrator.components.Session import handle_authenticate_telethon_sessions
            await handle_authenticate_telethon_sessions(update, ctx)
        else:
            await handle_access_control_actions(update, ctx)

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, global_text_handler))

    from shared.managers.LogManager import setup_unified_daily_backup
    from bots.administrator.config import TeamCloudverse_GROUP_CHAT_ID, BACKUP_TOPIC_ID
    
    setup_unified_daily_backup(
        application=app,
        chat_id=TeamCloudverse_GROUP_CHAT_ID,
        topic_id=BACKUP_TOPIC_ID
    )

    logger.info("Administrator Bot is running.")
    app.run_polling()

if __name__ == "__main__":
    main()
