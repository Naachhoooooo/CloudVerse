from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.core.Logger import get_logger
from shared.managers.BroadcastManager import get_broadcast_manager
from shared.core.UserState import UserState, UserStateEnum
from shared.core.ErrorHandler import handle_errors

logger = get_logger(__name__)

CANCEL_BUTTON = "❌ Cancel"
BROADCAST_MENU_TEXT = (
    "📡 <b>Broadcast Studio</b>\n\n"
    "📋 <b>How it works:</b>\n"
    "├ Your message is forwarded to Broadcasts topic\n"
    "├ There you can review and proceed further\n"
    "├ <b>Super Admins</b> can approve instantly with one click\n"
    "└ <b>Admins</b> require 2 unique approvals\n\n"
    "📊 <b>Target Audience:</b> <code>{user_count}</code> active users\n"
    "├ Drive: <code>{drive_count}</code> users\n"
    "├ Mega: <code>{mega_count}</code> users\n"
    "└ Rclone: <code>{rclone_count}</code> users\n\n"
    "✏️ Send your broadcast now.\n\n"
    "<i>Supports text, photo, video, document, audio and voice.</i>"
)

async def _render_broadcast_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    q = getattr(update, "callback_query", None)
    m = getattr(update, "message", None)
    
    user = q.from_user if q else (m.from_user if m else None)
    if not user:
        return

    from bots.administrator.components.db_utils import get_account_repo
    
    # Check permissions using active bot's repo for simplicity, as admins should be synced
    account_repo = get_account_repo("drive")
    if not await account_repo.is_admin(telegram_id=user.id):
        if q:
            await q.edit_message_text("You don't have permission to access Admin Controls.")
        return

    targets = ctx.user_data.setdefault("broadcast_targets", ["drive", "mega", "rclone"])
    
    total_users_by_bot = {}
    all_broadcast_users = set()
    selected_users_objs = []
    
    for bot_name in ["drive", "mega", "rclone"]:
        repo = get_account_repo(bot_name)
        whitelisted = await repo.get_by_role("whitelisted")
        valid_users = [u for u in whitelisted if not await repo.is_admin(u["telegram_id"])]
        total_users_by_bot[bot_name] = len(valid_users)
        
        if bot_name in targets:
            for u in valid_users:
                tid = u["telegram_id"]
                if tid not in all_broadcast_users:
                    all_broadcast_users.add(tid)
                    selected_users_objs.append(u)

    user_count = len(all_broadcast_users)

    text = BROADCAST_MENU_TEXT.format(
        user_count=user_count,
        drive_count=total_users_by_bot['drive'],
        mega_count=total_users_by_bot['mega'],
        rclone_count=total_users_by_bot['rclone']
    )
    
    buttons = [
        [
            InlineKeyboardButton(f"[{'✅' if 'drive' in targets else ' '}] Drive", callback_data="broadcast_target:drive"),
            InlineKeyboardButton(f"[{'✅' if 'mega' in targets else ' '}] Mega", callback_data="broadcast_target:mega"),
            InlineKeyboardButton(f"[{'✅' if 'rclone' in targets else ' '}] Rclone", callback_data="broadcast_target:rclone"),
        ],
        [InlineKeyboardButton("Refresh", callback_data="broadcast_menu")]
    ]

    ctx.user_data["awaiting_broadcast_message"] = True
    ctx.user_data["broadcast_users"] = selected_users_objs

    if q:
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    elif m:
        await m.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

async def handle_broadcast_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _render_broadcast_menu(update, ctx)

