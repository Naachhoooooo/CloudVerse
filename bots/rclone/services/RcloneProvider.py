"""
RcloneProvider — rclone implementation of ProviderInterface.

rclone does not store credentials per OAuth token. Instead, users upload an
rclone .conf file which is stored encrypted in rclone.db. The 'service' object
is the selected remote name string.

Login = collecting and storing the user's rclone config file.
Logout = removing the stored rclone config from DB.

All file operations use ConfigHelper.user_config() to decrypt→write→pass→delete
the per-user config for every rclone call. Never persists plaintext config.

CRITICAL: uses ctx.bot_data['credential_repo'] (rclone.db only) — NEVER shared credential_management.

Tags: [RCLONE][AUTH]
"""

from typing import Any, Dict, List, Tuple, Optional
from shared.core.Provider import ProviderInterface
from bots.rclone.services import RcloneService
from shared.core.Logger import get_logger
from shared.core.UserState import UserState, UserStateEnum

logger = get_logger(__name__)


class RcloneProvider(ProviderInterface):
    """rclone wrapper implementing the generic ProviderInterface."""

    # ── Credential management ─────────────────────────────────────────────────
    # rclone config is stored as an encrypted blob. These methods are no-ops;
    # the do_login/do_logout auth flow manages the credential lifecycle directly.

    async def get_credentials(
        self, telegram_id: int, account_email: Optional[str] = None
    ) -> Optional[Dict]:
        return None

    async def set_credentials(
        self, telegram_id: int, account_email: str, credentials_dict: dict
    ):
        pass

    async def remove_credentials(
        self, telegram_id: int, account_email: Optional[str] = None
    ):
        pass

    # ── Service acquisition ───────────────────────────────────────────────────

    async def get_service(
        self, telegram_id: int, account_identifier: Any = None
    ) -> Any:
        """
        For rclone the 'service' is the selected remote name string.
        Returns first available remote from user's config, or account_identifier if provided.
        """
        if account_identifier and account_identifier != "default_account":
            return account_identifier
        # No specific remote selected — just indicate no service yet
        return None

    async def get_user_info(self, service: Any) -> Dict[str, Any]:
        return {"user": {"displayName": "rclone Remote", "emailAddress": str(service or "")}}

    # ── Storage ───────────────────────────────────────────────────────────────

    async def get_storage_info(self, service: Any) -> Dict[str, Any]:
        """
        Get storage quota from rclone about --json for the selected remote.
        service = remote name, ctx_or_repo = needs credential_repo passed externally.
        Because ProviderInterface doesn't carry ctx here, storage info must use
        a pre-established config_path from the caller context.
        Returns storageQuota compatible dict.
        """
        # service is expected to be a tuple (remote_name, config_path) when called
        # from StorageDetails component (which has access to ctx)
        if isinstance(service, tuple) and len(service) == 2:
            remote, config_path = service
        else:
            logger.warning("[RCLONE] get_storage_info called without config_path")
            return {"storageQuota": {"limit": "0", "usage": "0", "usageInDriveTrash": "0"}}
        try:
            info = await RcloneService.get_storage_info(config_path, remote)
            return {
                "storageQuota": {
                    "limit": str(info.get("total") or 0),
                    "usage": str(info.get("used") or 0),
                    "usageInDriveTrash": str(info.get("trashed") or 0),
                }
            }
        except Exception as e:
            logger.warning(f"[RCLONE] get_storage_info failed for {remote}: {e}")
            return {"storageQuota": {"limit": "0", "usage": "0", "usageInDriveTrash": "0"}}

    # ── File listing ──────────────────────────────────────────────────────────

    async def get_folder_name(self, service: Any, folder_id: str) -> str:
        if not folder_id or folder_id == "root":
            return "rclone Root"
        return folder_id.split("/")[-1] if "/" in folder_id else folder_id

    async def list_files(
        self,
        service: Any,
        folder_id: str = "root",
        page_token: Optional[str] = None,
        page_size: int = 10,
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        service = (remote_name, config_path) for per-user config.
        """
        if isinstance(service, tuple) and len(service) == 2:
            remote, config_path = service
        else:
            return [], None

        path = "" if folder_id in ("root", "None", None, "") else folder_id
        if path and not path.endswith("/"):
            path += "/"
        try:
            items = await RcloneService.list_files(config_path, remote, path)
            formatted = [
                {
                    "id": item["Path"] if path == "" else f"{path}{item['Path']}",
                    "name": item["Name"],
                    "mimeType": (
                        "application/vnd.google-apps.folder"
                        if item["IsDir"]
                        else item.get("MimeType", "application/octet-stream")
                    ),
                    "size": str(item.get("Size", 0)),
                }
                for item in items
            ]
            return formatted, None
        except Exception as e:
            logger.error(f"[RCLONE] list_files failed for {remote}: {e}", exc_info=True)
            return [], None

    async def list_trashed_files(
        self,
        service: Any,
        page_token: Optional[str] = None,
        page_size: int = 10,
    ) -> Tuple[List[Dict], Optional[str]]:
        """rclone has no unified trash — always returns empty."""
        return [], None

    # ── File mutations ────────────────────────────────────────────────────────

    async def create_folder(
        self, service: Any, name: str, parent_id: Optional[str] = None
    ) -> Any:
        """service = (remote_name, config_path)."""
        if isinstance(service, tuple) and len(service) == 2:
            remote, config_path = service
        else:
            return name
        root = parent_id if parent_id and parent_id != "root" else ""
        path = f"{remote}{root}/{name}".lstrip("/")
        try:
            await RcloneService.mkdir(config_path, path)
            return {"id": path}
        except Exception as e:
            logger.error(f"[RCLONE] create_folder failed: {e}", exc_info=True)
            raise

    async def rename_file(self, service: Any, file_id: str, new_name: str) -> Any:
        """rclone moveto for renaming. service = (remote_name, config_path)."""
        if isinstance(service, tuple) and len(service) == 2:
            remote, config_path = service
        else:
            return False
        parent = "/".join(file_id.rstrip("/").split("/")[:-1])
        new_path = f"{parent}/{new_name}" if parent else new_name
        try:
            await RcloneService.move_file(config_path, f"{remote}{file_id}", f"{remote}{new_path}")
            return {"id": new_path, "name": new_name}
        except Exception as e:
            logger.error(f"[RCLONE] rename_file failed: {e}", exc_info=True)
            return False

    async def delete_file(self, service: Any, file_id: str) -> bool:
        """service = (remote_name, config_path)."""
        if isinstance(service, tuple) and len(service) == 2:
            remote, config_path = service
        else:
            return False
        try:
            return await RcloneService.delete_file(config_path, remote, file_id)
        except Exception as e:
            logger.error(f"[RCLONE] delete_file failed for {file_id}: {e}", exc_info=True)
            return False

    async def toggle_sharing(self, service: Any, file_id: str) -> None:
        """rclone sharing not universally supported."""
        logger.warning(f"[RCLONE] toggle_sharing not universally supported for {file_id}")

    async def get_file_link(self, service: Any, file_id: str) -> str:
        logger.warning(f"[RCLONE] get_file_link not implemented for {file_id}")
        return ""

    async def get_file_metadata(self, service: Any, file_id: str) -> Dict:
        """service = (remote_name, config_path)."""
        if isinstance(service, tuple) and len(service) == 2:
            remote, config_path = service
        else:
            return {}
        try:
            items = await RcloneService.list_files(config_path, remote, file_id)
            if items:
                item = items[0]
                return {
                    "id": file_id,
                    "name": item.get("Name", file_id.split("/")[-1]),
                    "size": item.get("Size", 0),
                    "mimeType": "application/vnd.google-apps.folder" if item.get("IsDir") else item.get("MimeType", "application/octet-stream")
                }
            return {}
        except Exception as e:
            logger.warning(f"[RCLONE] get_file_metadata failed for {file_id}: {e}")
            return {}

    async def restore_file(self, service: Any, file_id: str) -> Any:
        """rclone has no unified trash — restore is a no-op."""
        logger.warning("[RCLONE] restore_file not applicable for rclone")
        return None

    async def empty_trash(self, service: Any) -> bool:
        """rclone has no unified trash — no-op."""
        logger.warning("[RCLONE] empty_trash not applicable for rclone")
        return False

    async def get_item_size(
        self, service: Any, item_id: str, is_folder: bool = False
    ) -> int:
        return 0

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _send_upload_prompt(self, update: Any, telegram_id: int, user_state: Any) -> None:
        msg = (
            "📄 *rclone Setup*\n\n"
            "To connect a cloud remote, please send your `rclone.conf` file.\n\n"
            "You can find it at:\n"
            "• Linux/macOS: `~/.config/rclone/rclone.conf`\n"
            "• Windows: `%APPDATA%\\rclone\\rclone.conf`\n\n"
            "Run `rclone config` to create one if you haven't already."
        )
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="HTML")
        else:
            await update.message.reply_text(msg, parse_mode="HTML")

        user_state.set_state(UserStateEnum.EXPECTING_RCLONE_CONFIG)
        logger.info(f"[RCLONE][AUTH] Config upload prompt sent for user {telegram_id}")

    async def do_login(self, update: Any, ctx: Any) -> None:

        if ctx.user_data is None:
            ctx.user_data = {}
        if "state" not in ctx.user_data or not isinstance(ctx.user_data["state"], UserState):
            ctx.user_data["state"] = UserState(ctx)
        user_state: UserState = ctx.user_data["state"]

        telegram_id = (
            update.callback_query.from_user.id
            if update.callback_query and update.callback_query.from_user
            else update.effective_user.id if update.effective_user else None
        )

        credential_repo = ctx.bot_data.get("credential_repo")
        if credential_repo and telegram_id:
            try:
                existing = await credential_repo.get_full_credentials(str(telegram_id))
                if existing and existing.get("rclone_credential"):
                    msg = "✅ You already have an rclone config stored. Remove it first via /settings → Logout."
                    if update.callback_query:
                        await update.callback_query.edit_message_text(msg)
                    elif update.message:
                        await update.message.reply_text(msg)
                    return
            except Exception as e:
                logger.warning(f"[RCLONE][AUTH] Error checking existing config for {telegram_id}: {e}")

        await self._send_upload_prompt(update, telegram_id, user_state)

    async def _process_logout_confirmation(self, q: Any, action: str, telegram_id: int, credential_repo: Any) -> None:
        if action == "yes":
            try:
                if credential_repo:
                    await credential_repo.delete(str(telegram_id))
                await q.edit_message_text("✅ rclone configuration removed.")
                logger.info(f"[RCLONE][AUTH] User {telegram_id} removed rclone config")
            except Exception as e:
                logger.error(f"[RCLONE][AUTH] Logout failed for {telegram_id}: {e}", exc_info=True)
                await q.edit_message_text("❌ Failed to remove config. Please try again.")
        else:
            await q.edit_message_text("Logout cancelled.")

    async def _show_logout_prompt(self, msg_or_query: Any) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes", callback_data="confirm_logout:yes"),
             InlineKeyboardButton("❌ No", callback_data="confirm_logout:no")]
        ])
        if hasattr(msg_or_query, 'edit_message_text'):
            await msg_or_query.edit_message_text("⚠️ Are you sure you want to remove your rclone configuration?", reply_markup=reply_markup)
        else:
            await msg_or_query.reply_text("⚠️ Are you sure you want to remove your rclone configuration?", reply_markup=reply_markup)

    async def do_logout(self, update: Any, ctx: Any) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        if ctx.user_data is None:
            ctx.user_data = {}

        q = update.callback_query
        m = update.message
        
        if not q and not m:
            return

        telegram_id = q.from_user.id if q else m.from_user.id
        credential_repo = ctx.bot_data.get("credential_repo")

        if q and q.data and q.data.startswith("confirm_logout:"):
            await q.answer()
            action = q.data.split(":")[1]
            await self._process_logout_confirmation(q, action, telegram_id, credential_repo)
            return

        has_config = False
        if credential_repo:
            try:
                existing = await credential_repo.get_full_credentials(str(telegram_id))
                has_config = bool(existing and existing.get("rclone_credential"))
            except Exception as e:
                logger.warning(f"[RCLONE][AUTH] Error checking config during logout for {telegram_id}: {e}")

        if not has_config:
            msg_text = "ℹ️ No rclone configuration found."
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="SETTINGS")]])
            if q:
                await q.edit_message_text(msg_text, reply_markup=reply_markup)
            else:
                await m.reply_text(msg_text, reply_markup=reply_markup)
            return

        await self._show_logout_prompt(q if q else m)
