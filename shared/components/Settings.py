import html
"""
Settings — provider-agnostic settings UI for all CloudVerse bots.

handle_login and handle_logout delegate the entire auth flow to
ctx.bot_data['provider'].do_login() / .do_logout(), so this module
contains zero provider-specific logic.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from .FileManager import handle_file_manager
from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import access_required
from shared.core.UserState import UserState, UserStateEnum
from shared.core.Logger import get_logger

logger = get_logger(__name__)


@handle_errors
@access_required
async def handle_settings_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Display the Settings menu with Login/Logout based on credential state."""
    telegram_id = None
    if update.callback_query and update.callback_query.from_user:
        await update.callback_query.answer()
        telegram_id = update.callback_query.from_user.id
    elif update.message and update.message.from_user:
        telegram_id = update.message.from_user.id

    credential_repo = ctx.bot_data.get('credential_repo')
    all_creds = await credential_repo.get(telegram_id=str(telegram_id)) if telegram_id and credential_repo else None

    buttons = []
    if all_creds:
        buttons.append([InlineKeyboardButton("🚪 Logout", callback_data="logout")])
        if ctx.bot_data.get('provider_name') != 'rclone':
            buttons.append([InlineKeyboardButton("📁 Update Upload Location", callback_data="update_def_location")])
        buttons.append([InlineKeyboardButton("⚌ Update Parallel Transfers", callback_data="handle_update_parallel_transfers")])
    else:
        buttons.append([InlineKeyboardButton("🔑 Login", callback_data="login")])

    if update.callback_query:
        await update.callback_query.edit_message_text(
            "⚙️ <b>Settings</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML'
        )
    elif update.message:
        await update.message.reply_text(
            "⚙️ <b>Settings</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML'
        )


@handle_errors
@access_required
async def handle_login(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Provider-agnostic login dispatcher.
    Delegates entirely to ctx.bot_data['provider'].do_login(update, ctx).
    All OAuth/credential logic lives inside the provider implementation.
    """
    if update.callback_query:
        await update.callback_query.answer()
    provider = ctx.bot_data.get('provider')
    if not provider:
        msg = "❌ No provider configured. Contact the admin."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        elif update.message:
            await update.message.reply_text(msg)
        return
    await provider.do_login(update, ctx)


@handle_errors
@access_required
async def handle_logout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Provider-agnostic logout dispatcher.
    Delegates entirely to ctx.bot_data['provider'].do_logout(update, ctx).
    """
    if update.callback_query:
        await update.callback_query.answer()
    provider = ctx.bot_data.get('provider')
    if not provider:
        return
    await provider.do_logout(update, ctx)


@handle_errors
@access_required
async def update_default_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Let the user pick a new default upload folder via the file manager."""
    if ctx.user_data is None:
        ctx.user_data = {}

    if update.callback_query and update.callback_query.from_user:
        await update.callback_query.answer()
        telegram_id = update.callback_query.from_user.id
    else:
        return

    provider = ctx.bot_data['provider']
    service = await provider.get_service(telegram_id)
    if not service:
        await update.callback_query.edit_message_text("❌ Please login first.")
        return

    q = update.callback_query
    if q and q.data == "set_def_location":
        current_account = ctx.user_data.get("current_account") or "default_account"
        account_data = ctx.user_data.get("account_data", {}).get(current_account, {})
        folder_id = account_data.get("current_folder", "root")
        
        credential_repo = ctx.bot_data.get('credential_repo')
        if credential_repo and await credential_repo.update_default_location(telegram_id=str(telegram_id), location=folder_id):
            folder_name = await provider.get_folder_name(service, folder_id)
            await q.edit_message_text(
                f"✅ Default upload location updated to <b>{html.escape(folder_name)}</b>.", parse_mode='HTML'
            )
        else:
            await q.edit_message_text("❌ Failed to update default upload location.")
        ctx.user_data.pop("in_def_location", None)
        return

    ctx.user_data["in_def_location"] = True
    await handle_file_manager(update, ctx)


@handle_errors
@access_required
async def update_parallel_transfers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Let the user set the parallel upload concurrency limit (1-3)."""
    if ctx.user_data is None:
        ctx.user_data = {}

    if "state" not in ctx.user_data or not isinstance(ctx.user_data["state"], UserState):
        ctx.user_data["state"] = UserState(ctx)

    user_state: UserState = ctx.user_data["state"]

    # Process the typed number
    if update.message and update.message.text:
        if not user_state.is_state(UserStateEnum.EXPECTING_PARALLEL_TRANSFERS):
            return

        telegram_id = update.message.from_user.id
        text = update.message.text

        if isinstance(text, str) and text.isdigit():
            num = int(text)
            if 1 <= num <= 3:
                credential_repo = ctx.bot_data.get('credential_repo')
                if credential_repo and await credential_repo.update_parallel_uploads(telegram_id=str(telegram_id), parallel=num):
                    await update.message.reply_text(f"✅ Parallel transfer limit updated to {num}.")
                else:
                    await update.message.reply_text("❌ Failed to update parallel transfer limit.")
                user_state.reset()
            else:
                await update.message.reply_text("Please enter a number between 1 and 3.")
        else:
            await update.message.reply_text("Please enter a valid number.")
        return

    # Prompt for the number
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("✏️ Enter the parallel transfer limit (1-3):")
        user_state.set_state(UserStateEnum.EXPECTING_PARALLEL_TRANSFERS)

def register_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler
    app.add_handler(CommandHandler("settings", handle_settings_menu))
    app.add_handler(CommandHandler("login", handle_login))
    app.add_handler(CommandHandler("logout", handle_logout))
    app.add_handler(CallbackQueryHandler(handle_settings_menu, pattern=r"^SETTINGS$"))
    app.add_handler(CallbackQueryHandler(handle_login, pattern=r"^login$"))
    app.add_handler(CallbackQueryHandler(handle_logout, pattern=r"^(logout|logout_account|logout_specific:.*|logout_all_prompt|confirm_logout:.*)$"))
    app.add_handler(CallbackQueryHandler(update_default_location, pattern=r"^(update_def_location|set_def_location)$"))
    app.add_handler(CallbackQueryHandler(update_parallel_transfers, pattern=r"^handle_update_parallel_transfers$"))