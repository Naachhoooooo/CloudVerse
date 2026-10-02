import humanize
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.utils.pagination import Paginator
from shared.utils.pagination import Paginator, format_user_list_label
from shared.core.Logger import get_logger
from bots.administrator.components.db_utils import get_account_repo, get_active_bot, cycle_active_bot, get_filter_button_text, get_usage_repo, get_transfer_repo

logger = get_logger(__name__)

def _format_display_name(user_info):
    if not user_info:
        return "Unknown"
    name = user_info.get("name")
    if name:
        return name
    return f"@{user_info.get('username')}" if user_info.get("username") else str(user_info.get("telegram_id"))

async def _render_quota_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info("[BOT] Displaying quota management user list")
    current_page = ctx.user_data.get("quota_page", 0)
    
    active_bot = get_active_bot(ctx)
    from bots.administrator.components.db_utils import get_all_active_users_filtered
    regular_users = await get_all_active_users_filtered(active_bot)
    
    q = getattr(update, "callback_query", None)
    m = getattr(update, "message", None)
    if not q and not m:
        return

    if not regular_users:
        text = "🎫 <b>User Quota Management</b>\n\nNo regular users found."
        buttons = [
            [InlineKeyboardButton("Refresh", callback_data="manage_user_quota")],
        ]
        markup = InlineKeyboardMarkup(buttons)
        if q:
            await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        else:
            await m.reply_text(text, reply_markup=markup, parse_mode="HTML")
        return

    paginator = Paginator(regular_users, current_page, 10)
    page_users = paginator.items
    pagination_buttons = paginator.get_buttons("quota_prev_page", "quota_next_page")
    text = f"🎫 <b>User Quota Management</b> - {len(regular_users)} users\n\n"
    text += "Proceed by selecting a user:\n\n"
    buttons = []
    active_bot = get_active_bot(ctx).capitalize()
    buttons.append([InlineKeyboardButton(get_filter_button_text(active_bot), callback_data="toggle_bot_filter_quota")])
    for user in page_users:
        label = format_user_list_label(user, include_emoji=True)
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"quota_user:{user['telegram_id']}")
        ])
    if pagination_buttons:
        buttons.append(pagination_buttons)
    buttons.extend([
        [InlineKeyboardButton("Refresh", callback_data="manage_user_quota")],
    ])
    markup = InlineKeyboardMarkup(buttons)
    if q:
        await q.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await m.reply_text(text, reply_markup=markup, parse_mode="HTML")

