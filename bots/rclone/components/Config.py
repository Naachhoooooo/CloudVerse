"""Config component for the rclone bot."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import access_required
from bots.rclone.services.RcloneService import list_remotes
from bots.rclone.services.ConfigHelper import user_config
import os

logger = get_logger(__name__)

@handle_errors
@access_required
async def handle_config_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Handle the /config command to manage rclone.conf
    """
    credential_repo = ctx.bot_data.get('credential_repo')
    telegram_id = update.effective_user.id
    remotes = []
    
    try:
        async with user_config(credential_repo, str(telegram_id)) as config_path:
            remotes = await list_remotes(config_path)
    except Exception as e:
        logger.error(f"Failed to fetch config remotes: {e}")
        # Even if it fails (e.g. no config), we can show empty state

    if not remotes:
        msg = (
            "❇️ <b>Rclone configuration</b>\n\n"
            "You have no remotes configured yet.\n\n"
            "Send an <code>rclone.conf</code> file to this bot to configure your cloud drives."
        )
    else:
        remote_list = "\n".join(f"• {r}" for r in remotes)
        msg = (
            "❇️ <b>Rclone configuration</b>\n\n"
            "Here is list of drives in config file:\n"
            f"{remote_list}"
        )
    
    buttons = []
    if remotes:
        buttons.append([InlineKeyboardButton("📁 Get rclone config file", callback_data="rclone_config_get")])
        buttons.append([InlineKeyboardButton("🗑️ Delete config file", callback_data="rclone_config_delete")])
    
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    if update.message:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)


@handle_errors
@access_required
async def handle_config_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Handle config-related callbacks
    """
    q = update.callback_query
    await q.answer()
    
    if q.data == "rclone_config_delete":
        msg = "⚠️ Are you sure you want to delete your stored rclone.conf? It will be permanently removed from our servers."
        buttons = [
            [InlineKeyboardButton("✅ Yes, Delete", callback_data="rclone_config_delete_confirm")],
            [InlineKeyboardButton("❌ Cancel", callback_data="rclone_config_delete_cancel")]
        ]
        await q.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
        
    elif q.data == "rclone_config_delete_confirm":
        telegram_id = update.effective_user.id
        credential_repo = ctx.bot_data.get('credential_repo')
        if credential_repo:
            try:
                await credential_repo.delete(telegram_id=str(telegram_id), provider='rclone')
                await q.edit_message_text("✅ Rclone configuration deleted successfully.", parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to delete config for {telegram_id}: {e}")
                await q.edit_message_text("❌ Failed to delete rclone configuration.", parse_mode="HTML")
        
    elif q.data == "rclone_config_delete_cancel":
        await handle_config_cmd(update, ctx)
        
    elif q.data == "rclone_config_get":
        telegram_id = update.effective_user.id
        credential_repo = ctx.bot_data.get('credential_repo')
        try:
            async with user_config(credential_repo, str(telegram_id)) as config_path:
                if os.path.exists(config_path):
                    await q.message.reply_document(
                        document=open(config_path, 'rb'),
                        filename="rclone.conf",
                        caption="📁 Here is your current rclone config file."
                    )
                else:
                    await q.message.reply_text("❌ Could not generate the config file.")
        except Exception as e:
            logger.error(f"Failed to send config for {telegram_id}: {e}")
            await q.message.reply_text("❌ An error occurred while retrieving your config file.")
