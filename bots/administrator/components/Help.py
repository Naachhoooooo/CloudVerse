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
        "Here are the commands available to manage the CloudVerse ecosystem:\n\n"
        "<b>Access Control & Users</b>\n"
        "• /search (or /user) - Search and manage a specific user\n"
        "• /pending - Review pending access requests\n"
        "• /whitelisted - Manage approved users\n"
        "• /blacklisted - Manage banned users\n"
        "• /admin - Manage standard administrators\n"
        "• /superadmin - Manage super administrators\n\n"
        "<b>System & Limits</b>\n"
        "• /quota - Adjust global or specific user quotas\n"
        "• /server - Monitor server bandwidth, CPU, and RAM\n"
        "• /records - Manage and clean system database records\n"
        "• /maintenance - Toggle system maintenance mode\n"
        "• /domain - Manage allowed URL download domains\n"
        "• /session - Manage backend Telethon client sessions\n\n"
        "<b>Communication</b>\n"
        "• /broadcast - Send a broadcast message to users\n\n"
        "<i>💡 Tip: Most of these commands open interactive menus where you can navigate and make changes dynamically using buttons.</i>"
    )
    
    if update.message:
        await update.message.reply_text(help_text, parse_mode='HTML')
