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
async def _get_super_admin_list(ctx: ContextTypes.DEFAULT_TYPE) -> list:
    active_bot = get_active_bot(ctx)
    from bots.administrator.components.db_utils import get_users_by_role_filtered
    super_admins = await get_users_by_role_filtered("super_admin", active_bot)
    return [(sa['telegram_id'], f"{sa.get('bot_role_emoji', '👤')} {sa.get('username') or sa.get('name') or 'Unknown'}") for sa in super_admins]

@handle_errors
@admin_required
async def manage_super_admins(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if ctx.user_data is None:
        ctx.user_data = {}
    q = getattr(update, 'callback_query', None)
    if not q:
        return
    await q.answer()
    data = getattr(q, 'data', None)
    current_user_id = q.from_user.id
    is_current_super_admin = await get_account_repo(get_active_bot(ctx)).is_super_admin(telegram_id=current_user_id)
    if data and data.startswith('super_admin_select:'):
        user_id = data.split(':', 1)[1]
        active_bot = get_active_bot(ctx)
        
        if active_bot == 'all':
            from bots.administrator.components.GlobalProfile import build_global_user_profile, _render_global_profile
            global_user_info = await build_global_user_profile(user_id)
            if global_user_info['telegram_id']:
                await _render_global_profile(q.message, ctx, global_user_info, query_to_edit=q)
            return

        return await _handle_super_admin_select(q, ctx, user_id, current_user_id, is_current_super_admin)
    return await _render_super_admin_list(q, ctx, data)

async def _render_super_admin_list(q, ctx, data):
    try:
        current_page = ctx.user_data.get('super_admin_page', 0)
        if data == 'super_admin_prev_page':
            ctx.user_data['super_admin_page'] = max(0, current_page - 1)
        elif data == 'super_admin_next_page':
            ctx.user_data['super_admin_page'] = current_page + 1
        page = ctx.user_data.get('super_admin_page', 0)
        total_super_admins = await get_account_repo(get_active_bot(ctx)).count_by_role(role='super_admin')
        paginator = Paginator([], page, 10, total_items=total_super_admins)
        super_admins = await get_account_repo(get_active_bot(ctx)).get_by_role(role='super_admin', limit=10, offset=paginator.start_idx)
        paginator.set_items(super_admins)
        ctx.user_data['super_admin_page'] = paginator.current_page
        pagination_buttons = paginator.get_buttons('super_admin_prev_page', 'super_admin_next_page')
        text = f'ðŸ‘‘ *Manage Super Admins* - {total_super_admins}\n\n'
        buttons = []
        active_bot = get_active_bot(ctx).capitalize()
        buttons.append([InlineKeyboardButton(get_filter_button_text(active_bot), callback_data='toggle_bot_filter')])
        if not super_admins:
            text += 'No super admins found.'

        else:
            text += 'Select a super admin to view details:'
            for u in super_admins:
                label = format_user_list_label(u, include_emoji=True)
                buttons.append([InlineKeyboardButton(label, callback_data=f"super_admin_select:{u['telegram_id']}")])
            if pagination_buttons:
                buttons.append(pagination_buttons)

        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
    except Exception as e:
        logger.error(f'Error in manage_super_admins: {e}')
        await get_server_manager().send_error_notification(error_message='Failed to load super admin list', error_type='Admin Management', severity='MEDIUM')
        await q.edit_message_text('Failed to load super admin list. Please try again later.')

async def _handle_super_admin_select(q, ctx, admin_id, current_user_id, is_current_super_admin):
    try:
        is_protected = await get_account_repo(get_active_bot(ctx)).is_protected(telegram_id=admin_id)
        admin_data = await get_account_repo(get_active_bot(ctx)).get(telegram_id=admin_id)
        if admin_data and admin_data['role'] == 'super_admin':
            telegram_id = admin_data['telegram_id']
            username = admin_data.get('username')
            name = admin_data.get('name')
            handled_by = admin_data.get('handled_by')
            handled_at = admin_data.get('handled_at')
            display_name = name or username or f'User {telegram_id}'
            promoted_date = 'Unknown'
            if handled_at:
                try:
                    promoted_date = datetime.fromisoformat(handled_at).strftime('%d/%m/%Y %H:%M')
                except Exception as e:
                    logger.debug(f"Could not parse promotion date '{handled_at}': {e}")
                    promoted_date = str(handled_at)
            text = f'👑 *Super Admins* > 👤 *{escape_markdown(str(display_name))}*\n\n'
            text += f'*Name:* {escape_markdown(str(display_name))}\n'
            text += f'*User ID:* {escape_markdown(str(telegram_id))}\n'
            text += f"*Username:* @{(escape_markdown(username) if username else 'N/A')}\n"
            text += f'*Promoted At:* {promoted_date}\n'
            text += f"*Promoted By:* {handled_by or 'Cloudverse'}\n"
            if is_protected:
                text += '\nðŸ›¡ï¸ *Protected Account* - Cannot be modified'
            if str(current_user_id) == str(telegram_id):
                text += '\nðŸ‘¤ *(This is you)*'
            buttons = []
            if str(current_user_id) == str(telegram_id):
                text += '\nðŸ›¡ï¸ *Self-modification is disabled to prevent accidental account lockouts.*'
            elif is_current_super_admin and (not is_protected):
                buttons.extend([[InlineKeyboardButton('âœ‚ Demote to Admin', callback_data=f'demote_super_admin_to_admin:{admin_id}')], [InlineKeyboardButton('âœ‚ Demote to Whitelisted', callback_data=f'demote_super_admin_to_whitelist:{admin_id}')]])
            elif is_protected:
                text += '\nðŸ›¡ï¸ *Protected account cannot be modified.*'
            else:
                text += "\nâ›” *You don't have permission to modify this super admin.*"
            buttons.append([InlineKeyboardButton('Back to List', callback_data='manage_super_admins')])
            await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')
        else:
            await q.edit_message_text('Super admin not found.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back to List', callback_data='manage_super_admins')]]))
    except Exception as e:
        logger.error(f'Error loading super admin details: {e}')
        await get_server_manager().send_error_notification(error_message='Failed to load super admin details', error_type='Admin Management', severity='MEDIUM')
        await q.edit_message_text('Failed to load super admin details.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Back to List', callback_data='manage_super_admins')]]))
