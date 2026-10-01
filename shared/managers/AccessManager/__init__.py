"""
AccessManager package — authentication decorators, access lifecycle, and
business logic for user role management.

Replaces the old monolithic AccessManager.py. All existing import paths
continue to work unchanged.
"""

import asyncio
from datetime import datetime, timedelta
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from shared.core.Logger import get_logger
from shared.database.repositories.AccountRepository import AccountRepository
from shared.database.repositories.HistoryRepository import HistoryRepository
from shared.core.AsyncUtils import track_task

logger = get_logger(__name__)

# ── Auth Decorators ──────────────────────────────────────────────────────────

def _extract_telegram_id(update: Update):
    """Extract telegram_id from any update type."""
    if hasattr(update, 'effective_user') and update.effective_user:
        return update.effective_user.id
    if hasattr(update, 'message') and update.message and getattr(update.message, 'from_user', None):
        return update.message.from_user.id
    if hasattr(update, 'callback_query') and update.callback_query and getattr(update.callback_query, 'from_user', None):
        return update.callback_query.from_user.id
    return None


async def _check_maintenance(update: Update, ctx: ContextTypes.DEFAULT_TYPE, telegram_id) -> bool:
    """Returns True if access is allowed, False if blocked by maintenance."""
    try:
        from shared.managers.MaintenanceManager import get_maintenance_manager
        can_access, message = await get_maintenance_manager().check_access(
            int(telegram_id) if telegram_id else 0,
            ctx.bot_data.get('account_repo')
        )
        if not can_access:
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.answer(message, show_alert=True)
            elif hasattr(update, 'message') and update.message:
                await update.message.reply_text(message, parse_mode='HTML')
            return False
    except Exception as e:
        logger.error(f"Maintenance gating failed: {e}")
    return True


async def _send_denial(update: Update, message: str):
    """Send an access-denied response via the most appropriate channel."""
    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.answer(message, show_alert=True)
    elif hasattr(update, 'message') and update.message:
        await update.message.reply_text(message)


def _is_addressed_to_us(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns False if this is a reply to a DIFFERENT bot's message."""
    if hasattr(update, 'message') and update.message and update.message.reply_to_message:
        reply_user = update.message.reply_to_message.from_user
        if reply_user and reply_user.is_bot and reply_user.id != ctx.bot.id:
            return False
    return True


def admin_required(handler):
    """Decorator: requires admin or super-admin privileges."""
    @wraps(handler)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not _is_addressed_to_us(update, ctx):
            return

        telegram_id = _extract_telegram_id(update)
        logger.debug(f"Admin access check for {handler.__name__} by user {telegram_id}")

        if not await _check_maintenance(update, ctx, telegram_id):
            return

        account_repo = ctx.bot_data.get('account_repo')
        if not (telegram_id and account_repo and (
            await account_repo.is_admin(telegram_id) or
            await account_repo.is_super_admin(telegram_id)
        )):
            logger.warning(f"Admin access denied for user {telegram_id} → {handler.__name__}")
            await _send_denial(update, "You don't have permission to access this feature.")
            return

        logger.debug(f"Admin access granted for user {telegram_id} → {handler.__name__}")
        return await handler(update, ctx, *args, **kwargs)
    return wrapper


def super_admin_required(handler):
    """Decorator: requires super-admin privileges."""
    @wraps(handler)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not _is_addressed_to_us(update, ctx):
            return

        telegram_id = _extract_telegram_id(update)
        account_repo = ctx.bot_data.get('account_repo')
        if not (telegram_id and account_repo and await account_repo.is_super_admin(telegram_id)):
            await _send_denial(update, "Only the super admin can perform this action.")
            return
        return await handler(update, ctx, *args, **kwargs)
    return wrapper


def access_required(handler):
    """Decorator: requires whitelisted / admin access. Blocks banned users."""
    @wraps(handler)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE, **kwargs):
        if not _is_addressed_to_us(update, ctx):
            return

        telegram_id = _extract_telegram_id(update)
        user_name = None
        if hasattr(update, 'effective_user') and update.effective_user:
            user_name = update.effective_user.full_name

        if not await _check_maintenance(update, ctx, telegram_id):
            return

        account_repo = ctx.bot_data.get('account_repo')
        user_account = await account_repo.get(telegram_id) if account_repo else None

        # Blocked: blacklisted user
        if user_account and user_account['role'] == 'blacklisted':
            try:
                from .BlacklistManager import send_banned_user_reminder
                await send_banned_user_reminder(
                    ctx.bot, str(telegram_id),
                    user_name or user_account.get('name', 'User'),
                    user_account.get('restriction_type', 'Permanent'),
                    user_account.get('hours_remaining')
                )
            except Exception as e:
                logger.error(f"Failed to send ban reminder to user {telegram_id}: {e}")
            return

        if not (telegram_id and account_repo and (
            await account_repo.is_super_admin(telegram_id) or
            await account_repo.is_admin(telegram_id) or
            await account_repo.is_whitelisted(telegram_id)
        )):
            await _send_denial(update, "Access denied. You do not have permission to perform this action.")
            return

        return await handler(update, ctx, **kwargs)
    return wrapper


# ── AccessManager Class (Ban Lifecycle) ──────────────────────────────────────