async def _render_quota_user_details(q, ctx, user_id, usage_repo):
    logger.info(f"[BOT] Viewing quota for user {user_id}")
    account_repo = get_account_repo(get_active_bot(ctx))
    regular_users = await account_repo.get_by_role(role="whitelisted")
    user_info = next((u for u in regular_users if str(u["telegram_id"]) == str(user_id)), None)
    if not user_info:
        await q.edit_message_text(
            "❌ User not found or no longer a regular user.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_user_quota")]]),
        )
        return

    daily_limit = await usage_repo.get_limit(telegram_id=user_id)
    daily_used = await usage_repo.get_usage(telegram_id=user_id)
    
    usage_row = await usage_repo.get_usage_details(telegram_id=user_id) or {}
    usage_stats = {
        "today": {
            "count": usage_row.get("transfer_count_today", 0),
            "size": usage_row.get("transferred_today", 0),
        },
        "week": {
            "count": usage_row.get("transfer_count_this_week", 0),
            "size": usage_row.get("transferred_this_week", 0),
        },
        "month": {
            "count": usage_row.get("transfer_count_this_month", 0),
            "size": usage_row.get("transferred_this_month", 0),
        },
        "lifetime": {
            "count": usage_row.get("transfer_count_lifetime", 0),
            "size": usage_row.get("transferred_lifetime", 0),
        },
    }

    transfer_repo = get_transfer_repo(get_active_bot(ctx))
    completed_transfers = await transfer_repo.get_completed(telegram_id=str(user_id)) if transfer_repo else []
    last_upload = completed_transfers[0] if completed_transfers else None

    display_name = user_info.get("name") or "N/A"
    username_display = f"@{user_info.get('username')}" if user_info.get("username") else "N/A"
    
    joined_timestamp = user_info.get("handled_at", "Unknown")
    if joined_timestamp != "Unknown" and isinstance(joined_timestamp, str) and "T" in joined_timestamp:
        joined_timestamp = joined_timestamp.replace("T", " ").split(".")[0]

    last_upload_text = "No uploads yet"
    if last_upload:
        file_name = last_upload["file_name"]
        if len(file_name) > 30:
            file_name = file_name[:30] + "..."
        file_size = humanize.naturalsize(last_upload["file_size"], binary=True)
        upload_time = (last_upload["completed_at"][:19] if last_upload.get("completed_at") else "Unknown").replace("T", " ")
        last_upload_text = f"File: <code>{file_name}</code>\nSize: <b>{file_size}</b>\nDate: {upload_time}"

    remaining = "Unlimited" if daily_limit is None else daily_limit - daily_used
    text = (
        f"🎫 <b>Quotas</b> > 👤 <b>{display_name}</b>\n\n"
        f"Name: {display_name}\n"
        f"Username: <code>{username_display}</code>\n"
        f"Telegram ID: <code>{user_id}</code>\n"
        f"Joined: <code>{joined_timestamp}</code>\n\n"
        f"<b>Current Quota Allocation:</b>\n\n"
        f"Daily Upload Limit: <b>{daily_limit if daily_limit is not None else 'Unlimited'}</b>\n"
        f"Used Today: <b>{daily_used}</b>\n"
        f"Remaining: <b>{remaining}</b>\n\n"
        f"<b>Usage Statistics:</b>\n\n"
        f"Today: <b>{usage_stats['today']['count']}</b> files ({humanize.naturalsize(usage_stats['today']['size'], binary=True)})\n"
        f"Last Week: <b>{usage_stats['week']['count']}</b> files ({humanize.naturalsize(usage_stats['week']['size'], binary=True)})\n"
        f"Last Month: <b>{usage_stats['month']['count']}</b> files ({humanize.naturalsize(usage_stats['month']['size'], binary=True)})\n"
        f"Lifetime: <b>{usage_stats['lifetime']['count']}</b> files ({humanize.naturalsize(usage_stats['lifetime']['size'], binary=True)})\n\n"
        f"<b>Last Upload:</b>\n{last_upload_text}\n"
    )
    buttons = [
        [
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit_quota:{user_id}"),
            InlineKeyboardButton("🚩 Flag", callback_data=f"flag_user:{user_id}")
        ],
        [InlineKeyboardButton("Back", callback_data="manage_user_quota")],
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _render_edit_quota_prompt(q, ctx, user_id, usage_repo):
    logger.info(f"[BOT] Editing quota for user {user_id}")
    account_repo = get_account_repo(get_active_bot(ctx))
    regular_users = await account_repo.get_by_role(role="whitelisted")
    user_info = next((u for u in regular_users if str(u["telegram_id"]) == str(user_id)), None)
    if not user_info:
        await q.edit_message_text(
            "❌ User not found or no longer a regular user.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_user_quota")]]),
        )
        return

    display_name = _format_display_name(user_info)
    ctx.user_data["editing_quota_user_id"] = user_id
    ctx.user_data["awaiting_quota_input"] = True
    daily_limit = await usage_repo.get_limit(telegram_id=user_id)
    text = (
        f"🎫 <b>Quotas</b> > 👤 <b>{display_name}</b> > ✏️ Edit Limit\n\n"
        f"Enter the new daily upload limit for <b>{display_name}</b>:\n\n"
        f"<i>Current limit: {daily_limit if daily_limit is not None else 'Unlimited'}</i>\n\n"
        f"Select a preset below, or type a custom number."
    )
    buttons = [
        [
            InlineKeyboardButton("3", callback_data=f"set_quota:{user_id}:3"),
            InlineKeyboardButton("5", callback_data=f"set_quota:{user_id}:5"),
            InlineKeyboardButton("Unlimited", callback_data=f"set_quota:{user_id}:0"),
        ],
        [InlineKeyboardButton("♻️ Reset Quota", callback_data=f"reset_usage:{user_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"quota_user:{user_id}")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def _handle_quota_input(m, ctx, usage_repo):
    user_id = ctx.user_data.get("editing_quota_user_id")
    if not user_id:
        await m.reply_text("❌ No user selected for quota editing.")
        return
    try:
        user_input = m.text.strip().lower()
        new_limit = 0 if user_input == "unlimited" else int(user_input)
        if new_limit < 0:
            raise ValueError("Limit cannot be negative")

        account_repo = get_account_repo(get_active_bot(ctx))
        regular_users = await account_repo.get_by_role(role="whitelisted")
        user_info = next((u for u in regular_users if str(u["telegram_id"]) == str(user_id)), None)
        display_name = _format_display_name(user_info) if user_info else f"User {user_id}"

        success = await usage_repo.update_limit(telegram_id=user_id, limit=new_limit if new_limit > 0 else None)
        if success:
            ctx.user_data.pop("awaiting_quota_input", None)
            ctx.user_data.pop("editing_quota_user_id", None)
            limit_text = "unlimited" if new_limit == 0 else str(new_limit)
            logger.info(f"[BOT] Quota updated for {user_id}: {limit_text}")
            await m.reply_text(
                f"✅ Quota limit updated successfully!\n\n<b>{display_name}</b> now has a daily limit of <b>{limit_text}</b> uploads.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👤 View User", callback_data=f"quota_user:{user_id}"), InlineKeyboardButton("Back to List", callback_data="manage_user_quota")]]),
            )
        else:
            logger.warning(f"[BOT] Quota update returned failure for user {user_id}")
            await m.reply_text(
                "❌ Failed to update quota limit. Please try again.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Try Again", callback_data=f"edit_quota:{user_id}"), InlineKeyboardButton("Back", callback_data=f"quota_user:{user_id}")]]),
            )
    except ValueError:
        logger.warning(f"[BOT] Invalid quota input from user {m.from_user.id if m.from_user else '?'}")
        await m.reply_text(
            "❌ Invalid input. Please enter a valid number (0 or positive integer) or 'Unlimited'.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Try Again", callback_data=f"edit_quota:{user_id}"), InlineKeyboardButton("Back", callback_data=f"quota_user:{user_id}")]])
        )
    finally:
        ctx.user_data["awaiting_quota_input"] = False
        ctx.user_data.pop("editing_quota_user_id", None)

