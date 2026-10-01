"""
ActionDispatcher ΓÇö callback and message routing for access control actions.

Replaces the monolithic handle_access_control_actions god-function with
a clean dictionary-based dispatch pattern. Each route delegates business logic 
to the appropriate manager and calls UI render functions via lazy imports.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from datetime import datetime, timedelta
import humanize

from shared.core.Logger import get_logger

logger = get_logger(__name__)


def _get_deps():
    """Lazy import to avoid circular dependency chain."""
    from shared.core.ErrorHandler import handle_errors
    from shared.utils.text_formatter import escape_markdown
    from shared.managers.ServerManager import get_server_manager
    from shared.managers.AccessManager import RoleManager, WhitelistManager, BlacklistManager, RequestManager
    return handle_errors, escape_markdown, get_server_manager, RoleManager, WhitelistManager, BlacklistManager, RequestManager





async def dispatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Main entry point.

    Routes message-based inputs and callback queries to the appropriate
    manager function, then re-renders the UI.
    """
    _, _, get_server_manager, _, _, _, _ = _get_deps()

    if ctx.user_data is None:
        ctx.user_data = {}

    if update.message:
        await _dispatch_message(update, ctx)
        return

    q = getattr(update, 'callback_query', None)
    if not q:
        return

    try:
        await q.answer()
        await _dispatch_callback(q, update, ctx)
    except Exception as e:
        logger.error(f"Failed to process access control action: {e}")
        await get_server_manager().send_error_notification(
            error_message=f"Failed to process access control action. Details: {str(e)}",
            error_type="Admin Action", severity="MEDIUM"
        )
        await q.edit_message_text("Failed to process request action. Please try again later.")


# ΓöÇΓöÇ Message-Based Input Handlers ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

