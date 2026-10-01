from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import aiofiles
from datetime import datetime
from shared.managers.ServerManager import get_server_manager
from shared.core.ErrorHandler import handle_errors
from shared.utils.text_formatter import escape_markdown
from shared.core.Logger import get_logger

# These are injected at startup by shared.core.ComponentInitializer
TeamCloudverse_GROUP_CHAT_ID = None
Access_TOPIC_ID = None
Flags_TOPIC_ID = None
Broadcasts_TOPIC_ID = None
Management_TOPIC_ID = None
Alerts_TOPIC_ID = None
Bugs_TOPIC_ID = None
BACKUP_TOPIC_ID = None

logger = get_logger(__name__)

# In-memory store for pending group message IDs (supports both telegram_id and request_id keys)
_pending_group_messages = {}

async def send_welcome_notification(user_id: int, ctx: ContextTypes.DEFAULT_TYPE, custom_message: str = None, limit_hours: int = None):
    provider = ctx.bot_data.get('provider_name', 'drive').capitalize()
    platform = 'Google Drive' if provider == 'Drive' else 'Mega.nz' if provider == 'Mega' else 'Rclone' if provider == 'Rclone' else 'Cloud Storage'
    welcome_msg = (
        f"🎉 Welcome to <b>CloudVerse {provider} Bot</b>\n\n"
        f"your seamless solution for managing <b>{platform}</b> directly from <b>Telegram</b> with various other functionalities\n\n"
    )
    if limit_hours:
        welcome_msg += f"⚠️ <b>Note:</b> Your access is limited to <b>{limit_hours} hours</b>.\n\n"
    
    welcome_msg += (
        f"Review the <a href=\"https://t.me/{ctx.bot.username}?start=policies\">policies</a> before getting started\n\n"
        f"Please reach out to <b>us</b> anytime for assistance"
    )
    
    if custom_message:
        import html
        safe_text = html.escape(custom_message.strip())
        welcome_msg += f"\n\n<b>Team CloudVerse :</b> <i>{safe_text}</i>"
        
    try:
        await ctx.bot.send_message(chat_id=user_id, text=welcome_msg, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Failed to send welcome message to {user_id}: {e}")

def update_pending_user_group_message(key, message_id):
    # Store or update the pending message id for a user/request
    if key is None:
        return
    _pending_group_messages[str(key)] = int(message_id)


def get_pending_user_group_message(key):
    # Retrieve the stored message id for a user/request
    if key is None:
        return None
    return _pending_group_messages.get(str(key))


def clear_pending_user_group_message(key):
    # Remove the stored mapping once processed
    if key is None:
        return
    _pending_group_messages.pop(str(key), None)


def get_group_ids(topic_type):
   if TeamCloudverse_GROUP_CHAT_ID is None:
       raise ValueError("Group chat ID not configured.")
   
   topic_map = {
       "access_request": Access_TOPIC_ID,
       "flags": Flags_TOPIC_ID,
       "broadcasts": Broadcasts_TOPIC_ID,
       "management": Management_TOPIC_ID,
       "maintenance": Management_TOPIC_ID,
       "alerts": Alerts_TOPIC_ID,
       "bugs": Bugs_TOPIC_ID,
       "backup": BACKUP_TOPIC_ID
   }
   
   topic_id = topic_map.get(topic_type)
   if topic_id is None:
       raise ValueError(f"Topic ID for {topic_type} not configured.")
   
   return int(TeamCloudverse_GROUP_CHAT_ID), int(topic_id)


@handle_errors
async def handle_access_request(ctx, action, data):
   chat_id, topic_id = get_group_ids("access_request")
   telegram_id = data.get('telegram_id')
   username = data.get('username')
   full_name = data.get('full_name') or data.get('name', 'User')
   admin_username = data.get('admin_username')
   hours = data.get('hours')
   status = data.get('status')
   timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
   provider = ctx.bot_data.get('provider_name', 'drive').capitalize()
   bot_title = f"CloudVerse {provider} Bot"
   if action == 'request':
       user_info = (
           f"Bot : {bot_title}\n"
           f"Name: {full_name}\n"
           f"Username: @{username or 'N/A'}\n"
           f"ID: {telegram_id}"
       )
       buttons = [
           [InlineKeyboardButton("⏳ Limited Access", callback_data=f"access_limit:{telegram_id}"),
            InlineKeyboardButton("✅ Approve", callback_data=f"access_approve:{telegram_id}"),
            InlineKeyboardButton("⛔ Reject", callback_data=f"access_reject:{telegram_id}")]
       ]
       try:
            msg = await ctx.bot.send_message(
                chat_id=chat_id,
                text=user_info,
                message_thread_id=topic_id,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            update_pending_user_group_message(telegram_id, msg.message_id)
            return msg.message_id
       except Exception as e:
           from shared.core.Logger import log_exception
           log_exception(f"Error in handle_access_request (request): {e}")
           await get_server_manager().send_error_notification(
               error_message=f"Failed to send access request to group: {e}",
               error_type="Access Control",
               severity="HIGH"
           )
   elif action in ('approve', 'reject', 'limit', 'update'):
       row = get_pending_user_group_message(telegram_id)
       if row and TeamCloudverse_GROUP_CHAT_ID is not None and row is not None:
           message_id = row
           try:
               details = (
                   f"Name: {full_name}\n"
                   f"Username: @{username or 'N/A'}\n"
                   f"ID: {telegram_id}\n\n"
                   f"Bot : {bot_title}\n"
               )
               if action == 'approve':
                   details += "Request Status : Approved\n"
                   details += f"Timestamp: {timestamp}"
                   status_button = [[InlineKeyboardButton(f"✅ Approved by @{admin_username}", callback_data="noop")]]
               elif action == 'reject':
                   details += "Request Status : Rejected\n"
                   details += f"Timestamp: {timestamp}"
                   status_button = [[InlineKeyboardButton(f"❌ Rejected by @{admin_username}", callback_data="noop")]]
               elif action == 'limit':
                   details += f"Request Status : Limited Access ({hours} hours)\n"
                   details += f"Timestamp: {timestamp}"
                   status_button = [[InlineKeyboardButton(f"⏳ Limited by @{admin_username}", callback_data="noop")]]
               else:
                   details += f"Request Status : {status or 'Updated'}\n"
                   details += f"Timestamp: {timestamp}"
                   status_button = [[InlineKeyboardButton(f"📝 Updated by @{admin_username}", callback_data="noop")]]
               await ctx.bot.edit_message_text(
                   chat_id=chat_id,
                   message_id=message_id,
                   text=details,
                   reply_markup=InlineKeyboardMarkup(status_button)
               )
           except Exception as e:
               from shared.core.Logger import log_exception
               log_exception(f"Error updating group message: {e}")
               await get_server_manager().send_error_notification(
                   error_message=f"Failed to update access request status in group: {e}",
                   error_type="Access Control",
                   severity="MEDIUM"
               )
           clear_pending_user_group_message(telegram_id)





@handle_errors
async def handle_broadcast_request(ctx, action, data):
   chat_id, topic_id = get_group_ids("broadcasts")
   request_id = data.get('request_id')
   message = data.get('message')
   media_type = data.get('media_type')
   media_file_id = data.get('media_file_id')
   admin_username = data.get('admin_username')
   status = data.get('status')
   timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
   if action == 'request':
       import html
       group_text = f"📢 <b>New Broadcast Request</b>\n\n<b>Message:</b>\n{html.escape(message)}"
       buttons = [
           [InlineKeyboardButton("✅ Approve", callback_data=f"approve_broadcast:{request_id}"),
            InlineKeyboardButton("🚫 Reject (Super Admin Only)", callback_data=f"reject_broadcast:{request_id}")]
       ]
       msg = None
       try:
           if media_type == "text" or not media_type:
               msg = await ctx.bot.send_message(
                   chat_id=chat_id,
                   text=group_text,
                   message_thread_id=topic_id,
                   reply_markup=InlineKeyboardMarkup(buttons),
                   parse_mode='HTML'
               )
           elif media_type == "photo":
               msg = await ctx.bot.send_photo(
                   chat_id=chat_id,
                   photo=media_file_id,
                   caption=group_text,
                   message_thread_id=topic_id,
                   reply_markup=InlineKeyboardMarkup(buttons)
               )
           elif media_type == "video":
               msg = await ctx.bot.send_video(
                   chat_id=chat_id,
                   video=media_file_id,
                   caption=group_text,
                   message_thread_id=topic_id,
                   reply_markup=InlineKeyboardMarkup(buttons)
               )
           elif media_type == "document":
               msg = await ctx.bot.send_document(
                   chat_id=chat_id,
                   document=media_file_id,
                   caption=group_text,
                   message_thread_id=topic_id,
                   reply_markup=InlineKeyboardMarkup(buttons)
               )
           elif media_type == "audio":
               msg = await ctx.bot.send_audio(
                   chat_id=chat_id,
                   audio=media_file_id,
                   caption=group_text,
                   message_thread_id=topic_id,
                   reply_markup=InlineKeyboardMarkup(buttons)
               )
           elif media_type == "voice":
               msg = await ctx.bot.send_voice(
                   chat_id=chat_id,
                   voice=media_file_id,
                   caption=group_text,
                   message_thread_id=topic_id,
                   reply_markup=InlineKeyboardMarkup(buttons)
               )
           if msg and request_id:
               update_pending_user_group_message(request_id, msg.message_id)
       except Exception as e:
           from shared.core.Logger import log_exception
           log_exception(f"Error sending broadcast request to group: {e}")
           await get_server_manager().send_error_notification(
               error_message=f"Failed to send broadcast request to group: {e}",
               error_type="Broadcast System",
               severity="HIGH"
           )
   elif action in ('approve', 'reject', 'update'):
       row = get_pending_user_group_message(request_id)
       if row and TeamCloudverse_GROUP_CHAT_ID is not None and row is not None:
           message_id = row
           try:
               details = (
                   f"Broadcast Request\n\n"
                   f"Message: {message}\n"
                   f"Status: {status or action.capitalize()}\n"
                   f"Timestamp: {timestamp}"
               )
               if action == 'approve':
                   status_button = [[InlineKeyboardButton(f"✅ Approved by @{admin_username}", callback_data="noop")]]
               elif action == 'reject':
                   status_button = [[InlineKeyboardButton(f"❌ Rejected by @{admin_username}", callback_data="noop")]]
               else:
                   status_button = [[InlineKeyboardButton(f"📝 Updated by @{admin_username}", callback_data="noop")]]
               await ctx.bot.edit_message_text(
                   chat_id=chat_id,
                   message_id=message_id,
                   text=details,
                   reply_markup=InlineKeyboardMarkup(status_button)
               )
           except Exception as e:
               from shared.core.Logger import log_exception
               log_exception(f"Error updating group message: {e}")
               await get_server_manager().send_error_notification(
                   error_message=f"Failed to update broadcast request status in group: {e}",
                   error_type="Broadcast System",
                   severity="MEDIUM"
               )
           clear_pending_user_group_message(request_id)

@handle_errors
async def post_access_request(ctx, telegram_id, username, full_name):
   await handle_access_request(ctx, 'request', {
       'telegram_id': telegram_id,
       'username': username,
       'full_name': full_name
   })


@handle_errors
async def post_broadcast(ctx, text, media_type=None, media_file_id=None, request_id=None, user_count=None):
   await handle_broadcast_request(ctx, 'request', {
       'request_id': request_id,
       'message': text,
       'media_type': media_type,
       'media_file_id': media_file_id,
       'user_count': user_count
   })


@handle_errors
async def update_group_ban_message_status(telegram_id, name, username, ban_status, ban_type, admin_username, ctx, status_button=None):
   await handle_access_request(ctx, 'update', {
       'telegram_id': telegram_id,
       'full_name': name,
       'username': username,
       'status': f"Banned ({ban_type})",
       'admin_username': admin_username
   })


@handle_errors
async def update_request_status_cloudverse(telegram_id, status_text, ctx, status_button=None, account_repo=None):
    if not account_repo:
        account_repo = ctx.bot_data.get('account_repo')
        if not account_repo:
            return
            
    user_data = await account_repo.get(telegram_id=telegram_id)
    if user_data and TeamCloudverse_GROUP_CHAT_ID is not None and user_data.get('request_message_id') is not None:
        message_id = user_data['request_message_id']
        try:
            chat_id = int(str(TeamCloudverse_GROUP_CHAT_ID))
            msg_id = int(str(message_id))
            await ctx.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=status_text,
                reply_markup=InlineKeyboardMarkup(status_button) if status_button else None
            )
        except Exception as e:
            logger.error(f"Failed to update access request message: {e}")
            await get_server_manager().send_error_notification(
                error_message=f"Failed to update request message status: {e}",
                error_type="Access Control",
                severity="MEDIUM"
            )
        await account_repo.update(telegram_id=telegram_id, request_message_id=None)


@handle_errors
async def post_request_to_cloudverse(ctx, telegram_id, username, name):
    all_accounts = await ctx.bot_data['account_repo'].get_all()
    user_details = next((user for user in all_accounts if str(user['telegram_id']) == str(telegram_id)), None)
    expired_note = ""
    last_access_note = ""
    if user_details:
        if user_details.get("role") == "rejected":
            expired_note = "\n\n⚠️ This user is a rejected past cloudverse user."
        period = user_details.get("period")
        action_at = user_details.get("action_at")
        if period and action_at:
            try:
                last_access_note = f"\nLast allotted access: {period} hours"
            except Exception as e:
                logger.debug(f"Could not parse period for last access note: {e}")
    message = f"Access request from user:{expired_note}{last_access_note}"
    return await handle_access_request(ctx, 'request', {
        'telegram_id': telegram_id,
        'username': username,
        'name': name,
        'message': message
    })


@handle_errors
async def handle_new_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from shared.managers.AccessManager import access_required
    
    @access_required
    async def _inner_handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if ctx.user_data is None:
            ctx.user_data = {}
        q = getattr(update, 'callback_query', None)
        if not q:
            return
        try:
            await q.answer()
            data = q.data
            if data.startswith("access_limit:"):
                user_id = int(data.split(":")[1])
                ctx.user_data["pending_limit_user"] = user_id
                back_button = InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip", callback_data="access_skip_limit")]])
                await q.edit_message_text("Enter the number of hours for limited access:", reply_markup=back_button)
                ctx.user_data["awaiting_limit_hours"] = True
            elif data.startswith("access_approve:"):
                user_id = int(data.split(":")[1])
                ctx.user_data["pending_approve_user"] = user_id
                await q.edit_message_text(
                    "Send an optional welcome message to the user, or click Skip.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip", callback_data="access_skip_approve")]])
                )
                ctx.user_data["awaiting_approve_message"] = True
            elif data.startswith("access_reject:"):
                user_id = int(data.split(":")[1])
                ctx.user_data["pending_reject_user"] = user_id
                await q.edit_message_text(
                    "Send an optional rejection message to the user, or click Skip.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip", callback_data="access_skip_reject")]])
                )
                ctx.user_data["awaiting_reject_message"] = True
            elif data == "access_skip_approve":
                user_id = ctx.user_data.get("pending_approve_user")
                if user_id is not None:
                    await ctx.bot_data['account_repo'].promote(telegram_id=user_id, role='whitelisted', handled_by=str(q.from_user.id), access_type='permanent', period=None, action_by=str(q.from_user.id))
                    await q.edit_message_text("Access to the bot has been approved.")
                    await send_welcome_notification(user_id, ctx)
                    await update_request_status_cloudverse(user_id, f"✅ Approved by @{escape_markdown(q.from_user.username) if q.from_user.username else 'an admin'}", ctx)
                ctx.user_data.pop("pending_approve_user", None)
                ctx.user_data.pop("awaiting_approve_message", None)
            elif data == "access_skip_reject":
                user_id = ctx.user_data.get("pending_reject_user")
                if user_id is not None:
                    await ctx.bot_data['account_repo'].reject(telegram_id=user_id, handled_by=str(q.from_user.id))
                    await q.edit_message_text("Access to the bot has been rejected.")
                    await ctx.bot.send_message(chat_id=user_id, text="Your access request has been declined by <b>Team CloudVerse</b>.", parse_mode='HTML')
                    await update_request_status_cloudverse(user_id, f"❌ Rejected by @{escape_markdown(q.from_user.username) if q.from_user.username else 'an admin'}", ctx)
                ctx.user_data.pop("pending_reject_user", None)
                ctx.user_data.pop("awaiting_reject_message", None)
            elif data == "cancel_limit_setting":
                ctx.user_data.pop("pending_limit_user", None)
                ctx.user_data.pop("awaiting_limit_hours", None)
                await q.edit_message_text("Cancelled. Returning to access options...")
            elif data == "access_skip_limit":
                ctx.user_data.pop("pending_limit_user", None)
                ctx.user_data.pop("awaiting_limit_hours", None)
                await q.edit_message_text("Skipped setting limit.")
        except Exception as e:
            logger.error(f"Error handling access callback: {e}")
            await q.edit_message_text("An error occurred while processing the access request.")

    return await _inner_handle(update, ctx)

def register_handlers(app):
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(handle_new_request, pattern=r"^access_(limit|approve|reject|skip_approve|skip_reject):.*$"))
    app.add_handler(CallbackQueryHandler(handle_new_request, pattern=r"^(access_skip_approve|access_skip_reject|access_skip_limit|cancel_limit_setting)$"))
