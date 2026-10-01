from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.managers.MaintenanceManager import get_maintenance_manager
from shared.core.UserState import UserState, UserStateEnum
from shared.core.ErrorHandler import handle_errors

logger = get_logger(__name__)

async def _render_maintenance_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    q = getattr(update, "callback_query", None)
    m = getattr(update, "message", None)
    telegram_id = q.from_user.id if q else (m.from_user.id if m else None)
    
    if not telegram_id:
        return

    is_super = await ctx.bot_data["account_repo"].is_super_admin(telegram_id=telegram_id)
    if not is_super and not await ctx.bot_data["account_repo"].is_admin(telegram_id=telegram_id):
        if q:
            await q.answer("❌ Access denied. Super Admin privileges required.", show_alert=True)
        return

    manager = get_maintenance_manager()
    
    statuses = {}
    for b in ["drive", "mega", "rclone"]:
        statuses[b] = await manager.get_status(bot_name=b)

    status_text = "🔧 <b>System Maintenance Overview</b>\n\n"
    for b in ["drive", "mega", "rclone"]:
        state = "🔴 Offline (Maintenance)" if statuses[b] else "🟢 Online"
        status_text += f"• {b.capitalize()} Bot: {state}\n"
    
    custom_notice = await manager.get_custom_notice(bot_name="drive") # Assuming notice is global or sync'd, checking drive is fine
    MAINTENANCE_NOTICE = custom_notice if custom_notice else (
        "🔧 <b>CloudVerse is currently under maintenance</b>\n\n"
        "We're performing scheduled updates to improve your experience.\n"
        "Please try again shortly.\n\n"
        "🛡️ <b>Team CloudVerse</b>"
    )
    
    text = f"🔧 <b>Maintenance Mode Control</b>\n\n{status_text}\n"
    if is_super:
        text += "<b>Super Admin Controls:</b>\n• Toggle maintenance mode per bot\n\n"
    else:
        text += "<b>Admin View Only</b>\n• Only Super Admins can modify settings\n\n"
        
    preview_text = MAINTENANCE_NOTICE[:200] + "..." if len(MAINTENANCE_NOTICE) > 200 else MAINTENANCE_NOTICE
    text += f"<b>Notice Preview:</b>\n<blockquote>{preview_text}</blockquote>"

    buttons = []
    
    if is_super:
        buttons.append([
            InlineKeyboardButton(f"{'🟢' if statuses['drive'] else '🔴'} Drive Bot", callback_data="maint_toggle:drive"),
            InlineKeyboardButton(f"{'🟢' if statuses['mega'] else '🔴'} Mega Bot", callback_data="maint_toggle:mega"),
            InlineKeyboardButton(f"{'🟢' if statuses['rclone'] else '🔴'} Rclone Bot", callback_data="maint_toggle:rclone"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton(f"🔒 Drive", callback_data="noop"),
            InlineKeyboardButton(f"🔒 Mega", callback_data="noop"),
            InlineKeyboardButton(f"🔒 Rclone", callback_data="noop"),
        ])
    
    btn_text_notice = "📝 Update Notice" if is_super else "🔒 Update Notice"
    buttons.append([InlineKeyboardButton(btn_text_notice, callback_data="maintenance_update_notice")])
    
    # We removed broadcast logic from maintenance menu to avoid cross-coupling. Use /broadcast.
    
    buttons.append([InlineKeyboardButton("Refresh", callback_data="maintenance_menu")])

    markup = InlineKeyboardMarkup(buttons)
    if q:
        await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    elif m:
        await m.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def handle_maintenance_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _render_maintenance_menu(update, ctx)

async def handle_maintenance_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or not q.from_user:
        return

    telegram_id = q.from_user.id
    username = q.from_user.username or "Unknown"
    is_super = await ctx.bot_data["account_repo"].is_super_admin(telegram_id=telegram_id)
    manager = get_maintenance_manager()
    data = q.data

    if data == "maintenance_menu":
        await _render_maintenance_menu(update, ctx)
    elif data.startswith("maint_toggle:"):
        if not is_super:
            await q.answer("❌ Access denied.", show_alert=True)
            return
        bot_name = data.split(":")[1]
        is_enabled = await manager.get_status(bot_name=bot_name)
        if not is_enabled:
            success = await manager.enable_maintenance(telegram_id, username, bot_name=bot_name)
            if success:
                await q.answer(f"✅ {bot_name.capitalize()} Maintenance Activated!", show_alert=True)
                await manager.notify_team("ACTIVATED", username)
        else:
            success = await manager.disable_maintenance(telegram_id, username, bot_name=bot_name)
            if success:
                await q.answer(f"✅ {bot_name.capitalize()} Maintenance Deactivated!", show_alert=True)
                await manager.notify_team("DEACTIVATED", username)
        await _render_maintenance_menu(update, ctx)
    elif data == "maintenance_update_notice":
        if not is_super:
            await q.answer("❌ Access denied. Only Super Admins can update the maintenance notice.", show_alert=True)
            return
            
        custom_notice = await manager.get_custom_notice(bot_name="drive")
        current = custom_notice if custom_notice else (
            "🔧 <b>CloudVerse is currently under maintenance</b>\n\n"
            "We're performing scheduled updates to improve your experience.\n"
            "Please try again shortly.\n\n"
            "🛡️ <b>Team CloudVerse</b>"
        )
        
        text = f"📝 <b>Update Maintenance Notice</b>\n\nPlease type the new custom maintenance notice you would like to display to users when maintenance is active.\n\n<b>Current Notice:</b>\n<blockquote>{current}</blockquote>\n\nType your message below or send <code>/cancel</code> to abort:"
        await q.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="maintenance_menu")]]),
            parse_mode="HTML",
        )
        ctx.user_data["awaiting_maintenance_notice"] = True

@handle_errors
async def handle_maintenance_notice_input(message, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("awaiting_maintenance_notice"):
        return
        
    ctx.user_data["awaiting_maintenance_notice"] = False
    
    if message.text.strip().lower() == "/cancel":
        await message.reply_text(
            "❌ Update cancelled.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="maintenance_menu")]]),
            parse_mode="HTML"
        )
        return
        
    new_notice = message.text.strip()
    
    manager = get_maintenance_manager()
    for b in ["drive", "mega", "rclone"]:
        await manager.set_custom_notice(new_notice, bot_name=b)
        
    await message.reply_text(
        "✅ <b>Maintenance Notice Updated Globally</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Menu", callback_data="maintenance_menu")]]),
        parse_mode="HTML"
    )
