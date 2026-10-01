import humanize
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.managers.ServerManager import get_server_manager
from datetime import datetime, timedelta
from shared.core.ErrorHandler import handle_errors
from shared.core.CallbackDataCache import shorten_id, resolve_id
from shared.utils.text_formatter import escape_markdown
from shared.utils.time_utils import get_limit_buttons, get_duration_buttons, format_duration_from_hours
from shared.utils.pagination import Paginator, format_user_list_label
from shared.managers.AccessManager import admin_required
from shared.core.Logger import get_logger
from bots.administrator.utils.db_utils import get_account_repo, get_history_repo, get_active_bot, cycle_active_bot, get_filter_button_text

TeamCloudverse_GROUP_CHAT_ID = None
Access_TOPIC_ID = None
DB_PATH = None
SUPER_ADMIN_ID = None
logger = get_logger(__name__)

@handle_errors
@admin_required
async def handle_pending_requests(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Paginated view of pending-role users with per-user approve/reject actions."""
    if ctx.user_data is None:
        ctx.user_data = {}
    q = getattr(update, 'callback_query', None)
    if not q:
        return
    await q.answer()
    data = getattr(q, 'data', None)
    current_user_id = q.from_user.id
    if data == 'pending_approve_all':
        return await _handle_pending_bulk_approve(q, ctx, current_user_id)
    if data == 'pending_reject_all':
        return await _handle_pending_bulk_reject(q, ctx, current_user_id)
    if data and data.startswith('pending_approve_permanent:'):
        return await _handle_pending_approve_permanent(q, ctx, data.split(':')[1], current_user_id)
    if data and data.startswith('pending_approve_limited:'):
        return await _handle_pending_approve_limited(q, ctx, data.split(':')[1])
    if data and data.startswith('pending_select:'):
        user_id = data.split(':', 1)[1]
        active_bot = get_active_bot(ctx)
        
        if active_bot == 'all':
            from bots.administrator.components.GlobalProfile import build_global_user_profile, _render_global_profile
            global_user_info = await build_global_user_profile(user_id)
            if global_user_info['telegram_id']:
                await _render_global_profile(q.message, ctx, global_user_info, query_to_edit=q)
            return

        return await _handle_pending_select(q, ctx, user_id)
    if data and data.startswith('pending_reject:'):
        return await _handle_pending_reject(q, ctx, data.split(':')[1], current_user_id)
    return await _render_pending_list(q, ctx, data)

async def _render_pending_list(q, ctx, data):
    current_page = ctx.user_data.get('pending_page', 0)
    if data == 'pending_prev_page':
        ctx.user_data['pending_page'] = max(0, current_page - 1)
    elif data == 'pending_next_page':
        ctx.user_data['pending_page'] = current_page + 1
    page = ctx.user_data.get('pending_page', 0)
    total = await get_account_repo(get_active_bot(ctx)).count_by_role(role='pending')
    paginator = Paginator([], page, 10, total_items=total)
    pending_users = await get_account_repo(get_active_bot(ctx)).get_by_role(role='pending', limit=10, offset=paginator.start_idx)
    paginator.set_items(pending_users)
    ctx.user_data['pending_page'] = paginator.current_page
    text = f'📋 *Pending Access Requests* - {total}\n\nSelect a user to review:'
    buttons = []
    
    active_bot = get_active_bot(ctx).capitalize()
    buttons.append([InlineKeyboardButton(get_filter_button_text(active_bot), callback_data='toggle_bot_filter')])
    if not pending_users:
        text = '📋 *Pending Requests*\n\nNo pending requests.'

    else:
        buttons.append([InlineKeyboardButton('✅ Approve All', callback_data='pending_approve_all'), InlineKeyboardButton('🚫 Reject All', callback_data='pending_reject_all')])
        for u in pending_users:
            label = format_user_list_label(u, include_emoji=True)
            buttons.append([InlineKeyboardButton(label, callback_data=f"pending_select:{u['telegram_id']}")])
        nav = paginator.get_buttons('pending_prev_page', 'pending_next_page')
        if nav:
            buttons.append(nav)

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')

async def _handle_pending_bulk_approve(q, ctx, current_user_id):
    try:
        from shared.components.TeamCloudverse import update_request_status_cloudverse
        from shared.utils.text_formatter import escape_markdown
        username = getattr(q.from_user, 'username', 'an admin')
        pending_users = await get_account_repo(get_active_bot(ctx)).get_by_role(role='pending')
        count = 0
        for user in pending_users:
            await update_request_status_cloudverse(telegram_id=user['telegram_id'], status_text=f'✅ Approved by @{escape_markdown(username)}', ctx=ctx, account_repo=get_account_repo(get_active_bot(ctx)))
            await get_account_repo(get_active_bot(ctx)).promote(telegram_id=user['telegram_id'], role='whitelisted', handled_by=str(current_user_id), access_type='permanent', period=None, action_by=str(current_user_id))
            count += 1
        logger.info(f'[AUTH] Approved {count} pending users (Bulk Action) by {current_user_id}')
        await q.edit_message_text(f'✅ {count} users approved with permanent access.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='pending_requests')]]), parse_mode='HTML')
    except Exception as e:
        logger.error(f'[AUTH] Failed to bulk approve pending users: {e}', exc_info=True)
        await q.edit_message_text('Failed to bulk approve users. Please try again.')

async def _handle_pending_bulk_reject(q, ctx, current_user_id):
    try:
        from shared.components.TeamCloudverse import update_request_status_cloudverse
        from shared.utils.text_formatter import escape_markdown
        username = getattr(q.from_user, 'username', 'an admin')
        pending_users = await get_account_repo(get_active_bot(ctx)).get_by_role(role='pending')
        count = 0
        for user in pending_users:
            await update_request_status_cloudverse(telegram_id=user['telegram_id'], status_text=f'🚫 Rejected by @{escape_markdown(username)}', ctx=ctx, account_repo=get_account_repo(get_active_bot(ctx)))
            await get_account_repo(get_active_bot(ctx)).reject(telegram_id=user['telegram_id'], handled_by=str(current_user_id))
            count += 1
        logger.info(f'[AUTH] Rejected {count} pending users (Bulk Action) by {current_user_id}')
        await q.edit_message_text(f'🚫 {count} users rejected.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='pending_requests')]]), parse_mode='HTML')
    except Exception as e:
        logger.error(f'[AUTH] Failed to bulk reject pending users: {e}', exc_info=True)
        await q.edit_message_text('Failed to bulk reject users. Please try again.')

async def _get_pending_list(ctx: ContextTypes.DEFAULT_TYPE) -> list:
    active_bot = get_active_bot(ctx)
    from bots.administrator.utils.db_utils import get_users_by_role_filtered
    pending = await get_users_by_role_filtered("pending", active_bot)
    return [(p['telegram_id'], f"{p.get('bot_role_emoji', '👤')} {p.get('username') or p.get('name') or 'Unknown'}") for p in pending]

async def _handle_pending_select(q, ctx, user_id):
    user_data = await get_account_repo(get_active_bot(ctx)).get(telegram_id=user_id)
    if not user_data or user_data.get('role') != 'pending':
        await q.edit_message_text('User not found or no longer pending.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='pending_requests')]]))
        return
    name = user_data.get('name')
    username = user_data.get('username')
    display_name = name or username or f'User {user_id}'
    requested_at = user_data.get('requested_at', 'Unknown')
    try:
        requested_at = datetime.fromisoformat(requested_at).strftime('%d/%m/%Y %H:%M')
    except Exception:
        pass
    text = f"📋 *Pending Requests* > 👤 *{escape_markdown(str(display_name))}*\n\n*Name:* {escape_markdown(str(display_name))}\n*User ID:* `{user_id}`\n*Username:* {('@' + escape_markdown(username) if username else 'N/A')}\n*Requested At:* {requested_at}"
    buttons = [[InlineKeyboardButton('âœ… Approve (Permanent)', callback_data=f'pending_approve_permanent:{user_id}')], [InlineKeyboardButton('â° Approve (Limited)', callback_data=f'pending_approve_limited:{user_id}')], [InlineKeyboardButton('â›” Reject', callback_data=f'pending_reject:{user_id}')], [InlineKeyboardButton('Back to List', callback_data='pending_requests')]]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')

async def _handle_pending_approve_permanent(q, ctx, user_id, current_user_id):
    try:
        from shared.components.TeamCloudverse import update_request_status_cloudverse
        from shared.utils.text_formatter import escape_markdown
        username = getattr(q.from_user, 'username', 'an admin')
        await update_request_status_cloudverse(telegram_id=user_id, status_text=f'âœ… Approved by @{escape_markdown(username)}', ctx=ctx, account_repo=get_account_repo(get_active_bot(ctx)))
        await get_account_repo(get_active_bot(ctx)).promote(telegram_id=user_id, role='whitelisted', handled_by=str(current_user_id), access_type='permanent', period=None, action_by=str(current_user_id))
        logger.info(f'[AUTH] Pending user {user_id} approved (permanent) by {current_user_id}')
        await q.edit_message_text(f'âœ… User `{user_id}` approved with permanent access.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='pending_requests')]]), parse_mode='HTML')
    except Exception as e:
        logger.error(f'[AUTH] Failed to approve pending user {user_id}: {e}', exc_info=True)
        await q.edit_message_text('Failed to approve user. Please try again.')

async def _handle_pending_approve_limited(q, ctx, user_id):
    ctx.user_data['limit_user_id'] = user_id
    ctx.user_data['is_pending_limit'] = True
    buttons = get_limit_buttons()
    buttons[-1] = [InlineKeyboardButton('âŒ Cancel', callback_data=f'pending_select:{user_id}')]
    await q.edit_message_text(f'Select access duration for user `{user_id}`:', reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')

async def _handle_pending_reject(q, ctx, user_id, current_user_id):
    try:
        from shared.components.TeamCloudverse import update_request_status_cloudverse
        from shared.utils.text_formatter import escape_markdown
        username = getattr(q.from_user, 'username', 'an admin')
        await update_request_status_cloudverse(telegram_id=user_id, status_text=f'âŒ Rejected by @{escape_markdown(username)}', ctx=ctx, account_repo=get_account_repo(get_active_bot(ctx)))
        await get_account_repo(get_active_bot(ctx)).reject(telegram_id=user_id, handled_by=str(current_user_id))
        logger.info(f'[AUTH] Pending user {user_id} rejected by {current_user_id}')
        await q.edit_message_text(f'âŒ User `{user_id}` rejected.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back', callback_data='pending_requests')]]), parse_mode='HTML')
    except Exception as e:
        logger.error(f'[AUTH] Failed to reject pending user {user_id}: {e}', exc_info=True)
        await q.edit_message_text('Failed to reject user. Please try again.')