async def _handle_flag_user(q, ctx, user_id, usage_repo):
    from shared.components.TeamCloudverse import Flags_TOPIC_ID, TeamCloudverse_GROUP_CHAT_ID as GROUP_CHAT_ID
    if not (GROUP_CHAT_ID and Flags_TOPIC_ID):
        await q.answer("Flags topic not configured.", show_alert=True)
        return

    regular_users = await ctx.bot_data["account_repo"].get_by_role(role="whitelisted")
    user_info = next((u for u in regular_users if str(u["telegram_id"]) == str(user_id)), None)
    if not user_info:
        await q.answer("User not found.", show_alert=True)
        return

    name = user_info.get("name", "Unknown")
    username = user_info.get("username")
    flagged_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    admin_name = q.from_user.full_name
    admin_id = q.from_user.id
    
    text = (
        f"🚩 **User Flagged** 🚩\n\n"
        f"• **Name:** {name}\n"
        f"• **Username:** @{username or 'N/A'}\n"
        f"• **ID:** `{user_id}`\n\n"
        f"• **Flagged By:** {admin_name} (`{admin_id}`)\n"
        f"• **Flagged At:** {flagged_at}\n\n"
        f"• **Source:** Quota Management Panel"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⛔ Permanent Ban", callback_data=f"flag_ban_perm:{user_id}")],
        [InlineKeyboardButton("⏳ Temporary Ban (24h)", callback_data=f"flag_ban_temp:{user_id}")]
    ])
    try:
        await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, message_thread_id=int(Flags_TOPIC_ID), text=text, reply_markup=keyboard, parse_mode="HTML")
        await q.answer("User flagged successfully. Report sent to Flags topic.", show_alert=True)
    except Exception as e:
        logger.error(f"Failed to send flag report: {e}")
        await q.answer("Failed to send flag report.", show_alert=True)

