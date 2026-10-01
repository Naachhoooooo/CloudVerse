"""RemoteManager component stub for the rclone bot."""

from telegram import Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import access_required
from bots.rclone.services.RcloneService import list_remotes
from bots.rclone.services.ConfigHelper import user_config

logger = get_logger(__name__)


@handle_errors
@access_required
async def handle_remote_manager(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    List configured rclone remotes and display them as an inline keyboard.
    Calls RcloneService.list_remotes() — requires rclone binary + configured remotes.
    """
    try:
        credential_repo = ctx.bot_data.get('credential_repo')
        telegram_id = update.effective_user.id
        async with user_config(credential_repo, str(telegram_id)) as config_path:
            remotes = await list_remotes(config_path)
    except Exception as e:
        logger.error(f"Failed to list rclone remotes: {e}", exc_info=True)
        msg = f"❌ Failed to list remotes: `{e}`\n\nCheck that rclone is installed and configured."
        if update.message:
            await update.message.reply_text(msg, parse_mode="HTML")
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="HTML")
        return

    if not remotes:
        msg = (
            "📭 No rclone remotes configured.\n\n"
            "Run `rclone config` on the server to add a remote, then try again."
        )
    else:
        remote_list = "\n".join(f"• `{r}`" for r in remotes)
        msg = f"🌐 **Configured Remotes**\n\n{remote_list}"

    if update.message:
        await update.message.reply_text(msg, parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="HTML")
