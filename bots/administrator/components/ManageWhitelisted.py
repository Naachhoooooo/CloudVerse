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
from bots.administrator.components.db_utils import get_account_repo, get_history_repo, get_active_bot, cycle_active_bot, get_filter_button_text

TeamCloudverse_GROUP_CHAT_ID = None
Access_TOPIC_ID = None
DB_PATH = None
SUPER_ADMIN_ID = None
logger = get_logger(__name__)

@handle_errors
@admin_required
async def manage_whitelist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ctx.user_data is None:
        ctx.user_data = {}
    q = getattr(update, 'callback_query', None)
    if not q:
        return
    await q.answer()
    data = getattr(q, 'data', None)
    current_user_id = q.from_user.id
    is_current_admin = await get_account_repo(get_active_bot(ctx)).is_admin(telegram_id=current_user_id)
    is_current_super_admin = await get_account_repo(get_active_bot(ctx)).is_super_admin(telegram_id=current_user_id)
    if data and data.startswith('whitelisted_select:'):
        user_id = data.split(':', 1)[1]
        active_bot = get_active_bot(ctx)
        
        if active_bot == 'all':
            from bots.administrator.components.GlobalProfile import build_global_user_profile, _render_global_profile
            global_user_info = await build_global_user_profile(user_id)
            if global_user_info['telegram_id']:
                await _render_global_profile(q.message, ctx, global_user_info, query_to_edit=q)
            return

        return await _handle_whitelist_select(q, ctx, user_id, current_user_id, is_current_admin, is_current_super_admin)
    return await _render_whitelist(q, ctx, data)

async def _render_whitelist(q, ctx, data):
    try:
        current_page = ctx.user_data.get('whitelist_page', 0)
        if data == 'whitelist_prev_page':
            ctx.user_data['whitelist_page'] = max(0, current_page - 1)
        elif data == 'whitelist_next_page':
            ctx.user_data['whitelist_page'] = current_page + 1
        page = ctx.user_data.get('whitelist_page', 0)
        total_whitelist = await get_account_repo(get_active_bot(ctx)).count_by_role(role='whitelisted')
        paginator = Paginator([], page, 10, total_items=total_whitelist)
        whitelist = await get_account_repo(get_active_bot(ctx)).get_by_role(role='whitelisted', limit=10, offset=paginator.start_idx)
        paginator.set_items(whitelist)
        ctx.user_data['whitelist_page'] = paginator.current_page
        pagination_buttons = paginator.get_buttons('whitelist_prev_page', 'whitelist_next_page')
        text = f'✅ *Whitelisted Users* - {total_whitelist}\n\n'
        buttons = []
        active_bot = get_active_bot(ctx).capitalize()
        buttons.append([InlineKeyboardButton(get_filter_button_text(active_bot), callback_data='toggle_bot_filter:manage_whitelist')])
        if not whitelist:
            text += 'No whitelisted users found.'

        else:
            text += 'Select a user to view details:'
            for u in whitelist:
                label = format_user_list_label(u, include_emoji=True)
                if u.get('period') and u.get('access_type') == 'limited':
                    is_expired = await get_account_repo(get_active_bot(ctx)).is_expired(telegram_id=u['telegram_id'])
                    if not is_expired:
                        label += ' ⏳'
                buttons.append([InlineKeyboardButton(label, callback_data=f"whitelisted_select:{u['telegram_id']}")])
            if pagination_buttons:
                buttons.append(pagination_buttons)

        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
    except Exception as e:
        logger.error(f'Error in manage_whitelist: {e}')
        await get_server_manager().send_error_notification(error_message='Failed to load whitelist', error_type='Admin Management', severity='MEDIUM')
        await q.edit_message_text('Failed to load whitelist. Please try again later.')

async def _get_whitelisted_list(ctx: ContextTypes.DEFAULT_TYPE) -> list:
    active_bot = get_active_bot(ctx)
    from bots.administrator.components.db_utils import get_users_by_role_filtered
    whitelisted = await get_users_by_role_filtered("whitelisted", active_bot)
    return [(w['telegram_id'], f"{w.get('bot_role_emoji', '👤')} {w.get('username') or w.get('name') or 'Unknown'}") for w in whitelisted]

