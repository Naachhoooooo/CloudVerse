"""
Shared AccountRepository — manages cloudverse_accounts in shared.db.
Replaces the monolithic _account_management_sync god method.

Accepts optional history_repo for audit logging (fire-and-forget).
"""
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from shared.database.repositories.BaseRepository import BaseRepository
from shared.core.Logger import get_logger
from shared.core.AsyncUtils import track_task
from shared.core.CacheUtils import async_lru_cache
logger = get_logger(__name__)


class AccountRepository(BaseRepository):
    def __init__(self, db_path: str, history_repo=None):
        super().__init__(db_path)
        self.table_name = "cloudverse_accounts"
        self._history_repo = history_repo

    # ── Audit helper ──────────────────────────────────────────────────────
    def _log_history(self, **kwargs):
        """Fire-and-forget history entry if history_repo is injected."""
        if self._history_repo:
            try:
                track_task(self._history_repo.create(**kwargs))
            except RuntimeError:
                pass  # no running loop (e.g. during shutdown)

    # ── Write operations ──────────────────────────────────────────────────

    async def create_pending(
        self, telegram_id, username=None, name=None,
        request_message_id=None, handled_by=None,
        language_code=None, is_premium=0
    ) -> bool:
        """Insert or replace a pending access request with flood control."""
        # Flood Control Check
        existing = await self.get(telegram_id)
        if existing and existing.get('role') == 'pending' and existing.get('requested_at'):
            try:
                # requested_at format might be standard sqlite timestamp, isoformat handles most
                requested_at = datetime.fromisoformat(existing['requested_at'].replace(' ', 'T'))
                if datetime.now() - requested_at < timedelta(hours=24):
                    logger.warning(f"[AUTH] Flood control triggered for user {telegram_id}")
                    return False
            except Exception as e:
                logger.debug(f"[AUTH] Error parsing requested_at for flood control: {e}")

        logger.info(f"[AUTH] Creating pending account for {telegram_id}")
        query = """INSERT OR REPLACE INTO cloudverse_accounts
                   (telegram_id, username, name, language_code, is_premium, role, request_message_id, 
                    requested_at, last_updated, is_protected, access_type, restriction_type, period, 
                    action_by, action_at, last_active, handled_by, handled_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, 
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, NULL, NULL, NULL, 
                           NULL, NULL, CURRENT_TIMESTAMP, NULL, NULL)"""
        await self.execute(query, (
            str(telegram_id), username, name, language_code, is_premium, request_message_id,
        ))
        self._log_history(
            telegram_id=telegram_id, action_taken="pending_create",
            status="success", admin_telegram_id=handled_by or "SYSTEM",
            event_details="Pending account created",
        )
        logger.info(f"[AUTH] Pending account created for {telegram_id}")
        self.get.cache_delete(telegram_id)
        return True

    async def promote(
        self, telegram_id, role, handled_by=None,
        access_type=None, period=None, action_by=None, promoted_by=None,
    ) -> bool:
        if role not in ('whitelisted', 'admin', 'super_admin'):
            raise ValueError("Invalid promotion role")
        handled_at = datetime.now().isoformat()
        query = """UPDATE cloudverse_accounts SET role = ?, handled_by = ?, handled_at = ?,
                   access_type = ?, period = ?, action_by = ?, action_at = CURRENT_TIMESTAMP,
                   is_protected = 0, last_active = CURRENT_TIMESTAMP,
                   last_updated = CURRENT_TIMESTAMP WHERE telegram_id = ?"""
        await self.execute(query, (
            role, handled_by, handled_at, access_type, period,
            action_by, str(telegram_id),
        ))
        # Unlimited quota for admins/super_admins
        if role in ('admin', 'super_admin'):
            await self.execute(
                "UPDATE cloudverse_usage SET daily_transfer_limit = NULL WHERE telegram_id = ?",
                (str(telegram_id),),
            )
        self._log_history(
            telegram_id=telegram_id, action_taken="promotion",
            status="success", admin_telegram_id=promoted_by,
            event_details=f"Promoted to {role}",
        )
        logger.info(f"[AUTH] Account {telegram_id} promoted to {role}")
        self.get.cache_delete(telegram_id)
        return True

    async def ban(
        self, telegram_id, restriction_type=None, period=None, action_by=None,
    ) -> bool:
        action_at = datetime.now().isoformat()
        query = """UPDATE cloudverse_accounts SET role = 'blacklisted', restriction_type = ?, period = ?,
                   action_by = ?, action_at = ?, access_type = NULL, last_active = CURRENT_TIMESTAMP, 
                   last_updated = CURRENT_TIMESTAMP WHERE telegram_id = ?"""
        await self.execute(query, (
            restriction_type, period, action_by, action_at, str(telegram_id),
        ))
        self._log_history(
            telegram_id=telegram_id, action_taken="ban",
            status="success", admin_telegram_id=action_by,
            event_details="Account banned",
        )
        logger.info(f"[AUTH] Account {telegram_id} banned")
        self.get.cache_delete(telegram_id)
        return True

    async def reject(self, telegram_id, handled_by=None) -> bool:
        handled_at = datetime.now().isoformat()
        query = """UPDATE cloudverse_accounts SET role = 'rejected', handled_by = ?, handled_at = ?,
                   last_updated = CURRENT_TIMESTAMP WHERE telegram_id = ?"""
        await self.execute(query, (handled_by, handled_at, str(telegram_id)))
        self._log_history(
            telegram_id=telegram_id, action_taken="reject",
            status="success", admin_telegram_id=handled_by,
            event_details="Account rejected",
        )
        logger.info(f"[AUTH] Account {telegram_id} rejected")
        self.get.cache_delete(telegram_id)
        return True

    async def update(
        self, telegram_id, name=None, username=None, role=None,
        request_message_id=None, language_code=None, is_premium=None,
        updated_by=None,
    ) -> bool:
        fields, values = [], []
        if name is not None:
            fields.append("name = ?")
            values.append(name)
        if username is not None:
            fields.append("username = ?")
            values.append(username)
        if role is not None:
            fields.append("role = ?")
            values.append(role)
        if request_message_id is not None:
            fields.append("request_message_id = ?")
            values.append(request_message_id)
        if language_code is not None:
            fields.append("language_code = ?")
            values.append(language_code)
        if is_premium is not None:
            fields.append("is_premium = ?")
            values.append(is_premium)

        if fields:
            fields.append("last_updated = CURRENT_TIMESTAMP")
            query = f"UPDATE cloudverse_accounts SET {', '.join(fields)} WHERE telegram_id = ?"  # nosec B608
            values.append(str(telegram_id))
            await self.execute(query, tuple(values))
            self._log_history(
                telegram_id=telegram_id, action_taken="account_update",
                status="success", admin_telegram_id=updated_by,
                event_details="Account updated",
            )
        logger.info(f"[AUTH] Account {telegram_id} updated")
        self.get.cache_delete(telegram_id)
        return True

    async def sync_super_admins(self, super_admin_id_string: Optional[str]) -> None:
        """
        Synchronizes super admins based on the environment configuration.
        Promotes missing users to super_admin and demotes removed super_admins to whitelisted.
        """
        if not super_admin_id_string:
            env_super_admins = set()
        else:
            env_super_admins = {sid.strip() for sid in super_admin_id_string.split(',')}

        # 1. Fetch current super admins from DB
        current_super_admins_records = await self.fetch_all(
            "SELECT telegram_id FROM cloudverse_accounts WHERE role = 'super_admin'",
            ()
        )
        current_super_admins = {str(row['telegram_id']) for row in current_super_admins_records}

        # 2. Promote new super admins
        new_admins = env_super_admins - current_super_admins
        for sid in new_admins:
            # UPSERT into accounts
            upsert_account_query = """
                INSERT INTO cloudverse_accounts (telegram_id, role, is_protected, handled_at, last_updated, last_active)
                VALUES (?, 'super_admin', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(telegram_id) DO UPDATE SET 
                    role = 'super_admin',
                    is_protected = 1,
                    last_updated = CURRENT_TIMESTAMP,
                    last_active = CURRENT_TIMESTAMP
            """
            await self.execute(upsert_account_query, (sid,))
            
            # UPSERT into usage
            upsert_usage_query = """
                INSERT INTO cloudverse_usage (telegram_id, last_reset, last_updated)
                VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(telegram_id) DO UPDATE SET 
                    daily_transfer_limit = NULL,
                    last_updated = CURRENT_TIMESTAMP
            """
            await self.execute(upsert_usage_query, (sid,))
            
            self._log_history(
                telegram_id=sid, action_taken="promotion",
                status="success", admin_telegram_id="SYSTEM",
                event_details="System auto-promoted Super Admin based on .env configuration"
            )
            logger.info(f"[AUTH] Auto-promoted super admin: {sid}")
            self.get.cache_delete(sid)

        # 3. Demote removed super admins
        demoted_admins = current_super_admins - env_super_admins
        for sid in demoted_admins:
            demote_query = """
                UPDATE cloudverse_accounts 
                SET role = 'whitelisted', is_protected = 0, last_updated = CURRENT_TIMESTAMP 
                WHERE telegram_id = ?
            """
            await self.execute(demote_query, (sid,))
            
            # Reset daily limit to a default value (e.g., 5GB)
            reset_limit_query = """
                UPDATE cloudverse_usage 
                SET daily_transfer_limit = 5, last_updated = CURRENT_TIMESTAMP 
                WHERE telegram_id = ?
            """
            await self.execute(reset_limit_query, (sid,))
            
            self._log_history(
                telegram_id=sid, action_taken="demotion",
                status="success", admin_telegram_id="SYSTEM",
                event_details="System auto-demoted Super Admin to whitelisted due to removal from .env configuration"
            )
            logger.info(f"[AUTH] Auto-demoted super admin: {sid}")
            self.get.cache_delete(sid)

    async def update_allotted(
        self, telegram_id, access_type=None, period=None,
        action_by=None, updated_by=None,
    ) -> bool:
        query = """UPDATE cloudverse_accounts SET access_type = ?, period = ?, action_by = ?,
                   action_at = CURRENT_TIMESTAMP, last_updated = CURRENT_TIMESTAMP WHERE telegram_id = ?"""
        await self.execute(query, (access_type, period, action_by, str(telegram_id)))
        self._log_history(
            telegram_id=telegram_id, action_taken="allot_update",
            status="success", admin_telegram_id=updated_by,
            event_details="Allotment updated",
        )
        logger.info(f"[AUTH] Allotment updated for {telegram_id}")
        self.get.cache_delete(telegram_id)
        return True

    async def update_restriction(
        self, telegram_id, restriction_type=None, period=None,
        action_by=None, updated_by=None,
    ) -> bool:
        query = """UPDATE cloudverse_accounts SET restriction_type = ?, period = ?, action_by = ?,
                   action_at = CURRENT_TIMESTAMP, last_updated = CURRENT_TIMESTAMP WHERE telegram_id = ?"""
        await self.execute(query, (restriction_type, period, action_by, str(telegram_id)))
        self._log_history(
            telegram_id=telegram_id, action_taken="restrict_update",
            status="success", admin_telegram_id=updated_by,
            event_details="Restriction updated",
        )
        logger.info(f"[AUTH] Restriction updated for {telegram_id}")
        self.get.cache_delete(telegram_id)
        return True

    async def delete(self, telegram_id, updated_by=None) -> bool:
        if await self.is_protected(telegram_id):
            raise ValueError("Cannot delete protected account")
            
        t_id = str(telegram_id)
        
        # 1. Wipe usage statistics
        await self.execute("DELETE FROM cloudverse_usage WHERE telegram_id = ?", (t_id,))
        # 2. Wipe stored credentials
        await self.execute("DELETE FROM cloudverse_credentials WHERE telegram_id = ?", (t_id,))
        # 3. Wipe past and active file transfers
        await self.execute("DELETE FROM cloudverse_transfers WHERE telegram_id = ?", (t_id,))
        # 4. Wipe pending or past broadcast requests
        await self.execute("DELETE FROM cloudverse_broadcasts WHERE requester_telegram_id = ?", (t_id,))
        # 5. Wipe activity history
        await self.execute("DELETE FROM cloudverse_history WHERE telegram_id = ?", (t_id,))
        
        # 6. Finally, delete the core account record
        query = "DELETE FROM cloudverse_accounts WHERE telegram_id = ?"
        await self.execute(query, (t_id,))
        self._log_history(
            telegram_id=telegram_id, action_taken="account_delete",
            status="success", admin_telegram_id=updated_by,
            event_details="Account deleted",
        )
        logger.info(f"[AUTH] Account {telegram_id} deleted")
        self.get.cache_delete(telegram_id)
        return True

    # ── Read operations ───────────────────────────────────────────────────

    @async_lru_cache(maxsize=500)
    async def get(self, telegram_id) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_accounts WHERE telegram_id = ?"
        row = await self.fetch_one(query, (str(telegram_id),))
        return row

    async def get_all(self, filters: Optional[Dict] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_accounts WHERE 1=1"
        params: list = []
        if filters:
            for key, value in filters.items():
                query += f" AND {key} = ?"  # nosec B608
                params.append(value)
        return await self.fetch_all(query, tuple(params))

    async def get_by_role(
        self, role, filters: Optional[Dict] = None,
        limit: Optional[int] = None, offset: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_accounts WHERE role = ?"
        params: list = [role]
        if filters:
            for key, value in filters.items():
                query += f" AND {key} = ?"  # nosec B608
                params.append(value)
        query += " ORDER BY last_updated DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            if offset is not None:
                query += " OFFSET ?"
                params.append(offset)
        return await self.fetch_all(query, tuple(params))

    async def count_by_role(self, role, filters: Optional[Dict] = None) -> int:
        query = "SELECT COUNT(*) FROM cloudverse_accounts WHERE role = ?"
        params: list = [role]
        if filters:
            for key, value in filters.items():
                query += f" AND {key} = ?"  # nosec B608
                params.append(value)
        row = await self.fetch_one(query, tuple(params))
        return list(row.values())[0] if row else 0

    # ── Boolean checks ────────────────────────────────────────────────────

    async def is_expired(self, telegram_id) -> bool:
        account = await self.get(telegram_id)
        if account:
            action_at = account.get('action_at')
            period = account.get('period')
            access_type = account.get('access_type')
            restriction_type = account.get('restriction_type')
            current_role = account.get('role')

            if current_role == 'whitelisted' and access_type == 'limited' and period and action_at:
                exp_time = datetime.fromisoformat(action_at.replace('Z', '+00:00')) + timedelta(hours=period)
                return datetime.now() > exp_time
            elif current_role == 'blacklisted' and restriction_type == 'temporary' and period and action_at:
                exp_time = datetime.fromisoformat(action_at.replace('Z', '+00:00')) + timedelta(hours=period)
                return datetime.now() > exp_time
        return False

    async def is_whitelisted(self, telegram_id) -> bool:
        account = await self.get(telegram_id)
        if account and account.get('role') == 'whitelisted':
            return not await self.is_expired(telegram_id)
        return False

    async def is_blacklisted(self, telegram_id) -> bool:
        account = await self.get(telegram_id)
        if account and account.get('role') == 'blacklisted':
            return not await self.is_expired(telegram_id)
        return False

    async def is_admin(self, telegram_id) -> bool:
        account = await self.get(telegram_id)
        return account is not None and account.get('role') in ('admin', 'super_admin')

    async def is_super_admin(self, telegram_id) -> bool:
        account = await self.get(telegram_id)
        return account is not None and account.get('role') == 'super_admin'

    async def is_protected(self, telegram_id) -> bool:
        account = await self.get(telegram_id)
        return bool(account.get('is_protected')) if account else False

    async def first_registered(self, telegram_id) -> Optional[str]:
        account = await self.get(telegram_id)
        return account.get('requested_at') if account else None