async def handle_broadcast_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.user_data is None:
        ctx.user_data = {}
    q = update.callback_query
    if not q or not q.from_user:
        return
        
    await q.answer()
    data = getattr(q, "data", None)
    user = q.from_user
    
    if data == "broadcast_menu":
        await _render_broadcast_menu(update, ctx)
        return
        
    from bots.administrator.components.db_utils import get_account_repo
    account_repo = get_account_repo("drive")

    is_super = await account_repo.is_super_admin(telegram_id=user.id)
    is_admin = await account_repo.is_admin(telegram_id=user.id)

    if not is_admin:
        await q.answer("⛔ Only admins can interact with broadcast approvals.", show_alert=True)
        return

    if data.startswith("approve_broadcast:"):
        request_id = int(data.split(":")[1])
        manager = get_broadcast_manager()
        result = await manager.execute_broadcast_approval(
            ctx, request_id, str(user.id), user.username or "Unknown", is_super
        )
        if result["status"] == "not_found":
            if q:
                await q.edit_message_text("❌ Broadcast request not found.")
            return
            
        request = result["request"]
        approvers = result["approvers"]
        
        if result["status"] == "approved":
            # Update team UI ticket if we have a group_message_id
            config = ctx.bot_data.get('config', {})
            TeamCloudverse_GROUP_CHAT_ID = config.get('GROUP_CHAT_ID')
            group_message_id = request.get('group_message_id')
            
            if TeamCloudverse_GROUP_CHAT_ID and group_message_id:
                media_type = request.get('media_type', 'text')
                
                from datetime import datetime
                initiated_at = request.get('last_updated', 'Unknown')
                if isinstance(initiated_at, str): initiated_at = initiated_at[:16]
                approved_at = request.get('approved_at') or datetime.now().strftime('%Y-%m-%d %H:%M')
                if isinstance(approved_at, str): approved_at = approved_at[:16]
                
                text = (
                    f"📡 <b>Broadcast In Progress...</b>\n\n"
                    f"👤 <b>Requester:</b> @{request.get('requester_username')}\n"
                    f"🎯 <b>Target:</b> <code>{request.get('target_count')}</code> users\n"
                    f"📋 <b>Type:</b> {media_type.capitalize()}\n"
                    f"⏰ <b>Initiated At:</b> {initiated_at}\n"
                    f"✅ <b>Approved At:</b> {approved_at}\n\n"
                    f"🚀 <i>Sending messages...</i>"
                )
                
                buttons = []
                row = []
                for a in approvers:
                    row.append(InlineKeyboardButton(f"✅ Approved by @{a['username']}", callback_data="noop"))
                for j in range(0, len(row), 2):
                    buttons.append(row[j:j+2])

                try:
                    await ctx.bot.edit_message_text(
                        chat_id=int(TeamCloudverse_GROUP_CHAT_ID),
                        message_id=int(group_message_id),
                        text=text,
                        reply_markup=InlineKeyboardMarkup(buttons),
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"Failed to edit broadcast approval message: {e}")
            elif q and not group_message_id: 
                 await q.edit_message_text("✅ <b>Broadcast Approved</b>\n\nTask started in background.", parse_mode='HTML')

            target_users = ctx.bot_data.pop(f"broadcast_targets_{request_id}", [])
            if not target_users:
                from bots.administrator.components.db_utils import get_account_repo
                account_repo = get_account_repo("drive")
                whitelisted_users = await account_repo.get_by_role(role='whitelisted')
                target_users = [u for u in whitelisted_users if not await account_repo.is_admin(telegram_id=u['telegram_id'])]
            
            from shared.core.AsyncUtils import track_task
            track_task(_run_broadcast_task(ctx, manager, request, target_users, approvers))

        else:
            # Still pending, update buttons
            if q:
                config = ctx.bot_data.get('config', {})
                TeamCloudverse_GROUP_CHAT_ID = config.get('GROUP_CHAT_ID')
                group_message_id = request.get('group_message_id')
                if TeamCloudverse_GROUP_CHAT_ID and group_message_id:
                    button_labels = []
                    for i in range(2):
                        if i < len(approvers):
                            button_labels.append(f"✅ Approved by @{approvers[i]['username']}")
                        else:
                            button_labels.append("✅ Approve")
                            
                    buttons = [
                        [InlineKeyboardButton(button_labels[0], callback_data=f"approve_broadcast:{request_id}" if "Approve" == button_labels[0][-7:] else "noop"),
                         InlineKeyboardButton(button_labels[1], callback_data=f"approve_broadcast:{request_id}" if "Approve" == button_labels[1][-7:] else "noop")],
                        [InlineKeyboardButton("🚫 Reject", callback_data=f"reject_broadcast:{request_id}")]
                    ]
                    
                    media_type = request.get('media_type', 'text')
                    
                    from datetime import datetime
                    initiated_at = request.get('last_updated', 'Unknown')
                    if isinstance(initiated_at, str): initiated_at = initiated_at[:16]
                    
                    text = (
                        f"📡 <b>Broadcast Request</b>\n\n"
                        f"👤 <b>Requester:</b> @{request.get('requester_username')}\n"
                        f"🎯 <b>Target:</b> <code>{request.get('target_count')}</code> users\n"
                        f"📋 <b>Type:</b> {media_type.capitalize()}\n"
                        f"⏰ <b>Initiated At:</b> {initiated_at}\n\n"
                        f"⏳ <b>Awaiting approval...</b>\n"
                        f"<i>Admins: {len(approvers)}/2 approvals</i>"
                    )
                    
                    try:
                         await ctx.bot.edit_message_text(
                            chat_id=int(TeamCloudverse_GROUP_CHAT_ID),
                            message_id=int(group_message_id),
                            text=text,
                            reply_markup=InlineKeyboardMarkup(buttons),
                            parse_mode='HTML'
                        )
                    except Exception as e:
                         logger.error(f"Failed to update broadcast pending ui: {e}")
    elif data.startswith("broadcast_target:"):
        target = data.split(":")[1]
        targets = ctx.user_data.setdefault("broadcast_targets", ["drive", "mega", "rclone"])
        if target in targets:
            targets.remove(target)
        else:
            targets.append(target)
        await _render_broadcast_menu(update, ctx)
        return
        
    elif data.startswith("reject_broadcast:"):
        if not is_super:
            await q.answer("⛔ Only Super Admins can reject broadcasts.", show_alert=True)
            return
        request_id = int(data.split(":")[1])
        manager = get_broadcast_manager()
        from bots.administrator.components.db_utils import get_broadcast_repo
        broadcast_repo = get_broadcast_repo("drive")
        await manager.reject_broadcast(request_id, broadcast_repo, user.username)
        text = (
            f"🚫 <b>Broadcast Rejected</b>\n\n"
            f"👑 <b>Rejected by:</b> @{user.username}\n"
            f"🕐 <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC+5:30"
        )
        await q.edit_message_text(text, parse_mode="HTML")

