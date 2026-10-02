from telegram import Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from bots.administrator.components.db_utils import get_account_repo

logger = get_logger(__name__)

@handle_errors
async def handle_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command for the Administrator Gateway."""
    user = update.effective_user
    
    welcome_message = (
        f"👋 Welcome to the *CloudVerse Administrator Gateway*, {user.first_name}!\n\n"
        "This bot provides central management for the Drive, Mega, and Rclone bots.\n"
        "To get started, please use the *Menu* button to explore the available administrative commands."
    )
    
    await update.message.reply_text(welcome_message, parse_mode="Markdown")