class AccessManager:
    """Provider-agnostic access/ban lifecycle manager.

    Handles automatic unban after temporary bans expire, whitelist expiry,
    and user notifications via a callback pattern.
    """

    def __init__(self, account_repo: AccountRepository, history_repo: HistoryRepository, notification_func=None):
        self._account_repo = account_repo
        self._history_repo = history_repo
        self.notification_func = notification_func
        self.running = False
        self.task = None
        self.loop_interval = 300

    async def start(self):
        if self.running:
            return
        self.running = True
        self.task = track_task(self._countdown_loop())
        logger.info("AccessManager started")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                logger.debug("AccessManager task cancelled")
        logger.info("AccessManager stopped")

    async def _countdown_loop(self):
        while self.running:
            try:
                await self._process_countdown()
                await asyncio.sleep(self.loop_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in access countdown loop: {e}")
                await asyncio.sleep(60)

    async def _process_countdown(self):
        try:
            logger.debug("Processing access countdown...")
            await self._handle_expired_bans()
            await self._handle_expired_whitelists()

            # Send whitelist expiry reminders (within 30 minutes)
            all_whitelisted = await self._account_repo.get_by_role(role='whitelisted')
            for user in all_whitelisted:
                if user.get('action_at') and user.get('period'):
                    try:
                        exp_time = datetime.fromisoformat(user['action_at']) + timedelta(hours=user['period'])
                        if datetime.now() < exp_time <= datetime.now() + timedelta(minutes=30):
                            if self.notification_func:
                                await self.notification_func("whitelist_expiry_warning", user['telegram_id'], user_data=user)
                    except Exception as e:
                        logger.error(f"Failed to trigger expiry reminder to {user['telegram_id']}: {e}")

        except Exception as e:
            logger.error(f"Error processing access countdown: {e}")

    async def _handle_expired_bans(self):
        all_blacklisted = await self._account_repo.get_by_role(role='blacklisted')
        expired = [u for u in all_blacklisted if await self._account_repo.is_expired(telegram_id=u['telegram_id'])]

        unbanned = []
        for user in expired:
            tid = user['telegram_id']
            try:
                await self._account_repo.promote(
                    telegram_id=tid, role='whitelisted',
                    handled_by="system", access_type='permanent',
                    period=None, action_by="system"
                )
                unbanned.append(tid)
                if self.notification_func:
                    await self.notification_func("ban_expired", tid, user_data=user)
                track_task(self._history_repo.create(
                    telegram_id=tid, action_taken="auto_unban", status="success",
                    admin_telegram_id="system",
                    event_details="Automatic unban after temporary ban period"
                ))
            except Exception as e:
                logger.error(f"Failed to auto-unban user {tid}: {e}")

        if unbanned:
            logger.info(f"Automatically unbanned {len(unbanned)} users: {unbanned}")

    async def _handle_expired_whitelists(self):
        all_whitelisted = await self._account_repo.get_by_role(role='whitelisted')
        expired = [u for u in all_whitelisted if await self._account_repo.is_expired(telegram_id=u['telegram_id'])]

        for user in expired:
            tid = user['telegram_id']
            try:
                await self._account_repo.update(telegram_id=tid, role='pending', updated_by="system")
                if self.notification_func:
                    await self.notification_func("whitelist_expired", tid, user_data=user)
                track_task(self._history_repo.create(
                    telegram_id=tid, action_taken="auto_expiry", status="success",
                    admin_telegram_id="system",
                    event_details="Automatic expiry of whitelist access"
                ))
            except Exception as e:
                logger.error(f"Failed to auto-expire whitelist for {tid}: {e}")

    # Notification sending delegates fully to component layer

    async def get_status(self):
        all_blacklisted = await self._account_repo.get_by_role(role='blacklisted')
        active_bans = [u for u in all_blacklisted if not await self._account_repo.is_expired(telegram_id=u['telegram_id'])]
        return {
            'running': self.running,
            'temporarily_banned_count': len(active_bans),
            'temporarily_banned_users': active_bans
        }


# ── Module-Level Lifecycle ───────────────────────────────────────────────────

_countdown_manager = None


async def start_access_manager(account_repo: AccountRepository, history_repo: HistoryRepository, notification_func=None):
    global _countdown_manager
    if _countdown_manager is None:
        _countdown_manager = AccessManager(account_repo, history_repo, notification_func)
    await _countdown_manager.start()


async def stop_access_manager():
    if _countdown_manager:
        await _countdown_manager.stop()


async def get_access_status():
    if _countdown_manager:
        return await _countdown_manager.get_status()
    return {'running': False, 'temporarily_banned_count': 0, 'temporarily_banned_users': []}


# Backward-compat aliases
async def start_ban_countdown_manager(application, account_repo, history_repo):
    async def default_notifier(event, telegram_id, user_data=None):
        try:
            msg = ""
            if event == "whitelist_expiry_warning":
                msg = "Your access to the bot is about to expire. Please contact an admin to renew."
            elif event == "whitelist_expired":
                msg = "Your access to the bot has expired. Please request access again by sending /start."
            elif event == "ban_expired":
                import html
                safe_full_name = html.escape(user_data.get("name", "User") if user_data else "User")
                msg = (
                    "<b>Ban Period Completed - Access Restored</b>\n\n"
                    f"Hello {safe_full_name},\n\n"
                    "Your <b>temporary suspension</b> has <b>ended</b> and access to <b>CloudVerse</b> has been restored.\n"
                    "Please follow the rules to avoid further suspensions."
                )
            if msg:
                await application.bot.send_message(chat_id=telegram_id, text=msg, parse_mode='HTML')
        except Exception:
            pass
            
    await start_access_manager(account_repo, history_repo, default_notifier)


async def stop_ban_countdown_manager():
    await stop_access_manager()


async def notify_access_expired(telegram_id, bot):
    await bot.send_message(
        chat_id=telegram_id,
        text="Your access to the bot has expired. Please request access again by sending /start."
    )