async def _handle_whitelist_select(q, ctx, user_id, current_user_id, is_current_admin, is_current_super_admin):
    try:
        all_whitelisted = await get_account_repo(get_active_bot(ctx)).get_by_role(role='whitelisted')
        user_data = next((user for user in all_whitelisted if str(user['telegram_id']) == str(user_id)), None)
        if user_data:
            telegram_id = user_data['telegram_id']
            username = user_data.get('username')
            name = user_data.get('name')
            handled_by = user_data.get('handled_by')
            handled_at = user_data.get('handled_at')
            action_at = user_data.get('action_at')
            access_type = user_data.get('access_type')
            period = user_data.get('period')
            display_name = name or username or f'User {telegram_id}'
            approved_date = 'Unknown'
            if handled_at:
                try:
                    approved_date = datetime.fromisoformat(handled_at).strftime('%d/%m/%Y %H:%M')
                except Exception as e:
                    logger.debug(f'Could not parse promoted date: {e}')
                    approved_date = str(handled_at)
            time_remaining = None
            has_time_limit = False
            is_expired = False
            if access_type == 'limited' and action_at and period:
                is_expired = await get_account_repo(get_active_bot(ctx)).is_expired(telegram_id=user_id)
                if not is_expired:
                    try:
                        exp_time = datetime.fromisoformat(action_at) + timedelta(hours=period)
                        time_remaining = exp_time - datetime.now()
                        has_time_limit = True
                    except (ValueError, Exception) as e:
                        logger.error(f'Could not calculate time remaining for user {user_id}: {e}')
                        time_remaining = 'Error/Unknown'
            original_limit = 'No Limit'
            if period:
                original_limit = humanize.naturaldelta(timedelta(hours=period))
            text = f'✅ *Whitelisted* > 👤 *{escape_markdown(str(display_name))}*\n\n'
            text += f'*Name:* {escape_markdown(str(display_name))}\n'
            text += f'*User ID:* {escape_markdown(str(telegram_id))}\n'
            text += f"*Username:* @{(escape_markdown(username) if username else 'N/A')}\n"
            text += f'*Alloted Time Limit:* {original_limit}\n'
            if time_remaining == 'Error/Unknown':
                text += '*Limit Remaining:* Error/Unknown\n'
            elif has_time_limit and (not is_expired):
                text += f'*Limit Remaining:* {humanize.naturaldelta(time_remaining)}\n'
            elif access_type == 'limited' and is_expired:
                text += '*Limit Remaining:* Expired\n'
            else:
                text += '*Limit Remaining:* No Limit\n'
            text += f'*Approved At:* {approved_date}\n'
            text += f'*Approved By:* {handled_by}\n'
            buttons = []
            if is_current_admin:
                if is_current_super_admin:
                    buttons.extend([[InlineKeyboardButton('ðŸŽ‰ Promote to Admin', callback_data=f'promote_whitelist_to_admin:{user_id}')]])
                if has_time_limit and (not is_expired):
                    buttons.extend([[InlineKeyboardButton('â³ Modify Limit', callback_data=f'modify_whitelist_limit:{user_id}')], [InlineKeyboardButton('â™¾ï¸ Remove Limit', callback_data=f'remove_whitelist_limit:{user_id}')]])
                elif access_type == 'limited' and is_expired:
                    pass
                else:
                    buttons.append([InlineKeyboardButton('â³ Modify Limit', callback_data=f'set_whitelist_limit:{user_id}')])
                buttons.append([InlineKeyboardButton('ðŸ”¨ Ban User', callback_data=f'ban_whitelist_user:{user_id}')])
            else:
                text += "\nâ›” *You don't have permission to modify this user.*"
            buttons.append([InlineKeyboardButton('Back to List', callback_data='manage_whitelist')])
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
        else:
            await q.edit_message_text('Whitelisted user not found.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back to List', callback_data='manage_whitelist')]]))
    except Exception as e:
        logger.error(f'Error loading whitelist user details: {e}')
        await get_server_manager().send_error_notification(error_message='Failed to load whitelist user details', error_type='Admin Management', severity='MEDIUM')
        await q.edit_message_text('Failed to load user details.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back to List', callback_data='manage_whitelist')]]))