async def _process_flag_ban(q, ctx, action, telegram_id):
    repo = get_account_repo(get_active_bot(ctx))
    is_perm = (action == "flag_ban_perm")
    restriction_type = "permanent" if is_perm else "temporary"
    period = None if is_perm else 24
    
    success = await repo.ban(telegram_id=telegram_id, restriction_type=restriction_type, period=period, action_by=str(q.from_user.id))
    if success:
        admin_username = q.from_user.username
        admin_mention = f"@{admin_username}" if admin_username else q.from_user.full_name
        action_text = "Banned" if is_perm else "Restricted"
        new_text = q.message.text + f"\n\n✅ {action_text} by {admin_mention}"
        await q.edit_message_text(new_text, reply_markup=None)
        try:
            note = "permanently restricted" if is_perm else "restricted for 24 hours"
            await ctx.bot.send_message(chat_id=telegram_id, text=f"⚠️ Your account has been {note} by Team CloudVerse.")
        except Exception:
            pass
        await q.answer(f"User {action_text.lower()}.", show_alert=True)
    else:
        await q.answer("Failed to ban user.", show_alert=True)

async def _handle_set_quota_callback(q, ctx, user_id, limit_str, usage_repo):
    new_limit = int(limit_str)
    success = await usage_repo.update_limit(telegram_id=user_id, limit=new_limit if new_limit > 0 else None)
    if success:
        await q.answer("Quota updated.", show_alert=True)
        await _render_quota_user_details(q, ctx, user_id, usage_repo)
    else:
        await q.answer("Failed to update quota.", show_alert=True)

async def _handle_reset_usage_callback(q, ctx, user_id, usage_repo):
    success = await usage_repo.reset_daily(telegram_id=user_id)
    if success:
        await q.answer("User's daily quota usage has been reset.", show_alert=True)
        await _render_quota_user_details(q, ctx, user_id, usage_repo)
    else:
        await q.answer("Failed to reset quota usage.", show_alert=True)

async def handle_quota_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    await _render_quota_list(update, ctx)

async def handle_quota_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    q = update.callback_query
    m = update.message
    usage_repo = get_usage_repo(get_active_bot(ctx))

    if q:
        await q.answer()
        data = getattr(q, "data", None)
        if data == "quota_prev_page":
            current_page = ctx.user_data.get("quota_page", 0)
            ctx.user_data["quota_page"] = max(0, current_page - 1)
            await _render_quota_list(update, ctx)
        elif data == "quota_next_page":
            current_page = ctx.user_data.get("quota_page", 0)
            ctx.user_data["quota_page"] = current_page + 1
            await _render_quota_list(update, ctx)
        elif data.startswith("manage_user_quota"):
            parts = data.split(":")
            if len(parts) > 1:
                await _render_quota_user_details(q, ctx, parts[1], usage_repo)
            else:
                await _render_quota_list(update, ctx)
        elif data.startswith("quota_user:"):
            active_bot = get_active_bot(ctx)
            user_id = data.split(":", 1)[1]
            if active_bot == 'all':
                from bots.administrator.components.GlobalProfile import build_global_user_profile, _render_global_profile
                global_user_info = await build_global_user_profile(user_id)
                if global_user_info['telegram_id']:
                    await _render_global_profile(q.message, ctx, global_user_info, query_to_edit=q)
            else:
                await _render_quota_user_details(q, ctx, user_id, usage_repo)
        elif data.startswith("edit_quota:"):
            await _render_edit_quota_prompt(q, ctx, data.split(":", 1)[1], usage_repo)
        elif data.startswith("set_quota:"):
            _, user_id, limit_str = data.split(":")
            await _handle_set_quota_callback(q, ctx, user_id, limit_str, usage_repo)
        elif data.startswith("reset_usage:"):
            _, user_id = data.split(":")
            await _handle_reset_usage_callback(q, ctx, user_id, usage_repo)
        elif data.startswith("flag_user:"):
            _, user_id = data.split(":")
            await _handle_flag_user(q, ctx, user_id, usage_repo)
        elif data.startswith("flag_ban_perm:") or data.startswith("flag_ban_temp:"):
            parts = data.split(":")
            await _process_flag_ban(q, ctx, parts[1], parts[2])
        elif data == "toggle_bot_filter_quota":
            cycle_active_bot(ctx)
            await _render_quota_list(update, ctx)
    elif m and ctx.user_data.get("awaiting_quota_input"):
        await _handle_quota_input(m, ctx, usage_repo)
