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
async def _get_blacklisted_list(ctx: ContextTypes.DEFAULT_TYPE) -> list:
    active_bot = get_active_bot(ctx)
    from bots.administrator.components.db_utils import get_users_by_role_filtered
    blacklisted = await get_users_by_role_filtered("blacklisted", active_bot)
    return [(b['telegram_id'], f"{b.get('bot_role_emoji', '👤')} {b.get('username') or b.get('name') or 'Unknown'}") for b in blacklisted]

@handle_errors
@admin_required
async def manage_blacklist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ctx.user_data is None:
        ctx.user_data = {}
    q = getattr(update, 'callback_query', None)
    if not q:
        return
    await q.answer()
    data = getattr(q, 'data', None)
    current_user_id = q.from_user.id
    is_current_admin = await get_account_repo(get_active_bot(ctx)).is_admin(telegram_id=current_user_id)
    if data and data.startswith('blacklist_select:'):
        user_id = data.split(':', 1)[1]
        active_bot = get_active_bot(ctx)
        
        if active_bot == 'all':
            from bots.administrator.components.GlobalProfile import build_global_user_profile, _render_global_profile
            global_user_info = await build_global_user_profile(user_id)
            if global_user_info['telegram_id']:
                await _render_global_profile(q.message, ctx, global_user_info, query_to_edit=q)
            return

        return await _handle_blacklist_select(q, ctx, user_id, current_user_id, is_current_admin)
    return await _render_blacklist(q, ctx, data)

async def _render_blacklist(q, ctx, data):
    try:
        current_page = ctx.user_data.get('blacklist_page', 0)
        if data == 'blacklist_prev_page':
            ctx.user_data['blacklist_page'] = max(0, current_page - 1)
        elif data == 'blacklist_next_page':
            ctx.user_data['blacklist_page'] = current_page + 1
        page = ctx.user_data.get('blacklist_page', 0)
        total_blacklist = await get_account_repo(get_active_bot(ctx)).count_by_role(role='blacklisted')
        paginator = Paginator([], page, 10, total_items=total_blacklist)
        blacklist = await get_account_repo(get_active_bot(ctx)).get_by_role(role='blacklisted', limit=10, offset=paginator.start_idx)
        paginator.set_items(blacklist)
        ctx.user_data['blacklist_page'] = paginator.current_page
        pagination_buttons = paginator.get_buttons('blacklist_prev_page', 'blacklist_next_page')
        text = f'ðŸš« *Blacklisted Users* - {total_blacklist}\n\n'
        buttons = []
        active_bot = get_active_bot(ctx).capitalize()
        buttons.append([InlineKeyboardButton(get_filter_button_text(active_bot), callback_data='toggle_bot_filter:manage_blacklist')])
        if not blacklist:
            text += 'No blacklisted users found.'

        else:
            text += 'Select a user to edit restrictions:'
            for u in blacklist:
                label = format_user_list_label(u, include_emoji=True)
                restriction_type = u.get('restriction_type')
                if restriction_type != 'permanent':
                    is_expired = await get_account_repo(get_active_bot(ctx)).is_expired(telegram_id=u['telegram_id'])
                    if not is_expired:
                        label += ' ⏳'
                buttons.append([InlineKeyboardButton(label, callback_data=f"blacklist_select:{u['telegram_id']}")])
            if pagination_buttons:
                buttons.append(pagination_buttons)

        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
    except Exception as e:
        logger.error(f'Error in manage_blacklist: {e}')
        await get_server_manager().send_error_notification(error_message='Failed to load blacklist', error_type='Admin Management', severity='MEDIUM')
        await q.edit_message_text('Failed to load blacklist. Please try again later.')

