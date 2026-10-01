"""Search component for administrator bot."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.core.ErrorHandler import handle_errors
from shared.managers.AccessManager import admin_required

logger = get_logger(__name__)

from bots.administrator.utils.db_utils import get_account_repo

class DummyQuery:
    def __init__(self, message, user):
        self.message = message
        self.from_user = user
        self.data = None
        
    async def answer(self, *args, **kwargs):
        pass

    async def edit_message_text(self, text, *args, **kwargs):
        return await self.message.reply_text(text, *args, **kwargs)

@handle_errors
@admin_required
async def handle_search_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Command handler for /search <telegram_id_or_username>."""
    if not update.message or not update.message.text:
        return

    parts = update.message.text.split()
    if len(parts) < 2:
        await update.message.reply_text("Usage: /search <telegram_id_or_@username>")
        return

    search_term = parts[1]
    
    from bots.administrator.components.GlobalProfile import build_global_user_profile, _render_global_profile
    
    global_user_info = await build_global_user_profile(search_term)

    if not global_user_info['telegram_id']:
        await update.message.reply_text(f"User '{search_term}' not found in any database.")
        return

    await _render_global_profile(update.message, ctx, global_user_info)

async def handle_search_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.data:
        return
        
    await q.answer()
    
    if q.data == "noop":
        await q.delete_message()
        return
        
    if q.data.startswith("search_manage:"):
        _, bot_name, user_id = q.data.split(":")
        
        account_repo = get_account_repo(bot_name)
        user_data = await account_repo.get(telegram_id=user_id)
        if not user_data:
            await q.edit_message_text(f"User is no longer registered in {bot_name.capitalize()} database.")
            return
            
        role = user_data.get("role", "Unknown")
        
        dummy_q = DummyQuery(q.message, update.effective_user)
        current_user_id = update.effective_user.id
        
        # Set active context to the requested bot
        if ctx.user_data is None:
            ctx.user_data = {}
        ctx.user_data['filter_bot'] = bot_name
        
        if role == "pending":
            from bots.administrator.components.ManageRequest import _handle_pending_select
            await _handle_pending_select(dummy_q, ctx, str(user_id))
        elif role == "whitelisted":
            from bots.administrator.components.ManageWhitelisted import _handle_whitelist_select
            is_current_admin = await account_repo.is_admin(telegram_id=current_user_id)
            is_current_super_admin = await account_repo.is_super_admin(telegram_id=current_user_id)
            await _handle_whitelist_select(dummy_q, ctx, str(user_id), current_user_id, is_current_admin, is_current_super_admin)
        elif role == "blacklisted":
            from bots.administrator.components.ManageBlacklisted import _handle_blacklist_select
            is_current_admin = await account_repo.is_admin(telegram_id=current_user_id)
            await _handle_blacklist_select(dummy_q, ctx, str(user_id), current_user_id, is_current_admin)
        elif role == "admin":
            from bots.administrator.components.ManageAdmin import _handle_admin_select
            is_current_super_admin = await account_repo.is_super_admin(telegram_id=current_user_id)
            await _handle_admin_select(dummy_q, ctx, str(user_id), current_user_id, is_current_super_admin)
        elif role == "super_admin":
            from bots.administrator.components.ManageSuperadmin import _handle_super_admin_select
            is_current_super_admin = await account_repo.is_super_admin(telegram_id=current_user_id)
            await _handle_super_admin_select(dummy_q, ctx, str(user_id), current_user_id, is_current_super_admin)
        else:
            await q.edit_message_text(f"Unknown or unmanageable role: {role}")

async def handle_inline_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline query handler to search for users by ID or username."""
    query = update.inline_query.query.strip()
    if not query:
        return
        
    bot_contexts = ['drive', 'mega', 'rclone']
    
    # Check authorization
    user_id = update.inline_query.from_user.id
    is_admin = False
    for b in bot_contexts:
        repo = get_account_repo(b)
        if await repo.is_admin(user_id) or await repo.is_super_admin(user_id):
            is_admin = True
            break
            
    if not is_admin:
        return
        
    found_users = []
    
    from telegram import InlineQueryResultArticle, InputTextMessageContent
    
    for bot_name in bot_contexts:
        account_repo = get_account_repo(bot_name)
        if query.startswith('@'):
            res = await account_repo.get_all(filters={'username': query[1:]})
            if res:
                found_users.extend([(u, bot_name) for u in res])
        else:
            # Exact match by ID
            res = await account_repo.get(telegram_id=query)
            if res:
                found_users.append((res, bot_name))
                
    # Deduplicate
    seen = set()
    unique_users = []
    for u, b in found_users:
        if u['telegram_id'] not in seen:
            seen.add(u['telegram_id'])
            unique_users.append((u, b))
            
    results = []
    for user_data, bot_name in unique_users:
        uid = user_data['telegram_id']
        username = user_data.get('username')
        name = user_data.get('name', 'Unknown')
        role = user_data.get('role', 'Unknown')
        
        display = f"{name} (@{username})" if username else name
        desc = f"ID: {uid} | Role: {role.capitalize()} | DB: {bot_name.capitalize()}"
        
        results.append(
            InlineQueryResultArticle(
                id=str(uid),
                title=display,
                description=desc,
                input_message_content=InputTextMessageContent(
                    f"/search {uid}"
                )
            )
        )
        
    if results:
        await update.inline_query.answer(results, cache_time=10)
    else:
        await update.inline_query.answer([], cache_time=10)
