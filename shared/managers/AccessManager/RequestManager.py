"""
RequestManager — business logic for pending access request operations.

Handles approve/reject DB mutations and history logging. UI rendering
stays in AccessControl.py.
"""

from shared.core.Logger import get_logger
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)


async def approve_permanent(account_repo, user_id: str, admin_id: str, history_repo=None):
    """Approve a pending request with permanent access."""
    await account_repo.promote(
        telegram_id=user_id, role='whitelisted',
        handled_by=str(admin_id), access_type='permanent',
        period=None, action_by=str(admin_id)
    )
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=user_id, action_taken="approve_permanent",
            status="success", admin_telegram_id=str(admin_id),
            event_details="Approved with permanent access"
        ))
    logger.info(f"[AUTH] Pending user {user_id} approved (permanent) by {admin_id}")


async def approve_limited(account_repo, user_id: str, admin_id: str, hours: int, history_repo=None):
    """Approve a pending request with time-limited access."""
    await account_repo.promote(
        telegram_id=user_id, role='whitelisted',
        handled_by=str(admin_id), access_type='limited',
        period=hours, action_by=str(admin_id)
    )
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=user_id, action_taken="approve_limited",
            status="success", admin_telegram_id=str(admin_id),
            event_details=f"Approved with {hours}-hour access"
        ))
    logger.info(f"[AUTH] Pending user {user_id} approved ({hours}h) by {admin_id}")


async def reject_request(account_repo, user_id: str, admin_id: str, history_repo=None):
    """Reject a pending access request."""
    await account_repo.reject(telegram_id=user_id, handled_by=str(admin_id))
    if history_repo:
        track_task(history_repo.safe_create(
            telegram_id=user_id, action_taken="reject",
            status="success", admin_telegram_id=str(admin_id),
            event_details="Access request rejected"
        ))
    logger.info(f"[AUTH] Pending user {user_id} rejected by {admin_id}")
