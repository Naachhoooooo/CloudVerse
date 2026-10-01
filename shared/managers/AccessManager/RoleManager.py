"""
RoleManager — business logic for admin/super-admin role changes.

Handles promotions, demotions, removals with history logging.
"""

from shared.core.Logger import get_logger
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)


async def promote_to_super_admin(account_repo, history_repo, user_id: str, admin_id, admin_username: str = None):
    """Promote an admin to super-admin."""
    user_data = await account_repo.get(telegram_id=user_id)
    await account_repo.promote(
        telegram_id=user_id, role='super_admin',
        handled_by=str(admin_id), access_type=None,
        period=None, action_by=str(admin_id)
    )
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=user_id, username=user_data.get('username') if user_data else None,
            user_role='admin', user_role_after='super_admin',
            action_taken="promotion", status="success",
            admin_telegram_id=admin_id, admin_username=admin_username,
            admin_role='super_admin', event_details="Promoted to super_admin"
        ))
    logger.info(f"[AUTH] User {user_id} promoted to super_admin by {admin_id}")


async def promote_to_admin(account_repo, history_repo, user_id: str, admin_id, admin_username: str = None, from_role: str = 'whitelisted'):
    """Promote a whitelisted user to admin."""
    user_data = await account_repo.get(telegram_id=user_id)
    await account_repo.promote(
        telegram_id=user_id, role='admin',
        handled_by=str(admin_id), access_type=None,
        period=None, action_by=str(admin_id)
    )
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=user_id, username=user_data.get('username') if user_data else None,
            user_role=from_role, user_role_after='admin',
            action_taken="promotion", status="success",
            admin_telegram_id=admin_id, admin_username=admin_username,
            admin_role='admin', event_details="Promoted to admin"
        ))
    logger.info(f"[AUTH] User {user_id} promoted to admin by {admin_id}")


async def demote_super_admin(account_repo, history_repo, admin_id: str, demoted_by, demoted_by_username: str = None):
    """Demote a super-admin to admin. Checks protected status."""
    if await account_repo.is_protected(telegram_id=admin_id):
        raise PermissionError("Cannot demote the protected super admin.")

    user_data = await account_repo.get(telegram_id=admin_id)
    await account_repo.promote(
        telegram_id=admin_id, role='admin',
        handled_by=str(demoted_by), access_type=None,
        period=None, action_by=str(demoted_by)
    )
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=admin_id, username=user_data.get('username') if user_data else None,
            user_role='super_admin', user_role_after='admin',
            action_taken="demotion", status="success",
            admin_telegram_id=demoted_by, admin_username=demoted_by_username,
            admin_role='super_admin', event_details="Demoted to admin"
        ))
    logger.info(f"[AUTH] Super admin {admin_id} demoted to admin by {demoted_by}")


async def demote_to_whitelisted(account_repo, history_repo, user_id: str, demoted_by, demoted_by_username: str = None, from_role: str = 'admin'):
    """Demote an admin/super-admin to whitelisted. Checks protected status for super-admins."""
    if from_role == 'super_admin' and await account_repo.is_protected(telegram_id=user_id):
        raise PermissionError("Cannot demote the protected super admin.")

    user_data = await account_repo.get(telegram_id=user_id)
    await account_repo.promote(
        telegram_id=user_id, role='whitelisted',
        handled_by=str(demoted_by), access_type='permanent',
        period=None, action_by=str(demoted_by)
    )
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=user_id, username=user_data.get('username') if user_data else None,
            user_role=from_role, user_role_after='whitelisted',
            action_taken="demotion", status="success",
            admin_telegram_id=demoted_by, admin_username=demoted_by_username,
            admin_role='super_admin', event_details=f"Demoted from {from_role} to whitelisted"
        ))
    logger.info(f"[AUTH] User {user_id} demoted from {from_role} to whitelisted by {demoted_by}")


async def remove_admin(account_repo, admin_id: str):
    """Remove an admin entirely."""
    await account_repo.delete(telegram_id=admin_id)
    logger.info(f"[AUTH] Admin {admin_id} removed")