async def _dispatch_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle text inputs that are awaiting user responses."""
    _, escape_markdown, _, _, WhitelistManager, BlacklistManager, RequestManager = _get_deps()

    # Approve with message
    if ctx.user_data.get("awaiting_approve_message"):
        user_id = ctx.user_data.get("pending_approve_user")
        if user_id is not None:
            account_repo = ctx.bot_data['account_repo']
            await RequestManager.approve_permanent(account_repo, user_id, str(update.message.from_user.id), ctx.bot_data.get('history_repo'))
            await update.message.reply_text("Access to the bot has been approved.")
            
            msg_text = update.message.text or ''
            from shared.components.TeamCloudverse import send_welcome_notification, update_request_status_cloudverse
            await send_welcome_notification(user_id, ctx, custom_message=msg_text if msg_text else None)

            username = getattr(update.message.from_user, 'username', None) or 'Team CloudVerse'
            from shared.components.TeamCloudverse import update_request_status_cloudverse
            await update_request_status_cloudverse(user_id, f"Γ£à Approved by @{escape_markdown(username)}", ctx)

        ctx.user_data.pop("pending_approve_user", None)
        ctx.user_data.pop("awaiting_approve_message", None)
        return

    # Reject with message
    if ctx.user_data.get("awaiting_reject_message"):
        user_id = ctx.user_data.get("pending_reject_user")
        if user_id is not None:
            account_repo = ctx.bot_data['account_repo']
            await RequestManager.reject_request(account_repo, user_id, str(update.message.from_user.id), ctx.bot_data.get('history_repo'))
            await update.message.reply_text("Access to the bot has been rejected.")

            msg_text = update.message.text or ''
            reject_msg = "Your access request has been declined by <b>Team CloudVerse</b>."
            if msg_text:
                reject_msg += f"\n\nTeam CloudVerse Message : {msg_text}."
            await ctx.bot.send_message(chat_id=user_id, text=reject_msg, parse_mode='HTML')

            username = getattr(update.message.from_user, 'username', None) or 'Team CloudVerse'
            from shared.components.TeamCloudverse import update_request_status_cloudverse
            await update_request_status_cloudverse(user_id, f"Γ¥î Rejected by @{escape_markdown(username)}", ctx)

        ctx.user_data.pop("pending_reject_user", None)
        ctx.user_data.pop("awaiting_reject_message", None)
        return

    # Set limited hours
    if ctx.user_data.get("awaiting_limit_hours") or (
        ctx.user_data.get("next_action") and str(ctx.user_data.get("next_action")).startswith("set_limit_hours:")
    ):
        user_id = ctx.user_data.get("pending_limit_user")
        if not user_id:
            return
        ctx.user_data.pop("pending_limit_user", None)
        ctx.user_data.pop("awaiting_limit_hours", None)
        ctx.user_data.pop("next_action", None)

        if update.message and update.message.text:
            try:
                hours = int(update.message.text.strip())
                await WhitelistManager.set_time_limit(ctx.bot_data['account_repo'], user_id, hours, str(update.message.from_user.id))
                back_button = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_to_whitelist")]])

                is_whitelisted = await ctx.bot_data['account_repo'].is_whitelisted(telegram_id=user_id)
                if not is_whitelisted:
                    from shared.components.TeamCloudverse import send_welcome_notification
                    await update.message.reply_text("Access to the bot has been approved.", reply_markup=back_button)
                    await send_welcome_notification(user_id, ctx, limit_hours=hours)
                else:
                    msg = f"Your access to the bot has been modified by <b>Team CloudVerse</b> to <b>{hours} hour(s)</b>"
                    await update.message.reply_text("Access limit has been modified.", reply_markup=back_button)
                    await ctx.bot.send_message(chat_id=user_id, text=msg, parse_mode='HTML')

                username = getattr(update.message.from_user, 'username', None) or 'Team CloudVerse'
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                details = (
                    f"Name: {update.message.from_user.name or ''}\n"
                    f"Username: @{username or 'N/A'}\nID: {user_id}\n\n ---\n\n"
                    f"Request Status : Limited Access ({hours} hours)\nTimestamp: {timestamp}"
                )
                status_button = [[InlineKeyboardButton(f"ΓÅ░ Limited by @{escape_markdown(username)}", callback_data="noop")]]
                from shared.components.TeamCloudverse import update_request_status_cloudverse
                await update_request_status_cloudverse(user_id, details, ctx, status_button)
            except (ValueError, TypeError):
                back_button = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="back_to_whitelist")]])
                await update.message.reply_text("Please enter valid hours (e.g., 12 or 24).", reply_markup=back_button)
        return

    # Blacklist duration input
    if "awaiting_blacklist_duration" in ctx.user_data:
        telegram_id = ctx.user_data.pop("awaiting_blacklist_duration", None)
        if not telegram_id:
            return
        text = update.message.text if update.message else ''
        try:
            hours = int(text.strip())
            await BlacklistManager.set_restriction(ctx.bot_data['account_repo'], telegram_id, 'temporary', hours, str(update.message.from_user.id))
            await update.message.reply_text(f"Restriction for user {telegram_id} set to Temporary for {hours} hours.")
        except (ValueError, TypeError):
            await update.message.reply_text("Failed to set restriction duration. Please enter a valid number of hours.")
        return


# ΓöÇΓöÇ Action Handlers ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

def _get_ui():
    class UI:
        pass
    ui = UI()
    from bots.administrator.components.ManageAdmin import manage_admins
    from bots.administrator.components.ManageSuperadmin import manage_super_admins
    from bots.administrator.components.ManageWhitelisted import manage_whitelist
    from bots.administrator.components.ManageBlacklisted import manage_blacklist
    ui.manage_admins = manage_admins
    ui.manage_super_admins = manage_super_admins
    ui.manage_whitelist = manage_whitelist
    ui.manage_blacklist = manage_blacklist
    return ui

async def _handle_add_admin(q, update, ctx, deps):
    _, _, _, _, _, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    if not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only super admins can perform this action\n\nContact <b>Team CloudVerse</b> for more", parse_mode='HTML')
        return
    await q.edit_message_text("Enter the username of the new admin (e.g., @username):")
    ctx.user_data["next_action"] = "add_admin_username"

async def _handle_remove_admin(q, update, ctx, deps, data):
    _, _, _, RoleManager, _, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    ui = _get_ui()
    if not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only super admins can perform this action", parse_mode='HTML')
        return
    admin_id = data.split("remove_admin:")[1]
    if await account_repo.is_super_admin(telegram_id=admin_id):
        await q.edit_message_text("Access Denied : You are not authorised to perform this action", parse_mode='HTML')
        await ui.manage_admins(update, ctx)
        return
    try:
        await RoleManager.remove_admin(account_repo, admin_id)
        await q.edit_message_text(f"Admin with ID {admin_id} has been removed.")
    except ValueError as e:
        await q.edit_message_text(f"Γ¥î {str(e)}")
    await ui.manage_admins(update, ctx)

async def _handle_promote_admin(q, update, ctx, deps, data):
    _, _, _, RoleManager, _, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    history_repo = ctx.bot_data.get('history_repo')
    ui = _get_ui()
    if not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only super admins can perform this action", parse_mode='HTML')
        return
    admin_id = data.split(":")[1]
    try:
        await RoleManager.promote_to_super_admin(account_repo, history_repo, admin_id, telegram_id, q.from_user.username)
        await q.edit_message_text(f"Admin {admin_id} has been promoted to Super Admin.")
        await ui.manage_admins(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to promote admin: {str(e)}")

async def _handle_demote_admin_to_whitelist(q, update, ctx, deps, data):
    _, _, _, RoleManager, _, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    history_repo = ctx.bot_data.get('history_repo')
    ui = _get_ui()
    if not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only super admins can perform this action", parse_mode='HTML')
        return
    admin_id = data.split(":")[1]
    try:
        await RoleManager.demote_to_whitelisted(account_repo, history_repo, admin_id, telegram_id, q.from_user.username, from_role='admin')
        await q.edit_message_text(f"Admin {admin_id} has been demoted to whitelisted user.")
        await ui.manage_admins(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to demote admin: {str(e)}")

async def _handle_demote_super_admin_to_whitelist(q, update, ctx, deps, data):
    _, _, _, RoleManager, _, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    history_repo = ctx.bot_data.get('history_repo')
    ui = _get_ui()
    if not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only super admins can perform this action", parse_mode='HTML')
        return
    admin_id = data.split(":")[1]
    try:
        await RoleManager.demote_to_whitelisted(account_repo, history_repo, admin_id, telegram_id, q.from_user.username, from_role='super_admin')
        await q.edit_message_text(f"Super Admin {admin_id} has been demoted to whitelisted user.")
        await ui.manage_super_admins(update, ctx)
    except PermissionError as e:
        await q.edit_message_text(str(e), parse_mode='HTML')
    except Exception as e:
        await q.edit_message_text(f"Failed to demote super admin: {str(e)}")

async def _handle_demote_super_admin_to_admin(q, update, ctx, deps, data):
    _, _, _, RoleManager, _, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    history_repo = ctx.bot_data.get('history_repo')
    ui = _get_ui()
    if not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only super admins can perform this action", parse_mode='HTML')
        return
    admin_id = data.split(":")[1]
    try:
        await RoleManager.demote_super_admin(account_repo, history_repo, admin_id, telegram_id, q.from_user.username)
        await q.edit_message_text(f"Super Admin {admin_id} has been demoted to Admin.")
        await ui.manage_super_admins(update, ctx)
    except PermissionError as e:
        await q.edit_message_text(str(e), parse_mode='HTML')
    except Exception as e:
        await q.edit_message_text(f"Failed to demote super admin: {str(e)}")

async def _handle_promote_whitelist_to_admin(q, update, ctx, deps, data):
    _, _, _, RoleManager, _, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    history_repo = ctx.bot_data.get('history_repo')
    ui = _get_ui()
    if not await account_repo.is_admin(telegram_id=telegram_id) and not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only admins can perform this action", parse_mode='HTML')
        return
    user_id = data.split(":")[1]
    try:
        await RoleManager.promote_to_admin(account_repo, history_repo, user_id, telegram_id, q.from_user.username)
        await q.edit_message_text(f"User {user_id} has been promoted to Admin.")
        await ui.manage_whitelist(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to promote user: {str(e)}")

async def _handle_ban_whitelist_user(q, update, ctx, deps, data):
    _, _, _, _, WhitelistManager, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    history_repo = ctx.bot_data.get('history_repo')
    ui = _get_ui()
    if not await account_repo.is_admin(telegram_id=telegram_id) and not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only admins can perform this action", parse_mode='HTML')
        return
    user_id = data.split(":")[1]
    try:
        await WhitelistManager.ban_user(account_repo, history_repo, user_id, telegram_id, q.from_user.username)
        await q.edit_message_text(f"User {user_id} has been banned for 24 hours.")
        await ui.manage_whitelist(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to ban user: {str(e)}")

async def _handle_set_whitelist_limit(q, update, ctx, deps, data):
    from shared.utils.time_utils import get_limit_buttons
    user_id = data.split(":")[1]
    ctx.user_data['limit_user_id'] = user_id
    buttons = get_limit_buttons()
    buttons[-1] = [InlineKeyboardButton("Γ¥î Cancel", callback_data=f"whitelist_select:{user_id}")]
    await q.edit_message_text("**Set Time Limit**\n\nSelect duration for the time limit:",
                              reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML')

async def _handle_modify_whitelist_limit(q, update, ctx, deps, data):
    from shared.utils.time_utils import get_limit_buttons
    user_id = data.split(":")[1]
    account_repo = ctx.bot_data['account_repo']
    ctx.user_data['limit_user_id'] = user_id

    all_whitelisted = await account_repo.get_by_role(role='whitelisted')
    user_data = next((u for u in all_whitelisted if str(u['telegram_id']) == str(user_id)), None)

    current_limit = "No Limit"
    if user_data and user_data.get('period') and user_data.get('access_type') == 'limited':
        try:
            exp_time = datetime.fromisoformat(user_data['action_at']) + timedelta(hours=user_data['period'])
            current_limit = "Expired" if exp_time <= datetime.now() else humanize.naturaldelta(exp_time - datetime.now())
        except (ValueError, Exception):
            current_limit = "Unknown"

    buttons = get_limit_buttons()
    buttons[-1] = [InlineKeyboardButton("Γ¥î Cancel", callback_data=f"whitelist_select:{user_id}")]
    await q.edit_message_text(
        f"**Modify Time Limit**\n\n**Current Allotted Limit:** {current_limit}\n\nSelect new duration:",
        reply_markup=InlineKeyboardMarkup(buttons), parse_mode='HTML'
    )

async def _handle_remove_whitelist_limit(q, update, ctx, deps, data):
    _, _, get_server_manager, _, WhitelistManager, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    user_id = data.split(":")[1]
    try:
        await WhitelistManager.remove_time_limit(account_repo, user_id, str(telegram_id))
        await q.edit_message_text("Γ£à Time limit removed successfully!",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Details", callback_data=f"whitelist_select:{user_id}")]]))
    except Exception as e:
        logger.error(f"Error removing time limit for user {user_id}: {e}")
        await get_server_manager().send_error_notification(
            error_message=f"Failed to remove whitelist limit. Details: {str(e)}",
            error_type="Admin Action", severity="MEDIUM"
        )
        await q.edit_message_text("Γ¥î Failed to remove time limit. Please try again.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Details", callback_data=f"whitelist_select:{user_id}")]]))

async def _handle_remove_whitelist(q, update, ctx, deps, data):
    _, _, _, _, WhitelistManager, _, _ = deps
    account_repo = ctx.bot_data['account_repo']
    ui = _get_ui()
    user_id = data.split("remove_whitelist:")[1]
    try:
        await WhitelistManager.remove_user(account_repo, user_id)
        await q.edit_message_text(f"User with ID {user_id} has been removed from the whitelist.")
        await ui.manage_whitelist(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to remove user: {str(e)}")

async def _handle_remove_limit(q, update, ctx, deps, data):
    _, _, _, _, WhitelistManager, _, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    ui = _get_ui()
    user_id = data.split("remove_limit:")[1]
    try:
        await WhitelistManager.remove_time_limit(account_repo, user_id, str(telegram_id))
        ctx.user_data.pop("pending_limit_user", None)
        ctx.user_data.pop("awaiting_limit_hours", None)
        ctx.user_data.pop("next_action", None)
        await ui.manage_whitelist(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to remove limit: {str(e)}")

async def _handle_set_limit(q, update, ctx, deps, data):
    _, escape_markdown, _, _, _, _, _ = deps
    account_repo = ctx.bot_data['account_repo']
    user_id = data.split("set_limit:")[1]
    username = next((u.get('username') for u in await account_repo.get_all() if str(u['telegram_id']) == str(user_id)), None)
    prompt = f"Enter the time limit in hours for user @{escape_markdown(username)} (e.g., 24):" if username else f"Enter the time limit in hours for user {user_id} (e.g., 24):"
    back_button = InlineKeyboardMarkup([[InlineKeyboardButton("Γ¥î Cancel", callback_data="back_to_whitelist")]])
    await q.edit_message_text(prompt, reply_markup=back_button)
    ctx.user_data["next_action"] = f"set_limit_hours:{user_id}"
    ctx.user_data["awaiting_limit_hours"] = True
    ctx.user_data["pending_limit_user"] = user_id

async def _handle_limit_duration(q, update, ctx, deps, data):
    _, _, get_server_manager, _, WhitelistManager, _, _ = deps
    from shared.utils.time_utils import parse_duration_to_hours
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    user_id = ctx.user_data.get('limit_user_id')

    if not user_id:
        await q.edit_message_text("Error: User ID not found. Please try again.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_whitelist")]]))
        return

    duration_str = data.split("limit_")[1]
    try:
        hours = parse_duration_to_hours(duration_str)
        if hours is None:
            await q.edit_message_text("Γ¥î Invalid duration for time limit.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_whitelist")]]))
            return

        await WhitelistManager.set_time_limit(account_repo, user_id, hours, str(telegram_id))
        ctx.user_data.pop('limit_user_id', None)

        await q.edit_message_text(f"Γ£à Time limit set successfully!\n\n**Duration:** {duration_str}",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to List", callback_data="manage_whitelist")]]))
    except Exception as e:
        logger.error(f"Error setting time limit for user {user_id}: {e}")
        await get_server_manager().send_error_notification(
            error_message=f"Failed to set whitelist limit. Details: {str(e)}",
            error_type="Admin Action", severity="MEDIUM"
        )
        await q.edit_message_text("Γ¥î Failed to set time limit. Please try again.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to List", callback_data="manage_whitelist")]]))

async def _handle_promote_blacklist_to_whitelist(q, update, ctx, deps, data):
    _, _, _, _, _, BlacklistManager, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    history_repo = ctx.bot_data.get('history_repo')
    ui = _get_ui()
    if not await account_repo.is_admin(telegram_id=telegram_id) and not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only admins can perform this action", parse_mode='HTML')
        return
    user_id = data.split(":")[1]
    try:
        await BlacklistManager.promote_to_whitelisted(account_repo, history_repo, user_id, telegram_id, q.from_user.username)
        await q.edit_message_text(f"User {user_id} has been promoted to whitelisted.")
        await ui.manage_blacklist(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to promote user: {str(e)}")

async def _handle_set_restriction(q, update, ctx, deps, data):
    _, _, _, _, _, BlacklistManager, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    ui = _get_ui()
    if not await account_repo.is_admin(telegram_id=telegram_id) and not await account_repo.is_super_admin(telegram_id=telegram_id):
        await q.edit_message_text("Access Denied : Only admins can perform this action", parse_mode='HTML')
        return
    parts = data.split(":")
    user_id, restriction = parts[1], parts[2]
    try:
        if restriction == "permanent":
            await BlacklistManager.set_restriction(account_repo, user_id, 'permanent', None, str(telegram_id))
            await q.edit_message_text(f"User {user_id} restriction set to Permanent.")
        else:
            hours = int(restriction)
            await BlacklistManager.set_restriction(account_repo, user_id, 'temporary', hours, str(telegram_id))
            await q.edit_message_text(f"User {user_id} restriction set to {hours} hours.")
        await ui.manage_blacklist(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to set restriction: {str(e)}")

async def _handle_remove_blacklist(q, update, ctx, deps, data):
    _, _, _, _, _, BlacklistManager, _ = deps
    account_repo = ctx.bot_data['account_repo']
    ui = _get_ui()
    user_id = data.split("remove_blacklist:")[1]
    try:
        await BlacklistManager.remove_user(account_repo, user_id)
        await q.edit_message_text(f"User with ID {user_id} has been removed from the blacklist.")
        await ui.manage_blacklist(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to remove user: {str(e)}")

async def _handle_modify_blacklist_restriction(q, update, ctx, deps, data):
    ui = _get_ui()
    await ui.manage_blacklist(update, ctx)

async def _handle_unrestrict_blacklist(q, update, ctx, deps, data):
    _, _, _, _, _, BlacklistManager, _ = deps
    account_repo = ctx.bot_data['account_repo']
    ui = _get_ui()
    user_id = data.split(":")[1]
    try:
        await BlacklistManager.remove_user(account_repo, user_id)
        await q.edit_message_text(f"User {user_id} has been unrestricted.")
        await ui.manage_blacklist(update, ctx)
    except Exception as e:
        await q.edit_message_text(f"Failed to unrestrict user: {str(e)}")

async def _handle_edit_blacklist(q, update, ctx, deps, data):
    tid = data.split(":")[1]
    buttons = [
        [InlineKeyboardButton("ΓÅ│ Temporary", callback_data=f"edit_blacklist_type:Temporary:{tid}"),
         InlineKeyboardButton("Γ¢ö Permanent", callback_data=f"edit_blacklist_type:Permanent:{tid}")],
        [InlineKeyboardButton("Γ¥î Cancel", callback_data="manage_blacklist")]
    ]
    await q.edit_message_text(f"Edit restriction for user {tid}:\nChoose restriction type:", reply_markup=InlineKeyboardMarkup(buttons))
    ctx.user_data["edit_blacklist_user"] = tid

async def _handle_edit_blacklist_type(q, update, ctx, deps, data):
    _, _, _, _, _, BlacklistManager, _ = deps
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    _, restriction_type, tid = data.split(":")
    if restriction_type == "Temporary":
        await q.edit_message_text("Enter the duration in hours for temporary restriction:")
        ctx.user_data["awaiting_blacklist_duration"] = tid
    else:
        await BlacklistManager.set_restriction(account_repo, tid, 'permanent', None, str(telegram_id))
        await q.edit_message_text(f"Restriction for user {tid} set to Permanent.")
        ctx.user_data.pop("edit_blacklist_user", None)

async def _handle_duration_input(q, update, ctx, deps, data):
    _, _, get_server_manager, _, _, BlacklistManager, _ = deps
    from shared.utils.time_utils import parse_duration_to_hours
    telegram_id = getattr(q.from_user, 'id', None)
    account_repo = ctx.bot_data['account_repo']
    user_id = ctx.user_data.get('restriction_user_id')

    if not user_id:
        await q.edit_message_text("Error: User ID not found. Please try again.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="manage_blacklist")]]))
        return

    duration_str = data.split("duration_")[1]
    try:
        hours = parse_duration_to_hours(duration_str)
        restriction_type = 'permanent' if duration_str == "Permanent" else 'temporary'
        await BlacklistManager.set_restriction(account_repo, user_id, restriction_type, hours, str(telegram_id))
        ctx.user_data.pop('restriction_user_id', None)

        await q.edit_message_text(f"Γ£à Restriction updated successfully!\n\n**New restriction:** {duration_str}",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to List", callback_data="manage_blacklist")]]))
    except Exception as e:
        logger.error(f"Error updating restriction for user {user_id}: {e}")
        await get_server_manager().send_error_notification(
            error_message=f"Failed to set blacklist duration. Details: {str(e)}",
            error_type="Admin Action", severity="MEDIUM"
        )
        await q.edit_message_text("Γ¥î Failed to update restriction. Please try again.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to List", callback_data="manage_blacklist")]]))

async def _handle_toggle_bot_filter(q, update, ctx):
    from bots.administrator.utils.db_utils import cycle_active_bot
    cycle_active_bot(ctx)
    await q.answer("Filter updated! Please re-open the menu to see changes.", show_alert=True)

# ΓöÇΓöÇ Routing Dictionaries ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

# Exact string matches
EXACT_HANDLERS = {
    "add_admin": _handle_add_admin,
    "super_admin_prev_page": lambda q, u, c, d, _: _paginate_super_admins(q, u, c, -1),
    "super_admin_next_page": lambda q, u, c, d, _: _paginate_super_admins(q, u, c, 1),
    "toggle_bot_filter": lambda q, u, c, d, _: _handle_toggle_bot_filter(q, u, c),
}

# Prefix string matches (checked in order)
PREFIX_HANDLERS = {
    "remove_admin:": _handle_remove_admin,
    "promote_admin:": _handle_promote_admin,
    "demote_admin_to_whitelist:": _handle_demote_admin_to_whitelist,
    "demote_super_admin_to_whitelist:": _handle_demote_super_admin_to_whitelist,
    "demote_super_admin_to_admin:": _handle_demote_super_admin_to_admin,
    "promote_whitelist_to_admin:": _handle_promote_whitelist_to_admin,
    "ban_whitelist_user:": _handle_ban_whitelist_user,
    "set_whitelist_limit:": _handle_set_whitelist_limit,
    "modify_whitelist_limit:": _handle_modify_whitelist_limit,
    "remove_whitelist_limit:": _handle_remove_whitelist_limit,
    "remove_whitelist:": _handle_remove_whitelist,
    "remove_limit:": _handle_remove_limit,
    "set_limit:": _handle_set_limit,
    "limit_": _handle_limit_duration,
    "promote_blacklist_to_whitelist:": _handle_promote_blacklist_to_whitelist,
    "set_restriction:": _handle_set_restriction,
    "remove_blacklist:": _handle_remove_blacklist,
    "modify_blacklist_restriction:": _handle_modify_blacklist_restriction,
    "unrestrict_blacklist:": _handle_unrestrict_blacklist,
    "edit_blacklist:": _handle_edit_blacklist,
    "edit_blacklist_type:": _handle_edit_blacklist_type,
    "duration_": _handle_duration_input,
    
    "admin_select:": lambda q, u, c, d, data: __import__('bots.administrator.components.ManageAdmin', fromlist=['manage_admins']).manage_admins(u, c),
    "super_admin_select:": lambda q, u, c, d, data: __import__('bots.administrator.components.ManageSuperadmin', fromlist=['manage_super_admins']).manage_super_admins(u, c),
    "whitelist_select:": lambda q, u, c, d, data: __import__('bots.administrator.components.ManageWhitelisted', fromlist=['manage_whitelist']).manage_whitelist(u, c),
    "blacklist_select:": lambda q, u, c, d, data: __import__('bots.administrator.components.ManageBlacklisted', fromlist=['manage_blacklist']).manage_blacklist(u, c),
}

async def _paginate_super_admins(q, update, ctx, offset):
    ctx.user_data["super_admin_page"] = max(0, ctx.user_data.get("super_admin_page", 0) + offset)
    await __import__('bots.administrator.components.ManageSuperadmin', fromlist=['manage_super_admins']).manage_super_admins(update, ctx)


async def _dispatch_callback(q, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Route callback queries to the appropriate handler using dictionary dispatch."""
    deps = _get_deps()
    data = q.data

    # Exact match
    if data in EXACT_HANDLERS:
        if EXACT_HANDLERS[data].__name__ == "<lambda>":
            await EXACT_HANDLERS[data](q, update, ctx, deps, data)
        else:
            await EXACT_HANDLERS[data](q, update, ctx, deps)
        return

    # Prefix match
    for prefix, handler in PREFIX_HANDLERS.items():
        if data.startswith(prefix):
            await handler(q, update, ctx, deps, data)
            return

    logger.warning(f"Unhandled callback data in ActionDispatcher: {data}")
