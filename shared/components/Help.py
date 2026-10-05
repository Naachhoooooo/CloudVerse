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
            "🤖 <b>CloudVerse Command Centre</b>\n\n"
            "<b>Core Commands</b>\n"
            "• /start - ⚡️ Ping the bot\n"
            "• /copy - ⧉ Start a new cloud transfer\n"
            "• /remotes - 🌍 View and manage Rclone remotes\n"
            "• /config - ❇️ Upload or update configuration\n"
            "• /profile - 🪪 View your user profile\n"
            "• /storage - ☁️ Check your available storage\n"
            "• /filemanager - 🗂 Browse and manage files\n"
            "• /recyclebin - ♻️ Access deleted files\n"
            "• /settings - ⚙️ Customize your preferences\n\n"
            "<b>Transfers & Utilities</b>\n"
            "• /queue - ⏳ Monitor active transfers\n"
            "• /support - ✨ Contact Team CloudVerse\n"
            "• /policy - 📃 Read our usage policy\n"
            "• /help - ❓ Show this help guide\n\n"
            "💡 <b>Tip:</b> Use <b>/copy</b> to easily sync data between any of your configured remotes."
        )
    else:
        help_text = (
            "🤖 <b>CloudVerse Command Centre</b>\n\n"
            "<b>Core Commands</b>\n"
            "• /start - ⚡️ Ping the bot\n"
            "• /profile - 🪪 View your user profile\n"
            "• /storage - ☁️ Check your available storage\n"
            "• /filemanager - 🗂 Browse and manage files\n"
            "• /recyclebin - ♻️ Access deleted files\n"
            "• /settings - ⚙️ Customize your preferences\n\n"
            "<b>Authentication & Transfers</b>\n"
            "• /login - ✅ Connect your cloud account\n"
            "• /logout - ❌ Unlink your cloud account\n"
            "• /queue - ⏳ Monitor active uploads\n\n"
            "<b>Support & Info</b>\n"
            "• /support - ✨ Contact Team CloudVerse\n"
            "• /policy - 📃 Read our usage policy\n"
            "• /help - ❓ Show this help guide\n\n"
            "💡 <b>Tip:</b> Simply forward any file, photo, or video to me, and I will upload it directly to your cloud!"
        )

    if update.message:
        await update.message.reply_text(help_text, parse_mode='HTML')

def register_handlers(app):
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("help", help_command))
