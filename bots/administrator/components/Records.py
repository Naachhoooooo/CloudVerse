from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.managers.ServerManager import get_server_manager
from shared.utils.pagination import Paginator
from bots.administrator.config import SUPER_ADMIN_ID

logger = get_logger(__name__)

async def handle_records_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    m = update.message
    ctx.user_data["delete_records_page"] = 0
    await _render_delete_records(None, m, ctx)

async def handle_records_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    q = getattr(update, "callback_query", None)
    m = getattr(update, "message", None)
    data = q.data if q else None

    if q:
        await q.answer()
        
    page = ctx.user_data.get("delete_records_page", 0)
    if data == "delete_records_prev_page":
        ctx.user_data["delete_records_page"] = max(0, page - 1)
        await _render_delete_records(q, m, ctx)
        return
    elif data == "delete_records_next_page":
        ctx.user_data["delete_records_page"] = page + 1
        await _render_delete_records(q, m, ctx)
        return
    elif data == "handle_delete_records":
        await _render_delete_records(q, m, ctx)
        return
    elif data == "toggle_bot_filter":
        from bots.administrator.utils.db_utils import cycle_active_bot
        cycle_active_bot(ctx)
        await _render_delete_records(q, m, ctx)
        return

    if data and data.startswith("delete_user_typein:"):
        _, target, user_id = data.split(":", 2)
        ctx.user_data["delete_user_id"] = user_id
        ctx.user_data["delete_target"] = target
        ctx.user_data["awaiting_delete_typein"] = True
        
        user_display = await _get_user_display_list(ctx)
        label = next((l for uid, l in user_display if str(uid) == str(user_id)), user_id)
        
        # Extract username or use ID
        import re
        match = re.search(r'(@\w+)', label)
        identifier = match.group(1) if match else str(user_id)
        
        target_str = "global" if target == "all" else target
        expected_cmd = f"delete {identifier} {target_str}"
        
        prompt = f"<b>Final Confirmation</b>\n\nType the following command in chat to confirm:\n<code>{expected_cmd}</code>\n\n⚠️ <i>This action cannot be undone.</i>"
        buttons = [
            [InlineKeyboardButton("❌ Cancel", callback_data="handle_delete_records")],
        ]
        if q:
            await q.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return
        
    if data and data.startswith("delete_user_confirm:"):
        user_id = data.split(":", 1)[1]
        ctx.user_data["delete_user_id"] = user_id
        ctx.user_data["awaiting_delete_typein"] = False
        
        user_display = await _get_user_display_list(ctx)
        username = next((label for uid, label in user_display if str(uid) == str(user_id)), user_id)
        
        prompt = f"⚙️ <b>Records</b> > 🗑️ <b>Delete User</b>\n\nYou are about to delete records for: <b>{username}</b>\n\nWhere would you like to delete the records from?"
        
        from bots.administrator.utils.db_utils import get_account_repo
        buttons = []
        user_in_bots = 0
        
        for bot_name in ["drive", "mega", "rclone"]:
            if await get_account_repo(bot_name).get(telegram_id=user_id):
                user_in_bots += 1
                buttons.append([InlineKeyboardButton(f"🗑️ Delete from {bot_name.capitalize()}", callback_data=f"delete_user_typein:{bot_name}:{user_id}")])
                
        if user_in_bots > 1:
            buttons.append([InlineKeyboardButton("⚠️ Global Deletion", callback_data=f"delete_user_typein:all:{user_id}")])
            
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="handle_delete_records")])
        
        if q:
            await q.edit_message_text(prompt, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        return

    if m and ctx.user_data.get("awaiting_delete_typein"):
        user_id = ctx.user_data.get("delete_user_id")
        target = ctx.user_data.get("delete_target", "all")
        if not user_id:
            await m.reply_text("No user selected for deletion.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="handle_delete_records")]]))
            ctx.user_data["awaiting_delete_typein"] = False
            return
            
        user_display = await _get_user_display_list(ctx)
        display_name = next((label for uid, label in user_display if str(uid) == str(user_id)), f"User {user_id}")
        
        import re
        match = re.search(r'(@\w+)', display_name)
        identifier = match.group(1) if match else str(user_id)
        target_str = "global" if target == "all" else target
        
        expected = f"delete {identifier} {target_str}".lower()
        typed = m.text.strip().lower()
        
        from bots.administrator.utils.db_utils import get_account_repo
        
        # Check if they are Super Admin anywhere
        is_super = False
        for b in ["drive", "mega", "rclone"]:
            if await get_account_repo(b).is_super_admin(telegram_id=user_id):
                is_super = True
                break
                
        if is_super:
            await m.reply_text("Cannot delete Super Admin.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="handle_delete_records")]]))
            ctx.user_data["awaiting_delete_typein"] = False
            return
            
        if typed == expected:
            try:
                if target == "all":
                    for b in ["drive", "mega", "rclone"]:
                        await get_account_repo(b).delete(telegram_id=user_id)
                else:
                    await get_account_repo(target).delete(telegram_id=user_id)
                    
                ctx.user_data["awaiting_delete_typein"] = False
                ctx.user_data.pop("delete_user_id", None)
                ctx.user_data.pop("delete_target", None)
                
                target_result_str = "Global Deletion" if target == "all" else f"Deleted from {target.capitalize()}"
                
                await m.reply_text(
                    f"✅ Records for <b>{display_name}</b> have been deleted.\n\n<i>{target_result_str} completed.</i>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="handle_delete_records")]]),
                    parse_mode="HTML",
                )
            except Exception as e:
                await get_server_manager().send_error_notification(
                    error_message=f"Failed to delete records for user {user_id} (Target: {target}). Details: {str(e)}",
                    error_type="User Data Management",
                    severity="HIGH"
                )
                await m.reply_text("Critical error occurred. System admins have been notified.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="handle_delete_records")]]))
        else:
            await m.reply_text(f"❌ Incorrect confirmation. Please type exactly: <code>{expected}</code>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="handle_delete_records")]]), parse_mode="HTML")

async def _get_user_display_list(ctx):
    from bots.administrator.utils.db_utils import get_active_bot, get_all_active_users_filtered
    from shared.utils.pagination import format_user_list_label
    
    active_bot = get_active_bot(ctx)
    users = await get_all_active_users_filtered(active_bot)
    
    user_display = []
    for u in users:
        if str(u["telegram_id"]) == str(SUPER_ADMIN_ID):
            continue
        user_id = u["telegram_id"]
        label = format_user_list_label(u, include_emoji=True)
        user_display.append((user_id, label))
    return user_display

async def _render_delete_records(q, m, ctx):
    page = ctx.user_data.get("delete_records_page", 0)
    user_display = await _get_user_display_list(ctx)
    
    from bots.administrator.utils.db_utils import get_active_bot, get_filter_button_text
    
    paginator = Paginator(user_display, page, 10)
    page_users = paginator.items
    pagination_buttons = paginator.get_buttons("delete_records_prev_page", "delete_records_next_page")
    
    buttons = []
    active_bot = get_active_bot(ctx).capitalize()
    buttons.append([InlineKeyboardButton(get_filter_button_text(active_bot), callback_data="toggle_bot_filter")])
    
    for user_id, label in page_users:
        buttons.append([InlineKeyboardButton(label, callback_data=f"delete_user_confirm:{user_id}")])
        
    if pagination_buttons:
        buttons.append(pagination_buttons)
        
    buttons.append([InlineKeyboardButton("Refresh", callback_data="handle_delete_records")])
    
    text = "⚙️ <b>Records</b> > 🗑️ <b>Delete User</b>\n\nSelect a user below to permanently remove their records.\n\n<i>Note: Super Admins cannot be deleted.</i>"
    
    if q:
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    elif m:
        await m.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
