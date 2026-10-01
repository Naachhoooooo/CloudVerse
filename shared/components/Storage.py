from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.managers.AccessManager import access_required
from shared.core.ErrorHandler import handle_errors
logger = get_logger(__name__)


async def get_all_user_accounts(telegram_id, ctx: ContextTypes.DEFAULT_TYPE):
    """Get all Google accounts for a user"""
    try:
        credential_repo = ctx.bot_data.get('credential_repo')
        all_creds_dict = await credential_repo.get(telegram_id=str(telegram_id)) if credential_repo else None
        if not all_creds_dict:
            return []
            
        all_creds = [k for k in all_creds_dict.keys() if k.startswith('account_')]
        
        if all_creds:
            accounts = []
            for account_key in all_creds:
                accounts.append(all_creds_dict[account_key]['email'])
            return accounts
        elif 'email_address' in all_creds_dict:
            return [all_creds_dict['email_address']]
        else:
            return ['default']
    except Exception as e:
        logger.error(f"Error getting user accounts: {e}")
        return []


async def get_storage_for_account(provider, telegram_id, account_email):
    """Get storage information for a specific account"""
    try:
        service = await provider.get_service(telegram_id, account_email)
        if not service:
            return None
        
        storage = await provider.get_storage_info(service)
        used = int(float(storage["storageQuota"]["usage"])) / (1024 ** 3)
        limit = int(float(storage["storageQuota"]["limit"])) / (1024 ** 3)
        free = limit - used
        trash = int(float(storage["storageQuota"].get("usageInDriveTrash", 0))) / (1024 ** 3)
        used_percent = (used / limit) * 100 if limit > 0 else 0
        free_percent = (free / limit) * 100 if limit > 0 else 0
        
        return {
            'email': account_email,
            'used': used,
            'limit': limit,
            'free': free,
            'trash': trash,
            'used_percent': used_percent,
            'free_percent': free_percent
        }
    except Exception as e:
        logger.error(f"Error getting storage for account {account_email}: {e}")
        return None


@handle_errors
@access_required
async def handle_storage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if ctx.user_data is None:
            ctx.user_data = {}
        
        if update.message and update.message.from_user:
            telegram_id = update.message.from_user.id
            is_command = True
        elif update.callback_query and update.callback_query.from_user:
            await update.callback_query.answer()
            telegram_id = update.callback_query.from_user.id
            is_command = False
        else:
            return
        
        # Get all user accounts
        accounts = await get_all_user_accounts(telegram_id, ctx)
        if not accounts:
            provider_label = ctx.bot_data.get('provider_name', 'your cloud service').replace('gdrive', 'Google Drive').replace('mega', 'Mega').replace('rclone', 'Rclone')
            error_msg = f"Please login to {provider_label} first using /login"
            if update.message:
                await update.message.reply_text(error_msg)
            elif update.callback_query:
                await update.callback_query.edit_message_text(error_msg)
            return
        
        # Get storage information for all accounts
        storage_data = []
        total_used = 0
        total_limit = 0
        total_free = 0
        total_trash = 0
        
        for account_email in accounts:
            account_storage = await get_storage_for_account(ctx.bot_data['provider'], telegram_id, account_email)
            if account_storage:
                storage_data.append(account_storage)
                total_used += account_storage['used']
                total_limit += account_storage['limit']
                total_free += account_storage['free']
                total_trash += account_storage['trash']
        
        if not storage_data:
            error_msg = "Failed to retrieve storage information for any account. Please try again later."
            if update.message:
                await update.message.reply_text(error_msg)
            elif update.callback_query:
                await update.callback_query.edit_message_text(error_msg)
            return
        
        # Build the message text
        text = "📊 <b>Storage Usage Overview</b>\n\n"

        # Account details — layout: Account → blank line → bar → Used → blank → Free/Trash → blank
        for account in storage_data:
            account_label = account['email'][0].upper() + account['email'][1:] if account['email'] != 'default' else 'Default Account'
            text += f"<b>Account:</b> {account_label}\n\n"

            # Progress bar — green filled squares + white empty squares
            filled = round(account['used_percent'] / 10)  # 10 blocks total
            bar = "🟢" * filled + "⚪" * (10 - filled)
            text += f"{bar} {account['used_percent']:.0f}%\n\n"

            text += f"<b>Used:</b> {account['used']:.2f} GB of {account['limit']:.2f} GB\n"
            text += f"<b>Free:</b> {account['free']:.2f} GB ({account['free_percent']:.0f}%)\n"
            text += f"<b>Trash:</b> {account['trash']:.2f} GB\n"
            text += "\n"

        buttons = [
            [InlineKeyboardButton("Refresh", callback_data="refresh_storage")]
        ]
        
        try:
            if is_command and update.message:
                await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
            elif not is_command and update.callback_query:
                await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            import telegram
            if isinstance(e, telegram.error.BadRequest) and "Message is not modified" in str(e):
                logger.debug("Message content unchanged, no update needed")
            else:
                logger.error(f"Error handling storage details: {e}")
    except Exception as e:
        logger.error(f"Error in handle_storage: {e}")
        if update.message:
            await update.message.reply_text("Failed to load storage details. Please try again later.")
        elif update.callback_query:
            await update.callback_query.edit_message_text("Failed to load storage details. Please try again later.")


async def refresh_storage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
   if update.callback_query:
       await update.callback_query.answer("Refreshing storage details...")
   await handle_storage(update, ctx)

def register_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler
    app.add_handler(CommandHandler("storage", handle_storage))
    app.add_handler(CallbackQueryHandler(handle_storage, pattern=r"^STORAGE$"))
    app.add_handler(CallbackQueryHandler(refresh_storage, pattern=r"^refresh_storage$"))