async def _handle_blacklist_select(q, ctx, user_id, current_user_id, is_current_admin):
    try:
        all_blacklisted = await get_account_repo(get_active_bot(ctx)).get_by_role(role='blacklisted')
        user_data = next((user for user in all_blacklisted if str(user['telegram_id']) == str(user_id)), None)
        if user_data:
            telegram_id = user_data['telegram_id']
            username = user_data.get('username')
            name = user_data.get('name')
            action_by = user_data.get('action_by')
            action_at = user_data.get('action_at')
            restriction_type = user_data.get('restriction_type')
            period = user_data.get('period')
            display_name = name or username or f'User {telegram_id}'
            restricted_date = 'Unknown'
            if action_at:
                try:
                    restricted_date = datetime.fromisoformat(action_at).strftime('%d/%m/%Y %H:%M')
                except Exception as e:
                    logger.debug(f"Could not parse restriction date '{action_at}': {e}")
                    restricted_date = str(action_at)
            restriction_type_disp = restriction_type.capitalize() if restriction_type else 'Permanent'
            if restriction_type_disp == 'Permanent':
                restriction_period_text = 'Permanent'
                restriction_remaining_text = 'Permanent'
            else:
                restriction_period_text = humanize.naturaldelta(timedelta(hours=period)) if period else 'Unknown'
                is_expired = await get_account_repo(get_active_bot(ctx)).is_expired(telegram_id=user_id)
                if is_expired:
                    restriction_remaining_text = 'Expired'
                else:
                    try:
                        exp_time = datetime.fromisoformat(action_at) + timedelta(hours=period)
                        restriction_remaining_text = humanize.naturaldelta(exp_time - datetime.now())
                    except (ValueError, Exception) as e:
                        logger.error(f'Could not calculate restriction remaining for user {user_id}: {e}')
                        restriction_remaining_text = 'Error/Unknown'
            text = f'🚫 *Blacklisted* > 👤 *{escape_markdown(str(display_name))}*\n\n'
            text += f'*Name:* {escape_markdown(str(display_name))}\n'
            text += f'*User ID:* {escape_markdown(str(telegram_id))}\n'
            text += f"*Username:* @{(escape_markdown(username) if username else 'N/A')}\n"
            text += f'*Restriction Type:* {restriction_type_disp}\n'
            if restriction_type_disp != 'Permanent':
                text += f'*Restriction Period:* {restriction_period_text}\n'
                text += f'*Restriction Remaining:* {restriction_remaining_text}\n'
            text += f'*Restricted At:* {restricted_date}\n'
            text += f'*Restricted By:* {action_by}\n'
            buttons = []
            if is_current_admin:
                buttons.extend([[InlineKeyboardButton('ðŸŽ‰ Promote to Whitelisted', callback_data=f'promote_blacklist_to_whitelist:{user_id}')], [InlineKeyboardButton('âš™ï¸ Modify Restriction', callback_data=f'modify_blacklist_restriction:{user_id}')]])
            else:
                text += "\nâ›” *You don't have permission to modify this user.*"
            buttons.append([InlineKeyboardButton('Back to List', callback_data='manage_blacklist')])
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
        else:
            await q.edit_message_text('Blacklisted user not found.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back to List', callback_data='manage_blacklist')]]))
    except Exception as e:
        logger.error(f'Error loading blacklist user details: {e}')
        await get_server_manager().send_error_notification(error_message='Failed to load blacklist user details', error_type='Admin Management', severity='MEDIUM')
        await q.edit_message_text('Failed to load user details.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back to List', callback_data='manage_blacklist')]]))

async def _handle_blacklist_modify(q, ctx, user_id):
    try:
        all_blacklisted = await get_account_repo(get_active_bot(ctx)).get_by_role(role='blacklisted')
        user_data = next((user for user in all_blacklisted if str(user['telegram_id']) == str(user_id)), None)
        if user_data:
            period = user_data.get('period')
            restriction_type = user_data.get('restriction_type')
            if restriction_type == 'permanent':
                current_period = 'Permanent'
            else:
                current_period = format_duration_from_hours(period)
            text = 'âš ï¸ *Modify User Restriction*\n\n'
            text += f'*Current restriction period:* {current_period}\n\n'
            text += 'Select new restriction period:'
            buttons = get_duration_buttons()
            buttons[-1] = [InlineKeyboardButton('âŒ Cancel', callback_data=f'blacklist_select:{user_id}')]
            ctx.user_data['restriction_user_id'] = user_id
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
        else:
            await q.edit_message_text('User not found.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back to List', callback_data='manage_blacklist')]]))
    except Exception as e:
        logger.error(f'Error showing restriction modification menu: {e}')
        await get_server_manager().send_error_notification(error_message='Failed to show restriction modification menu', error_type='Admin Management', severity='MEDIUM')
        await q.edit_message_text('Failed to load restriction options.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back to List', callback_data='manage_blacklist')]]))
