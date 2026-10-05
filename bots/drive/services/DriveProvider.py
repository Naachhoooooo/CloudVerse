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
            logger.warning(f"Error fetching file metadata for {file_id}: {e}")
            return {}

    async def list_trashed_files(self, service: Any, page_token: Optional[str] = None, page_size: int = 100) -> Tuple[List[Dict], Optional[str]]:
        return await DriveService.list_trashed_files(service, page_token, page_size)

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
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
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
            # We revert back to Desktop App / Web App config
            flow = InstalledAppFlow.from_client_config(config, scopes)
            flow.redirect_uri = 'http://127.0.0.1'
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            
            msg = (
                "🔐 <b>Authorization Steps</b>\n\n"
                "Please follow these steps to login:\n\n"
                "1. Open the given Authorization URL\n"
                "2. Sign in with your Google Drive account\n"
                "3. Give permission to access Google Drive\n"
                "4. Copy the entire URL from your browser's address bar and send it to me\n\n"
                "<i>⚠️ Note: After authorising, Google will redirect you to an error page (e.g. 'Site can't be reached'). This is expected! Just copy the URL from that page.</i>"
            )
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
            buttons = [[InlineKeyboardButton("Authorization URL", url=auth_url)]]
            
            if update.callback_query:
                await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML", disable_web_page_preview=True)
                await update.callback_query.message.reply_text("Enter the authorization code:", reply_markup=ReplyKeyboardMarkup([["Cancel"]], resize_keyboard=True, one_time_keyboard=True))
            else:
                await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML", disable_web_page_preview=True)
                await update.message.reply_text("Enter the authorization code:", reply_markup=ReplyKeyboardMarkup([["Cancel"]], resize_keyboard=True, one_time_keyboard=True))

            user_state.set_state(UserStateEnum.EXPECTING_CODE)
            if not hasattr(self, "_flows"):
                self._flows = {}
            self._flows[telegram_id] = flow
            logger.info(f"Drive OAuth flow started for user {telegram_id}")
        except Exception as e:
            logger.error(f"Failed to start Drive OAuth flow for {telegram_id}: {e}", exc_info=True)
            msg = f"❌ Failed to start login: {e}"
            if update.callback_query:
                await update.callback_query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)

    async def do_login(self, update: Any, ctx: Any) -> None:
        from shared.core.UserState import UserState, UserStateEnum
        
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

        if update.message and user_state.is_state(UserStateEnum.EXPECTING_CODE):
            text = update.message.text.strip() if update.message.text else ""
            if text.startswith("/"):
                user_state.reset()
            elif text.lower() == "cancel":
                from telegram import ReplyKeyboardRemove
                user_state.reset()
                await update.message.reply_text("❌ Login cancelled.", reply_markup=ReplyKeyboardRemove())
                return
            else:
                await self._handle_code_input(update, user_state, telegram_id)
                return
        if await self._check_existing_credentials(telegram_id, update):
            return

        await self._initiate_oauth_flow(telegram_id, update, ctx, user_state)

    async def _handle_code_input(self, update: Any, user_state: Any, telegram_id: int) -> None:
        from bots.drive.config import BOT_DB_PATH
        from shared.managers.EncryptionManager import get_encryption_manager
        from shared.database.repositories.DriveCredentialsRepository import DriveCredentialsRepository
        
        code = update.message.text.strip()
        if "code=" in code:
            from urllib.parse import urlparse, parse_qs
            parsed_url = urlparse(code)
            qs = parse_qs(parsed_url.query)
            if "code" in qs:
                code = qs["code"][0]
            
        flow = getattr(self, "_flows", {}).get(telegram_id)
        if not flow:
            await update.message.reply_text("❌ Login session expired. Please try logging in again.")
            from telegram import ReplyKeyboardRemove
            user_state.reset()
            return
            
        try:
            flow.fetch_token(code=code)
            creds = flow.credentials
            
            from googleapiclient.discovery import build
            service = build('drive', 'v3', credentials=creds)
            about = service.about().get(fields="user").execute()
            email_address = about.get("user", {}).get("emailAddress", "Unknown")
            display_name = about.get("user", {}).get("displayName", "")
            
            repo = DriveCredentialsRepository(str(BOT_DB_PATH), get_encryption_manager())
            creds_data = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": creds.scopes
            }
            username = update.message.from_user.username or display_name
            await repo.upsert(telegram_id, username, email_address, creds_data)
            email_str = email_address[0].upper() + email_address[1:] if email_address else "Unknown"
            await update.message.reply_text(f"✅ <b>Account Linked Successfully!</b>\n📧 <code>{email_str}</code>", parse_mode="HTML")
            logger.info(f"User {telegram_id} successfully linked Google Drive as {email_address}")
        except Exception as e:
            logger.error(f"Failed to fetch token for {telegram_id}: {e}", exc_info=True)
            from telegram import ReplyKeyboardRemove
            await update.message.reply_text("❌ Invalid code or authorization failed. Please try again.", reply_markup=ReplyKeyboardRemove())
        finally:
            if hasattr(self, "_flows") and telegram_id in self._flows:
                del self._flows[telegram_id]
            user_state.reset()

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

        q = update.callback_query
        m = update.message
        if not q and not m:
            return

        telegram_id = q.from_user.id if q else m.from_user.id

        # Handle confirmation response
        if q and q.data and q.data.startswith("confirm_logout:"):
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
            msg_text = "⚠️ No linked account found."
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="SETTINGS")]])
            if q:
                await q.edit_message_text(msg_text, reply_markup=reply_markup)
            else:
                await m.reply_text(msg_text, reply_markup=reply_markup)
            return

        # Show confirmation prompt
        buttons = [
            [InlineKeyboardButton("✅ Yes", callback_data="confirm_logout:yes"),
             InlineKeyboardButton("❌ No", callback_data="confirm_logout:no")]
        ]
        msg_text = "⚠️ Are you sure you want to logout of your Google Account?"
        reply_markup = InlineKeyboardMarkup(buttons)
        if q:
            await q.edit_message_text(msg_text, reply_markup=reply_markup)
        else:
            await m.reply_text(msg_text, reply_markup=reply_markup)