async def _send_broadcast_message(ctx: ContextTypes.DEFAULT_TYPE, user_id: str, request: dict, approver_text: str):
    try:
        if request['media_type'] == 'text':
            await ctx.bot.send_message(
                chat_id=int(user_id),
                text=request['message_text'] + approver_text,
                parse_mode="HTML"
            )
        elif request['media_type'] == 'photo':
            await ctx.bot.send_photo(chat_id=int(user_id), photo=request['media_file_id'], caption=request['message_text'])
        elif request['media_type'] == 'video':
            await ctx.bot.send_video(chat_id=int(user_id), video=request['media_file_id'], caption=request['message_text'])
        elif request['media_type'] == 'document':
            await ctx.bot.send_document(chat_id=int(user_id), document=request['media_file_id'], caption=request['message_text'])
        elif request['media_type'] == 'audio':
            await ctx.bot.send_audio(chat_id=int(user_id), audio=request['media_file_id'], caption=request['message_text'])
        elif request['media_type'] == 'voice':
            await ctx.bot.send_voice(chat_id=int(user_id), voice=request['media_file_id'], caption=request['message_text'])
    except Exception as e:
        raise e

async def _run_broadcast_task(ctx: ContextTypes.DEFAULT_TYPE, manager, request: dict, target_users: list, approvers: list):
    config = ctx.bot_data.get('config', {})
    group_chat_id = config.get('GROUP_CHAT_ID')
    group_message_id = request.get('group_message_id')
    
    async def send_wrapper(user_id, req, app_text):
        await _send_broadcast_message(ctx, user_id, req, app_text)
        
    async for progress in manager.execute_broadcast_task(request, target_users, approvers, send_wrapper):
        if progress.get("completed"):
            break
            
        if group_chat_id and group_message_id and progress.get("processed", 0) % 30 == 0:
            try:
                await ctx.bot.edit_message_text(
                    chat_id=int(group_chat_id),
                    message_id=int(group_message_id),
                    text=progress["progress_text"],
                    parse_mode='HTML'
                )
            except Exception:
                pass

