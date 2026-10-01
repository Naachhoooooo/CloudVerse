from telegram import Update
from telegram.ext import ContextTypes
from shared.core.ErrorHandler import handle_errors
from shared.core.Logger import get_logger

logger = get_logger(__name__)

from telegram import Update
from telegram.ext import ContextTypes
from shared.core.ErrorHandler import handle_errors
from shared.core.Logger import get_logger

logger = get_logger(__name__)

from shared.managers.AccessManager import access_required

@handle_errors
@access_required
async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info("Help command called")
    provider_name = ctx.bot_data.get('provider_name', 'Cloud')
    
    if provider_name == 'rclone':
        help_text = (
            f"Here are the commands you can use to interact with me:\n\n"
            "<b>Core Commands</b>\n"
            "• /start - Launch the main menu\n"
            "• /copy - Start a new Rclone cloud-to-cloud transfer\n"
            "• /remotes - View and manage your Rclone remotes\n"
            "• /config - Upload or update your rclone.conf file\n"
            "• /profile - View your profile and connection status\n"
            "• /filemanager - Browse and manage your cloud files\n"
            "• /storage - Check your available cloud storage\n"
            "• /settings - Customize your bot preferences\n"
            "• /policy - Read our Policy\n\n"
            "<b>Advanced Commands (Hidden)</b>\n"
            "• /queue - View the status of your active transfers\n\n"
            "💡 <b>How to Transfer?</b>\n"
            "Use the <b>/copy</b> command to sync files securely between your configured cloud remotes.\n\n"
            "💬 <b>Need Human Assistance?</b>\n"
            "If you encounter any bugs or need support, use the <b>/support</b> command."
        )
    else:
        help_text = (
            f"Here are the commands you can use to interact with me:\n\n"
            "<b>Core Commands</b>\n"
            "• /start - Launch the main menu\n"
            "• /profile - View your profile and connection status\n"
            "• /filemanager - Browse, rename, and manage your cloud files\n"
            "• /storage - Check your available cloud storage\n"
            "• /settings - Customize your bot preferences\n"
            "• /policy - Read our Policy\n\n"
            "<b>Advanced Commands (Hidden)</b>\n"
            "• /login - Authenticate with your cloud account directly\n"
            "• /queue - View the status of your active uploads\n\n"
            "💡 <b>How to Upload?</b>\n"
            "Simply send or forward any file, photo, or video directly to me, and I will safely upload it to your cloud!\n\n"
            "💬 <b>Need Human Assistance?</b>\n"
            "If you encounter any bugs or need support, use the <b>/support</b> command."
        )

    if update.message:
        await update.message.reply_text(help_text, parse_mode='HTML')

def register_handlers(app):
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("help", help_command))
