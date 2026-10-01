"""
WhitelistManager — business logic for whitelist operations.

Handles time-limit management, user removal, and banning.
"""

from shared.core.Logger import get_logger
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)


async def set_time_limit(account_repo, user_id: str, hours: int, admin_id: str):
    """Set a time limit on a whitelisted user's access."""
    await account_repo.update_allotted(
        telegram_id=user_id, access_type='limited',
        period=hours, action_by=str(admin_id)
    )
    logger.info(f"[AUTH] Admin {admin_id} set time limit for user {user_id} to {hours}h")


async def remove_time_limit(account_repo, user_id: str, admin_id: str):
    """Remove time limit, granting permanent access."""
    await account_repo.update_allotted(
        telegram_id=user_id, access_type='permanent',
        period=None, action_by=str(admin_id)
    )
    logger.info(f"[AUTH] Admin {admin_id} removed time limit for user {user_id}")


async def remove_user(account_repo, user_id: str):
    """Remove a user from the whitelist entirely."""
    await account_repo.delete(telegram_id=user_id)
    logger.info(f"[AUTH] User {user_id} removed from whitelist")


async def ban_user(account_repo, history_repo, user_id: str, admin_id, admin_username: str = None, hours: int = 24):
    """Ban a whitelisted user (move to blacklist)."""
    user_data = await account_repo.get(telegram_id=user_id)
    await account_repo.ban(
        telegram_id=user_id, restriction_type='temporary',
        period=hours, action_by=str(admin_id)
    )
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=user_id,
            username=user_data.get('username') if user_data else None,
            user_role='whitelisted', user_role_after='blacklisted',
            action_taken="blacklist", status="success",
            admin_telegram_id=admin_id, admin_username=admin_username,
            admin_role='admin', event_details=f"Banned for {hours} hours"
        ))
    logger.info(f"[AUTH] User {user_id} banned for {hours}h by {admin_id}")
