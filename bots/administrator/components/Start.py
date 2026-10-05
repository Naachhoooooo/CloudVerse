from telegram import Update
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from bots.administrator.components.db_utils import get_account_repo

logger = get_logger(__name__)

@handle_errors
async def handle_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command for the Administrator Gateway."""
    user_name = update.effective_user.first_name or "Administrator"
    
    # Check role for transparency
    account_repo = get_account_repo('drive')
    is_super = await account_repo.is_super_admin(update.effective_user.id)
    role_str = "Super Administrator" if is_super else "Administrator"
    
    welcome_message = (
        f"👋 Welcome to the <b>CloudVerse Administrator Gateway</b>.\n\n"
        f"🔐 <b>Authorization Level:</b> <code>{role_str}</code>\n\n"
        f"🛡 This secure terminal provides central management for the Drive, Mega, and Rclone infrastructure.\n\n"
        f"To get started, please use the <b>Menu</b> button below to access administrative tools."
    )
    
    await update.message.reply_text(welcome_message, parse_mode="HTML")
