import re
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.managers.AccessManager import access_required
from shared.core.UserState import UserStateEnum, UserState
from shared.core.Logger import get_logger
from shared.managers.ServerManager import get_server_manager
from datetime import datetime

logger = get_logger(__name__)

def _md_to_html(text: str) -> str:
    """Convert subset of markdown to Telegram HTML (bold, italic, underline, code)."""
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # Bold: **text** or __text__ (where __ used as bold in these docs)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Italic: _text_ or *text*
    text = re.sub(r'(?<!_)_([^_]+?)_(?!_)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\*)\*([^\*]+?)\*(?!\*)', r'<i>\1</i>', text)
    # Underline: __text__ (double underscore not already consumed by bold)
    text = re.sub(r'__(.+?)__', r'<u>\1</u>', text)
    # Inline code: `text`
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    # Headers: #, ##, ### Text
    text = re.sub(r'^#{1,3}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    return text

ROOT_DIR = Path(__file__).parent.parent.parent

SHARED_LEGAL_FILE = ROOT_DIR / "shared" / "assets" / "Policy.md"

async def load_policy_content(file_path: str) -> str:
    """Helper function to load policy content from markdown file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load policy file {file_path}: {e}")
        return "? Failed to load policy document. Please try again later."

async def save_policy_content(file_path: str, content: str):
    """Save updated policy content with new last updated date"""
    try:
        current_date = datetime.now().strftime("%d %B %Y")
        # Replace or add Last Updated line (matches *Last Updated: DD Month YYYY*)
        updated_content = re.sub(
            r'\*Last Updated: \d+ \w+ \d{4}\*',
            f'*Last Updated: {current_date}*',
            content
        )
        # If no match, add at the top
        if '*Last Updated:' not in updated_content:
            updated_content = f"*Last Updated: {current_date}*\n\n" + updated_content
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        return True
    except Exception as e:
        logger.error(f"Failed to save policy file {file_path}: {e}")
        return False

async def show_policies_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    if ctx.user_data is None:
        ctx.user_data = {}
        
    content = await load_policy_content(str(SHARED_LEGAL_FILE))
    # Keep it under Telegram limits (~4096). The combined policy is ~3300 chars, which fits.
    html_content = _md_to_html(content)
    
    text = f"<b>📜 CloudVerse Policies</b>\n\n{html_content}"
    
    buttons = []
    
    telegram_id = update.effective_user.id if update.effective_user else None
    if telegram_id:
        account_repo = ctx.bot_data.get('account_repo')
        if account_repo:
            is_admin = await account_repo.is_admin(telegram_id=telegram_id)
            is_super_admin = await account_repo.is_super_admin(telegram_id=telegram_id)
            if is_admin or is_super_admin:
                buttons.append([InlineKeyboardButton("Update", callback_data="prompt_update_policy")])
                
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    
    if update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='HTML')
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode='HTML')
    
    ctx.user_data["state"] = UserState(ctx)
    ctx.user_data["state"].set_state(UserStateEnum.TERMS)

async def handle_policy_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    q = getattr(update, 'callback_query', None)
    m = getattr(update, 'message', None)
    # Enforce admin check for policy updates
    telegram_id = update.effective_user.id if update.effective_user else None
    if telegram_id:
        account_repo = ctx.bot_data.get('account_repo')
        is_admin = await account_repo.is_admin(telegram_id=telegram_id)
        is_super_admin = await account_repo.is_super_admin(telegram_id=telegram_id)
        if not (is_admin or is_super_admin):
            if q:
                await q.answer("You do not have permission to update policies.", show_alert=True)
            return

    if q:
        data = q.data
        await q.answer()
        if data == "update_policies":
            await show_policies_menu(update, ctx)
            return
        elif data == "prompt_update_policy":
            content = await load_policy_content(str(SHARED_LEGAL_FILE))
            
            # Escape HTML to prevent Telegram parsing errors, but leave markdown tags raw so they can be copied
            safe_content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            text = (
                f"📝 <b>Update CloudVerse Policies</b>\n\n"
                f"{safe_content}\n\n"
                f"<i>Please manually copy the above text, edit it, and send it back with your changes (Markdown formatting like **bold** and __italic__ is supported). Or send <code>/cancel</code> to abort.</i>"
            )
            # Ensure text fits in one message or crop instructions (Telegram limit 4096)
            if len(text) > 4096:
                text = text[:4000] + "...\n\n<i>[Text truncated. Copying might miss parts. Send full markdown back]</i>"
            await q.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="policies_menu")]]),
                parse_mode="HTML"
            )
            ctx.user_data["awaiting_policy_update"] = "policy"
            return
    if m and ctx.user_data.get("awaiting_policy_update") == "policy":
        # Respect built-in Telegram formatting by extracting HTML and converting back to our Markdown
        if getattr(m, 'text_html', None):
            import html
            raw_html = m.text_html
            raw_html = re.sub(r'<b>(.*?)</b>', r'**\1**', raw_html, flags=re.DOTALL | re.IGNORECASE)
            raw_html = re.sub(r'<strong>(.*?)</strong>', r'**\1**', raw_html, flags=re.DOTALL | re.IGNORECASE)
            raw_html = re.sub(r'<i>(.*?)</i>', r'_\1_', raw_html, flags=re.DOTALL | re.IGNORECASE)
            raw_html = re.sub(r'<em>(.*?)</em>', r'_\1_', raw_html, flags=re.DOTALL | re.IGNORECASE)
            raw_html = re.sub(r'<u>(.*?)</u>', r'__\1__', raw_html, flags=re.DOTALL | re.IGNORECASE)
            raw_html = re.sub(r'<code>(.*?)</code>', r'`\1`', raw_html, flags=re.DOTALL | re.IGNORECASE)
            new_content = html.unescape(raw_html)
        else:
            new_content = m.text
            
        success = await save_policy_content(str(SHARED_LEGAL_FILE), new_content)
        if success:
            await m.reply_text(
                f"✅ Policies updated successfully.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("View Updated", callback_data="policies_menu")]])
            )
        else:
            await get_server_manager().send_error_notification(
                error_message=f"Failed to update Policies",
                error_type="Policy Management",
                severity="HIGH"
            )
            await m.reply_text(
                f"❌ Failed to update Policies. System admins notified.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Try Again", callback_data="prompt_update_policy")]])
            )
        ctx.user_data.pop("awaiting_policy_update", None)

def register_handlers(app):
    from telegram.ext import CommandHandler, CallbackQueryHandler
    app.add_handler(CommandHandler("policy", show_policies_menu))
    app.add_handler(CommandHandler("policies", show_policies_menu))
    app.add_handler(CommandHandler("terms", show_policies_menu))
    app.add_handler(CallbackQueryHandler(handle_policy_update, pattern=r"^(update_policies|prompt_update_policy)$"))
    app.add_handler(CallbackQueryHandler(show_policies_menu, pattern=r"^policies_menu$"))