@handle_errors
async def handle_broadcast_media_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("awaiting_broadcast_message") or not update.message:
        return

    user = update.message.from_user
    from bots.administrator.components.db_utils import get_account_repo, get_broadcast_repo
    account_repo = get_account_repo("drive")
    if not await account_repo.is_admin(telegram_id=user.id):
        return

    broadcast_users = ctx.user_data.get("broadcast_users", [])
    user_count = len(broadcast_users)

    msg = update.message
    message_text, media_type, media_file_id = "", None, None
    if msg.text:
        message_text, media_type = msg.text, "text"
    elif msg.photo:
        message_text = msg.caption or ""
        media_type, media_file_id = "photo", msg.photo[-1].file_id
    elif msg.video:
        message_text = msg.caption or ""
        media_type, media_file_id = "video", msg.video.file_id
    elif msg.document:
        message_text = msg.caption or ""
        media_type, media_file_id = "document", msg.document.file_id
    elif msg.audio:
        message_text = msg.caption or ""
        media_type, media_file_id = "audio", msg.audio.file_id
    elif msg.voice:
        message_text = msg.caption or ""
        media_type, media_file_id = "voice", msg.voice.file_id
    else:
        await msg.reply_text("❌ Unsupported media type. Broadcast cancelled.")
        ctx.user_data.pop("awaiting_broadcast_message", None)
        ctx.user_data.pop("broadcast_users", None)
        return

    manager = get_broadcast_manager()
    broadcast_repo = get_broadcast_repo("drive")

    request_id = await manager.create_broadcast_request(
        broadcast_repo,
        str(user.id),
        user.username or "Unknown",
        message_text,
        media_type,
        media_file_id,
        user_count,
    )

    if not request_id:
        await msg.reply_text("❌ Failed to create broadcast request. Please try again.")
        return
        
    ctx.bot_data[f"broadcast_targets_{request_id}"] = broadcast_users

    config = ctx.bot_data.get("config", {})
    group_chat_id = config.get("GROUP_CHAT_ID")
    broadcasts_topic_id = config.get("BROADCASTS_TOPIC_ID")

    if group_chat_id and broadcasts_topic_id:
        try:
            await msg.forward(chat_id=int(group_chat_id), message_thread_id=int(broadcasts_topic_id))
        except Exception as e:
            logger.error(f"Failed to forward broadcast content to group: {e}")

        try:
            is_super = await account_repo.is_super_admin(telegram_id=user.id)
            role_badge = "👑 Super Admin" if is_super else "🛡️ Admin"
            approval_text = (
                f"📡 <b>Broadcast Request</b>\n\n"
                f"👤 <b>Requester:</b> @{user.username} <i>({role_badge})</i>\n"
                f"🎯 <b>Target:</b> <code>{user_count}</code> users\n"
                f"📋 <b>Type:</b> {media_type.capitalize()}\n"
                f"⏰ <b>Submitted:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC+5:30\n\n"
                f"⏳ <b>Awaiting approval...</b>\n"
                f"<i>Super Admin: instant ✦ Admins: 2 approvals needed</i>"
            )
            approval_buttons = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_broadcast:{request_id}"),
                    InlineKeyboardButton("🚫 Reject", callback_data=f"reject_broadcast:{request_id}"),
                ],
            ]
            approval_msg = await ctx.bot.send_message(
                chat_id=int(group_chat_id),
                message_thread_id=int(broadcasts_topic_id),
                text=approval_text,
                reply_markup=InlineKeyboardMarkup(approval_buttons),
                parse_mode="HTML",
            )
            await broadcast_repo.update_group_message_id(request_id, approval_msg.message_id)
        except Exception as e:
            logger.error(f"Failed to post broadcast approval card to group: {e}")

    ctx.user_data.pop("awaiting_broadcast_message", None)
    ctx.user_data.pop("broadcast_users", None)

    await msg.reply_text(
        f"✅ <b>Broadcast submitted for approval.</b>\n\n"
        f"Your message has been forwarded to the Broadcasts topic.\n"
        f"Administrators have been notified to review it.",
        parse_mode="HTML",
    )
