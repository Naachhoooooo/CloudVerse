"""Help component for administrator bot."""

from telegram import Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import admin_required

logger = get_logger(__name__)

@handle_errors
@admin_required
async def handle_help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Command handler for /help."""
    help_text = (
        "🛡️ <b>Administrator Help Guide</b>\n\n"
        "<b>Access Control</b>\n"
        "• /start - ⚡️ Ping the bot\n"
        "• /search - ⌕ Lookup and manage a specific user\n"
        "• /pending - ⏳ Review pending access requests\n"
        "• /whitelisted - ✅ Manage approved users\n"
        "• /blacklisted - 🚫 Manage banned users\n\n"
        "<b>Role Management</b>\n"
        "• /admin - ⭐️ Manage standard administrators\n"
        "• /superadmin - 👑 Manage super administrators\n\n"
        "<b>System & Server</b>\n"
        "• /quota - ⚖️ Adjust storage quotas\n"
        "• /server - 모 Monitor server performance (CPU/RAM)\n"
        "• /records - 🗑 Clean up database records\n"
        "• /maintenance - 🛠 Toggle system maintenance mode\n"
        "• /session - 📱 Manage backend sessions\n"
        "• /domain - 🔗 Manage allowed download domains\n\n"
        "<b>Communication</b>\n"
        "• /broadcast - 📺 Send network-wide announcements\n"
        "• /help - ❓ Show this help guide\n\n"
        "💡 <b>Tip:</b> Most commands open interactive menus. Use the inline buttons to easily navigate and make changes."
    )
    
    if update.message:
        await update.message.reply_text(help_text, parse_mode='HTML')
