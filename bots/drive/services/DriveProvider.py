from typing import Optional, Dict, List, Tuple, Any
import asyncio
from shared.core.Provider import ProviderInterface
from bots.drive.services import DriveService
from shared.core.Logger import get_logger
from shared.core.UserState import UserState, UserStateEnum

logger = get_logger(__name__)

class DriveProvider(ProviderInterface):
    """
    Drive implementation of the ProviderInterface.
    Delegates file-management calls to DriveService.py.
    Owns all Google OAuth login/logout logic (do_login / do_logout).
    """

    async def get_credentials(self, telegram_id: int, account_email: Optional[str] = None) -> Optional[Dict]:
        return await DriveService.get_credentials(telegram_id, account_email)

    async def set_credentials(self, telegram_id: int, account_email: str, credentials_dict: dict):
        return await DriveService.set_credentials(telegram_id, account_email, credentials_dict)

    async def remove_credentials(self, telegram_id: int, account_email: Optional[str] = None):
        return await DriveService.remove_credentials(telegram_id, account_email)

    async def get_service(self, telegram_id: int, account_email: Optional[str] = None) -> Any:
        return await DriveService.get_drive_service(telegram_id, account_email)

    async def get_folder_name(self, service: Any, folder_id: str) -> str:
        return await DriveService.get_folder_name(service, folder_id)

    async def list_files(self, service: Any, folder_id: str = "root", page_token: Optional[str] = None, page_size: int = 10) -> Tuple[List[Dict], Optional[str]]:
        return await DriveService.list_files(service, folder_id, page_token, page_size)

    async def list_trashed_files(self, service: Any, page_token: Optional[str] = None, page_size: int = 10) -> Tuple[List[Dict], Optional[str]]:
        return await DriveService.list_trashed_files(service, page_token, page_size)

    async def create_folder(self, service: Any, name: str, parent_id: Optional[str] = None) -> Any:
        return await DriveService.create_folder(service, name, parent_id)

    async def rename_file(self, service: Any, file_id: str, new_name: str) -> Any:
        return await DriveService.rename_file(service, file_id, new_name)

    async def delete_file(self, service: Any, file_id: str) -> bool:
        return await DriveService.delete_file(service, file_id)

    async def toggle_sharing(self, service: Any, file_id: str) -> None:
        return await DriveService.toggle_sharing(service, file_id)

    async def get_file_link(self, service: Any, file_id: str) -> str:
        return await DriveService.get_file_link(service, file_id)

    async def get_file_metadata(self, service: Any, file_id: str) -> Dict:
        """Fetch name, size, mimeType for a file or folder via Drive API."""
        try:
            loop = asyncio.get_running_loop()
            meta = await loop.run_in_executor(
                None,
                lambda: service.files().get(fileId=file_id, fields="id,name,size,mimeType").execute()
            )
            return meta
        except Exception as e:
            logger.warning(f"[DRIVE] get_file_metadata failed for {file_id}: {e}")
            return {}

    async def get_storage_info(self, service: Any) -> Dict:
        return await DriveService.get_storage_info(service)

    async def get_user_info(self, service: Any) -> Dict:
        return await DriveService.get_user_info(service)

    async def restore_file(self, service: Any, file_id: str) -> Any:
        return await DriveService.restore_file(service, file_id)

    async def empty_trash(self, service: Any) -> bool:
        return await DriveService.empty_trash(service)

    async def get_item_size(self, service: Any, item_id: str, is_folder: bool = False) -> int:
        """Get file size in bytes via Drive API. Returns 0 for folders (not exposed by API)."""
        try:
            if not is_folder:
                loop = asyncio.get_running_loop()
                file = await loop.run_in_executor(
                    None,
                    lambda: service.files().get(fileId=item_id, fields="size").execute()
                )
                return int(file.get('size', 0))
            return 0
        except Exception as e:
            logger.warning(f"[DRIVE] Failed to get item size for {item_id}: {e}")
            return 0

    async def _check_existing_credentials(self, telegram_id: int, update: Any) -> bool:
        from bots.drive.config import BOT_DB_PATH
        from shared.managers.EncryptionManager import get_encryption_manager
        from shared.database.repositories.DriveCredentialsRepository import DriveCredentialsRepository
        try:
            repo = DriveCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
            if await repo.has_credentials(telegram_id=telegram_id):
                msg = "✅ You already have a Google account linked. Log out first to link a new one."
                if update.callback_query:
                    await update.callback_query.edit_message_text(msg)
                else:
                    await update.message.reply_text(msg)
                return True
            return False
        except Exception as e:
            logger.error(f"Error checking credentials before Drive login for {telegram_id}: {e}", exc_info=True)
            return False

    async def _initiate_oauth_flow(self, telegram_id: int, update: Any, ctx: Any, user_state: Any) -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow
        config = ctx.bot_data.get("gdrive_client_config")
        scopes = ctx.bot_data.get("gdrive_scopes", ["https://www.googleapis.com/auth/drive"])
        if not config:
            msg = "❌ Google Drive is not configured. Contact the admin."
            if update.callback_query:
                await update.callback_query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)
            return

        try:
            flow = InstalledAppFlow.from_client_config(config, scopes, redirect_uri='urn:ietf:wg:oauth:2.0:oob')
            auth_url, _ = flow.authorization_url(prompt='consent')
            msg = (
                "🔐 Click the link below to authorise your Google account:\n\n"
                f"{auth_url}\n\n"
                "After authorisation, paste the code you receive here."
            )
            if update.callback_query:
                await update.callback_query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)

            user_state.set_state(UserStateEnum.EXPECTING_CODE)
            user_state.data["flow"] = flow
            logger.info(f"Drive OAuth flow started for user {telegram_id}")
        except Exception as e:
            logger.error(f"Failed to start Drive OAuth flow for {telegram_id}: {e}", exc_info=True)
            msg = f"❌ Failed to start login: {e}"
            if update.callback_query:
                await update.callback_query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)

    async def do_login(self, update: Any, ctx: Any) -> None:

        if ctx.user_data is None:
            ctx.user_data = {}
        if "state" not in ctx.user_data or not isinstance(ctx.user_data["state"], UserState):
            ctx.user_data["state"] = UserState(ctx)
        user_state: UserState = ctx.user_data["state"]

        if update.callback_query and update.callback_query.from_user:
            telegram_id = update.callback_query.from_user.id
        elif update.message and update.message.from_user:
            telegram_id = update.message.from_user.id
        else:
            return

        if await self._check_existing_credentials(telegram_id, update):
            return

        await self._initiate_oauth_flow(telegram_id, update, ctx, user_state)

    async def do_logout(self, update: Any, ctx: Any) -> None:
        """
        Google Drive logout flow.
        Shows a Yes/No confirmation keyboard, then removes credentials on confirmation.
        """
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from bots.drive.config import BOT_DB_PATH
        from shared.managers.EncryptionManager import get_encryption_manager
        from shared.database.repositories.DriveCredentialsRepository import DriveCredentialsRepository

        if ctx.user_data is None:
            ctx.user_data = {}

        if not (update.callback_query and update.callback_query.from_user):
            return

        q = update.callback_query
        telegram_id = q.from_user.id

        # Handle confirmation response
        if q.data and q.data.startswith("confirm_logout:"):
            await q.answer()
            action = q.data.split(":")[1]
            repo = DriveCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
            if action == "yes":
                if await repo.clear_credentials(telegram_id=telegram_id):
                    await q.edit_message_text("✅ Successfully logged out of your Google account.")
                    logger.info(f"User {telegram_id} logged out of Google Drive")
                else:
                    await q.edit_message_text("❌ Failed to logout. Please try again.")
            else:
                await q.edit_message_text("Logout cancelled.")
            return

        # Guard: nothing to logout from
        repo = DriveCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
        has_creds = await repo.has_credentials(telegram_id=telegram_id)
        if not has_creds:
            await q.edit_message_text(
                "ℹ️ No linked account found.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="SETTINGS")]])
            )
            return

        # Show confirmation prompt
        buttons = [
            [InlineKeyboardButton("✅ Yes", callback_data="confirm_logout:yes"),
             InlineKeyboardButton("❌ No", callback_data="confirm_logout:no")]
        ]
        await q.edit_message_text(
            "⚠️ Are you sure you want to logout of your Google Account?",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
