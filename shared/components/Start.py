from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.UserState import UserStateEnum, UserState
from shared.core.ErrorHandler import handle_errors
from shared.core.Logger import get_logger
logger = get_logger(__name__)

@handle_errors
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info("Main menu start function called")
    # Handle deep link: /start policies → open policies menu directly
    if ctx.args and ctx.args[0] == "policies":
        from shared.components.Policy import show_policies_menu
        await show_policies_menu(update, ctx)
        return
    if update.effective_user and hasattr(update.effective_user, 'id'):
        telegram_id = update.effective_user.id
        username = update.effective_user.username
        first_name = getattr(update.effective_user, 'first_name', '') or ''
        last_name = getattr(update.effective_user, 'last_name', '') or ''
        name = f"{first_name} {last_name}".strip()

        logger.info(f"Displaying main menu for user {telegram_id}")
        
        try:
            user_data = await ctx.bot_data['account_repo'].get(telegram_id=telegram_id)
            if user_data:
                if user_data.get('username') != username or user_data.get('name') != name:
                    await ctx.bot_data['account_repo'].update(
                        telegram_id=telegram_id,
                        username=username,
                        name=name
                    )
        except Exception as e:
            logger.debug(f"Failed to sync user profile: {e}")

        from shared.managers.AccessManager import _check_maintenance
        if not await _check_maintenance(update, ctx, telegram_id):
            return
    else:
        logger.warning("No effective user found in start function")
        return
    
    is_super_admin = await ctx.bot_data['account_repo'].is_super_admin(telegram_id=telegram_id)
    is_admin = await ctx.bot_data['account_repo'].is_admin(telegram_id=telegram_id)
    is_whitelisted = await ctx.bot_data['account_repo'].is_whitelisted(telegram_id=telegram_id)
    is_authorized = is_whitelisted or is_admin or is_super_admin

    user_name = update.effective_user.first_name or "there"

    if not is_authorized:
        text = (
            f"Hi <b>{user_name}</b>👋 Welcome to CloudVerse!\n\n"
            f"🛡 You are not currently authorized to use this bot.\n\n"
            f"In order to use this bot, you need to request access. "
            f"Please click the button below to submit a request, and Team CloudVerse will review it shortly."
        )
        buttons = [
            [InlineKeyboardButton("🔐 Request Access", callback_data="REQUEST_ACCESS")]
        ]
        markup = InlineKeyboardMarkup(buttons)
        if update.message:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
        elif update.callback_query and hasattr(update.callback_query, 'edit_message_text'):
            await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        return

    
    provider = ctx.bot_data.get('provider_name', 'drive').capitalize()
    platform = 'Google Drive' if provider == 'Drive' else 'Mega.nz' if provider == 'Mega' else 'Cloud Storage'

    text = (
        f"👋 Welcome back, <b>{user_name}</b>!, How are you feeling today? 😊\n\n"
        f"You are currently connected to the <b>CloudVerse {platform}</b> gateway.\n\n"
        f"🛡 Please make sure to check out our /policy before getting started!"
    )

    if update.message:
        await update.message.reply_text(text, parse_mode="HTML")
    elif update.callback_query and hasattr(update.callback_query, 'edit_message_text'):
        await update.callback_query.edit_message_text(text, parse_mode="HTML")

async def handle_request_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles the Request Access button."""
    q = update.callback_query
    if not q or not q.from_user:
        return
    await q.answer()
    
    telegram_id = str(q.from_user.id)
    username = q.from_user.username or "Unknown"
    first_name = q.from_user.first_name or "User"
    
    try:
        from shared.components.TeamCloudverse import post_request_to_cloudverse
        account_repo = ctx.bot_data['account_repo']
        
        # Check existing status
        existing = await account_repo.get(telegram_id)
        if existing:
            role = existing.get('role')
            if role in ('whitelisted', 'admin', 'super_admin'):
                await q.edit_message_text("✅ You already have access. Please use /start.")
                return
        
        # Register in DB
        success = await account_repo.create_pending(
            telegram_id=telegram_id,
            username=username,
            name=first_name,
            request_message_id=None
        )
        
        if success is False:
            await q.edit_message_text(
                "⏳ You have already submitted a request recently. Please wait 24 hours before trying again."
            )
            return

        # Notify admins
        msg_id = await post_request_to_cloudverse(ctx, telegram_id, username, first_name)
        if msg_id:
            await account_repo.update(telegram_id=telegram_id, request_message_id=msg_id)
        
        await q.edit_message_text(
            "✅ <b>Request Submitted</b>\n\n"
            "Team CloudVerse will review it shortly and you will be notified once a decision is made.",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Failed to handle request access for user {telegram_id}: {e}")
        await q.answer("Failed to submit request. Please try again later.", show_alert=True)



async def handle_noop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle noop callback to prevent endless loading spinner."""
    if update.callback_query:
        await update.callback_query.answer()

def register_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler
    app.add_handler(CommandHandler("start", start))
    from shared.components.Help import help_command
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(handle_request_access, pattern=r"^REQUEST_ACCESS$"))
    app.add_handler(CallbackQueryHandler(handle_noop, pattern=r"^noop$"))
