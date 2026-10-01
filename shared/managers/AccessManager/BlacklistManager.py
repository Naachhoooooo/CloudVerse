"""
BlacklistManager — business logic for blacklist operations.

Handles restriction management, unblocking, and ban reminder notifications.
"""

from shared.core.Logger import get_logger
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)


async def set_restriction(account_repo, user_id: str, restriction_type: str, hours, admin_id: str):
    """Set or update a blacklist restriction (temporary or permanent)."""
    period = None if restriction_type == 'permanent' else hours
    await account_repo.update_restriction(
        telegram_id=user_id, restriction_type=restriction_type,
        period=period, action_by=str(admin_id)
    )
    label = "Permanent" if restriction_type == 'permanent' else f"{hours}h temporary"
    logger.info(f"[AUTH] Admin {admin_id} set {label} restriction on user {user_id}")


async def promote_to_whitelisted(account_repo, history_repo, user_id: str, admin_id, admin_username: str = None):
    """Promote a blacklisted user back to whitelisted."""
    user_data = await account_repo.get(telegram_id=user_id)
    await account_repo.promote(
        telegram_id=user_id, role='whitelisted',
        handled_by=str(admin_id), access_type='permanent',
        period=None, action_by=str(admin_id)
    )
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=user_id,
            username=user_data.get('username') if user_data else None,
            user_role='blacklisted', user_role_after='whitelisted',
            action_taken="whitelist", status="success",
            admin_telegram_id=admin_id, admin_username=admin_username,
            admin_role='admin', event_details="Promoted from blacklisted"
        ))
    logger.info(f"[AUTH] Blacklisted user {user_id} promoted to whitelisted by {admin_id}")


async def remove_user(account_repo, user_id: str):
    """Remove a user from the blacklist entirely."""
    await account_repo.delete(telegram_id=user_id)
    logger.info(f"[AUTH] User {user_id} removed from blacklist")


async def send_banned_user_reminder(bot, telegram_id: str, name: str, restriction_type: str, hours_remaining=None):
    """Send a reminder to a banned user that they are currently restricted.

    This was previously referenced but never implemented — fixes the
    missing-function bug in the access_required decorator.
    """
    try:
        import html
        safe_name = html.escape(name)
        if restriction_type == 'permanent':
            duration_text = "Your ban is <b>permanent</b>."
        elif hours_remaining:
            duration_text = f"Your ban has approximately <b>{hours_remaining} hours</b> remaining."
        else:
            duration_text = "Your ban is currently active."

        message = (
            f"⛔ <b>Access Restricted</b>\n\n"
            f"Hello {safe_name},\n\n"
            f"Your access to <b>CloudVerse</b> is currently suspended.\n"
            f"{duration_text}\n\n"
            f"If you believe this is an error, please reach out to us.\n\n"
            f"— 🛡️ <b>Team CloudVerse</b>"
        )
        await bot.send_message(
            chat_id=telegram_id, text=message,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Failed to send ban reminder to {telegram_id}: {e}")
