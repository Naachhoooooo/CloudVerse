"""
MegaProvider — Mega.nz implementation of ProviderInterface.

Uses mega.py to manage per-user Mega sessions in memory.
Owns all Mega login/logout auth flow (do_login / do_logout).

CRITICAL: uses ctx.bot_data['credential_repo'] (mega.db) — never shared credential_management.
"""

import asyncio
from typing import Any, Dict, List, Tuple, Optional
from shared.core.Provider import ProviderInterface
from shared.core.Logger import get_logger
from shared.core.UserState import UserState, UserStateEnum
from mega import Mega
import mega.errors

if hasattr(mega.errors, '_CODE_TO_DESCRIPTIONS') and -26 not in mega.errors._CODE_TO_DESCRIPTIONS:
    mega.errors._CODE_TO_DESCRIPTIONS[-26] = ('EMFAREQUIRED', 'Multi-factor authentication required')


logger = get_logger(__name__)


class MegaProvider(ProviderInterface):
    """Mega.nz implementation of the ProviderInterface using mega.py."""

    def __init__(self):
        self.credential_repo = None
        self.sessions: Dict[int, Mega] = {}
        self._files_cache: Dict[int, Tuple[float, dict]] = {}

    async def _get_cached_files(self, service: Any) -> dict:
        import time
        now = time.time()
        sid = id(service)
        if sid in self._files_cache:
            ts, files = self._files_cache[sid]
            if now - ts < 30:  # Cache for 30 seconds
                return files
        files = await asyncio.to_thread(service.get_files)
        self._files_cache[sid] = (now, files)
        return files

    def _invalidate_cache(self, service: Any):
        sid = id(service)
        if sid in self._files_cache:
            del self._files_cache[sid]

    def set_credential_repo(self, repo: Any):
        self.credential_repo = repo

    # ── Credential management ─────────────────────────────────────────────────

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
        self, telegram_id: int, account_email: Optional[str] = None
    ) -> Any:
        """
        Returns a Mega instance logged in for the user, or None if not logged in.
        """
        if telegram_id in self.sessions:
            return self.sessions[telegram_id]

        if self.credential_repo:
            try:
                creds = await self.credential_repo.get(str(telegram_id))
                if creds and creds.get('email_address') and creds.get('mega_credential'):
                    try:
                        password = creds['mega_credential']
                        email = creds['email_address']
                        
                        def _login():
                            m = Mega()
                            m.login(email, password)
                            return m
                        
                        m = await asyncio.to_thread(_login)
                        self.sessions[telegram_id] = m
                        logger.info(f"[MEGA] Auto-login triggered for {telegram_id}")
                        return m
                    except Exception as dec_err:
                        logger.error(f"[MEGA] Auto-login decryption or login failed for {telegram_id}: {dec_err}")
            except Exception as e:
                logger.error(f"[MEGA] Error fetching credentials during get_service: {e}")
        
        return None

    # ── User info ─────────────────────────────────────────────────────────────

    async def get_user_info(self, service: Any) -> Dict[str, Any]:
        """service = Mega instance."""
        try:
            user_data = await asyncio.to_thread(service.get_user)
            email = user_data.get("email", "Mega User") if user_data else "Mega User"
            return {"user": {"displayName": email, "emailAddress": email}}
        except Exception as e:
            logger.error(f"[MEGA] get_user_info failed: {e}")
            return {"user": {"displayName": "Mega User", "emailAddress": "unknown"}}

    # ── Storage ───────────────────────────────────────────────────────────────

    async def get_storage_info(self, service: Any) -> Dict[str, Any]:
        """
        Fetch real storage quota via mega.py get_quota().
        Returns storageQuota dict compatible with shared StorageDetails.py.
        """
        try:
            space = await asyncio.to_thread(service.get_storage_space)
            used = space.get("used", 0)
            total = space.get("total", 0)
            return {
                "storageQuota": {
                    "limit": str(total),
                    "usage": str(used),
                    "usageInDriveTrash": "0",
                }
            }
        except Exception as e:
            logger.error(f"[MEGA] get_storage_info failed: {e}", exc_info=True)
            return {
                "storageQuota": {"limit": "0", "usage": "0", "usageInDriveTrash": "0"}
            }

    # ── File listing ──────────────────────────────────────────────────────────

    async def get_folder_name(self, service: Any, folder_id: str) -> str:
        """folder_id = node_id."""
        if not folder_id or folder_id in ("root", "/"):
            return "Mega Root"
        try:
            files = await self._get_cached_files(service)
            if folder_id in files:
                return files[folder_id]["a"]["n"]
        except Exception as e:
            logger.warning(f"[MEGA] get_folder_name failed: {e}")
        return "Mega Folder"

    async def get_file_metadata(self, service: Any, file_id: str) -> dict:
        """Return a normalised metadata dict matching the shape FileManager expects.
        Fields: name, size (str bytes), mimeType.
        """
        if not file_id or file_id in ("root", "/"):
            return {"name": "Mega Root", "size": None, "mimeType": "folder"}
        try:
            files = await self._get_cached_files(service)
            node = files.get(file_id)
            if node:
                is_dir = node.get("t") in (1, 2, 3, 4)
                return {
                    "name": node.get("a", {}).get("n", "Unknown"),
                    "size": str(node.get("s", 0)) if not is_dir else None,
                    "mimeType": "application/vnd.google-apps.folder" if is_dir else node.get("a", {}).get("c", "application/octet-stream"),
                }
        except Exception as e:
            logger.warning(f"[MEGA] get_file_metadata failed for {file_id}: {e}")
        return {}

    async def list_files(
        self,
        service: Any,
        folder_id: str = "root",
        page_token: Optional[str] = None,
        page_size: int = 10,
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        List files using mega.py.
        mega.py get_files() returns a flat dict of all nodes.
        We have to filter by parent_id.
        """
        try:
            def _get_children(files):
                parent = service.root_id if folder_id in ("root", "", "/", None) else folder_id
                
                children = []
                for node_id, node in files.items():
                    if node.get("p") == parent:
                        # 0: file, 1: directory, 2: root, 3: inbox, 4: trash
                        is_dir = node.get("t") in (1, 2, 3, 4)
                        children.append({
                            "id": node_id,
                            "name": node["a"]["n"],
                            "mimeType": "application/vnd.google-apps.folder" if is_dir else "application/octet-stream",
                            "size": str(node.get("s", 0)),
                        })
                return children

            files = await self._get_cached_files(service)
            children = await asyncio.to_thread(_get_children, files)
            
            start = 0
            if page_token:
                try:
                    start = int(page_token)
                except ValueError:
                    start = 0
            
            page = children[start: start + page_size]
            next_token = str(start + page_size) if start + page_size < len(children) else None
            
            return page, next_token
        except Exception as e:
            logger.error(f"[MEGA] list_files failed: {e}", exc_info=True)
            return [], None

    async def list_trashed_files(
        self,
        service: Any,
        page_token: Optional[str] = None,
        page_size: int = 10,
    ) -> Tuple[List[Dict], Optional[str]]:
        """
        List files in Mega's Rubbish Bin.
        Node type 4 is trash folder.
        """
        try:
            def _get_trash(files):
                trash_id = None
                for node_id, node in files.items():
                    if node.get("t") == 4:
                        trash_id = node_id
                        break
                
                if not trash_id:
                    return []
                    
                children = []
                for node_id, node in files.items():
                    if node.get("p") == trash_id:
                        is_dir = node.get("t") == 1
                        children.append({
                            "id": node_id,
                            "name": node["a"]["n"],
                            "mimeType": "application/vnd.google-apps.folder" if is_dir else "application/octet-stream",
                            "size": str(node.get("s", 0)),
                        })
                return children

            files = await self._get_cached_files(service)
            children = await asyncio.to_thread(_get_trash, files)
            
            start = 0
            if page_token:
                try:
                    start = int(page_token)
                except ValueError:
                    start = 0
            
            page = children[start: start + page_size]
            next_token = str(start + page_size) if start + page_size < len(children) else None
            return page, next_token
        except Exception as e:
            logger.warning(f"[MEGA] list_trashed_files failed: {e}")
            return [], None

    # ── File mutations ────────────────────────────────────────────────────────

    async def create_folder(
        self, service: Any, name: str, parent_id: Optional[str] = None
    ) -> Any:
        try:
            def _mkdir():
                parent = service.root_id if parent_id in ("root", "", "/", None) else parent_id
                # service.create_folder(name, dest) where dest is node_id
                res = service.create_folder(name, parent)
                # create_folder returns dict with node info usually, or tuple
                # Handle mega.py return types
                if isinstance(res, dict):
                    # Sometimes it returns a dict mapped by new node id
                    new_id = list(res.keys())[0] if res else None
                    return {"id": new_id, "kind": "drive#file"}
                elif isinstance(res, list) and res:
                    # sometimes returns list of node dicts
                    new_id = list(res[0].keys())[0] if isinstance(res[0], dict) else None
                    return {"id": new_id, "kind": "drive#file"}
                return {"id": str(res), "kind": "drive#file"}

            result = await asyncio.to_thread(_mkdir)
            self._invalidate_cache(service)
            return result
        except Exception as e:
            logger.error(f"[MEGA] create_folder failed: {e}", exc_info=True)
            raise

    async def rename_file(self, service: Any, file_id: str, new_name: str) -> Any:
        try:
            files = await self._get_cached_files(service)
            def _rename():
                file_node = files.get(file_id)
                if file_node:
                    # mega.py expects a tuple (node_id, node_data)
                    node_tuple = (file_id, file_node)
                    service.rename(node_tuple, new_name)
            await asyncio.to_thread(_rename)
            self._invalidate_cache(service)
            return {"id": file_id, "name": new_name}
        except Exception as e:
            logger.error(f"[MEGA] rename_file failed: {e}", exc_info=True)
            raise

    async def delete_file(self, service: Any, file_id: str) -> bool:
        try:
            def _delete():
                service.destroy(file_id)
            await asyncio.to_thread(_delete)
            self._invalidate_cache(service)
            return True
        except Exception as e:
            logger.error(f"[MEGA] delete_file failed: {e}", exc_info=True)
            raise

    async def toggle_sharing(self, service: Any, file_id: str) -> None:
        try:
            await asyncio.to_thread(service.export, node_id=file_id)
            self._invalidate_cache(service)
        except Exception as e:
            logger.error(f"[MEGA] toggle_sharing failed: {e}", exc_info=True)
            raise

    async def get_file_link(self, service: Any, file_id: str) -> str:
        # mega.py's get_link() expects (node_id, node_data) tuple, not a plain dict.
        # Bypass service.export() and build the tuple directly from get_files().
        try:
            def _build_link():
                nodes = service.get_files()
                node = nodes.get(file_id)
                if node is None:
                    raise ValueError(f"Node {file_id} not found in Mega file tree")
                node_data = node[1] if isinstance(node, (list, tuple)) else node
                is_file = node_data.get("t") == 0
                # Wrap in tuple as get_link/get_folder_link expect
                node_tuple = (file_id, node_data)
                if is_file:
                    return service.get_link(node_tuple)
                else:
                    return service.get_folder_link(node_tuple)
            link = await asyncio.to_thread(_build_link)
            return link or ""
        except Exception as e:
            logger.error(f"[MEGA] get_file_link failed for {file_id}: {e}", exc_info=True)
            return ""

    async def restore_file(self, service: Any, file_id: str) -> Any:
        try:
            files = await self._get_cached_files(service)
            def _restore():
                node = files.get(file_id)
                if node:
                    service.move(node[0] if isinstance(node, tuple) else node, service.root_id)
            await asyncio.to_thread(_restore)
            self._invalidate_cache(service)
            return {"id": file_id, "name": "Restored"}
        except Exception as e:
            logger.error(f"[MEGA] restore_file failed: {e}", exc_info=True)
            raise

    async def empty_trash(self, service: Any) -> bool:
        try:
            await asyncio.to_thread(service.empty_trash)
            self._invalidate_cache(service)
            return True
        except Exception as e:
            logger.error(f"[MEGA] empty_trash failed: {e}", exc_info=True)
            return False

    async def get_item_size(
        self, service: Any, item_id: str, is_folder: bool = False
    ) -> int:
        # mega.py does not expose recursive folder size calculation.
        # Return -1 as a sentinel so the caller can display "N/A" instead of "0 B".
        if is_folder:
            return -1
        try:
            files = await self._get_cached_files(service)
            node = files.get(item_id)
            if node:
                node_data = node[1] if isinstance(node, tuple) else node
                return node_data.get("s", 0)
            return 0
        except Exception as e:
            logger.warning(f"[MEGA] get_item_size failed: {e}")
            return 0

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _handle_email_input(self, update: Any, user_state: Any) -> None:
        user_state.data["mega_email"] = update.message.text.strip()
        user_state.set_state(UserStateEnum.EXPECTING_MEGA_PASSWORD)
        await update.message.reply_text("🔒 Now enter your Mega.nz password:")

    async def _handle_password_input(self, update: Any, user_state: Any, telegram_id: int, credential_repo: Any, username: str) -> None:
        email = user_state.data.get("mega_email", "")
        password = update.message.text.strip()
        user_state.data["mega_password"] = password
        await update.message.reply_text("⏳ Authenticating with Mega.nz…")
        try:
            def _login():
                m = Mega()
                m.login(email, password)
                return m

            m = await asyncio.to_thread(_login)
            
            # Store in session cache
            self.sessions[telegram_id] = m

            if credential_repo:
                await credential_repo.upsert(str(telegram_id), username, email, password)
            
            user_state.reset()
            await update.message.reply_text(f"✅ Logged in to Mega.nz as <b>{email}</b>.", parse_mode="HTML")
            logger.info(f"[MEGA][AUTH] User {telegram_id} logged in to Mega (email redacted)")
        except mega.errors.RequestError as e:
            if e.code == -26:
                user_state.set_state(UserStateEnum.EXPECTING_MEGA_2FA)
                await update.message.reply_text("🔐 2FA is enabled. Please enter your 6-digit Mega.nz Authenticator code:")
            else:
                user_state.reset()
                logger.error(f"[MEGA][AUTH] Login failed for {telegram_id}: {e}", exc_info=True)
                await update.message.reply_text("❌ Login failed. Please verify your credentials and try again.")
        except Exception as e:
            user_state.reset()
            logger.error(f"[MEGA][AUTH] Login failed for {telegram_id}: {e}", exc_info=True)
            await update.message.reply_text("❌ Login failed. Please verify your credentials and try again.")

    async def _handle_2fa_input(self, update: Any, user_state: Any, telegram_id: int, credential_repo: Any, username: str) -> None:
        email = user_state.data.get("mega_email", "")
        password = user_state.data.get("mega_password", "")
        mfa_code = update.message.text.strip()
        user_state.reset()
        await update.message.reply_text("⏳ Verifying 2FA code…")
        try:
            def _login_with_2fa():
                from mega.crypto import base64_to_a32, prepare_key, str_to_a32, stringhash, a32_to_str, base64_url_encode
                import hashlib
                m = Mega()
                user_email = email.lower()
                get_user_salt_resp = m._api_request({'a': 'us0', 'user': user_email})
                user_salt = None
                try:
                    user_salt = base64_to_a32(get_user_salt_resp['s'])
                except KeyError:
                    password_aes = prepare_key(str_to_a32(password))
                    user_hash = stringhash(user_email, password_aes)
                else:
                    pbkdf2_key = hashlib.pbkdf2_hmac(hash_name='sha512',
                                                     password=password.encode(),
                                                     salt=a32_to_str(user_salt),
                                                     iterations=100000,
                                                     dklen=32)
                    password_aes = str_to_a32(pbkdf2_key[:16])
                    user_hash = base64_url_encode(pbkdf2_key[-16:])
                
                resp = m._api_request({'a': 'us', 'user': user_email, 'uh': user_hash, 'mfa': mfa_code})
                if isinstance(resp, int):
                    raise mega.errors.RequestError(resp)
                m._login_process(resp, password_aes)
                m._trash_folder_node_id = m.get_node_by_type(4)[0]
                return m

            m = await asyncio.to_thread(_login_with_2fa)
            
            self.sessions[telegram_id] = m
            if credential_repo:
                await credential_repo.upsert(str(telegram_id), username, email, password)
            
            await update.message.reply_text(f"✅ Logged in to Mega.nz as <b>{email}</b>.", parse_mode="HTML")
            logger.info(f"[MEGA][AUTH] User {telegram_id} logged in to Mega with 2FA")
        except Exception as e:
            logger.error(f"[MEGA][AUTH] 2FA Login failed for {telegram_id}: {e}", exc_info=True)
            await update.message.reply_text("❌ 2FA Login failed. Please verify your credentials and code, and try again.")


    async def _start_login_flow(self, update: Any, user_state: Any) -> None:
        msg = "📧 Enter your Mega.nz email address:"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg)
        else:
            await update.message.reply_text(msg)
        user_state.set_state(UserStateEnum.EXPECTING_MEGA_EMAIL)

    async def do_login(self, update: Any, ctx: Any) -> None:
        if ctx.user_data is None:
            ctx.user_data = {}
        if "state" not in ctx.user_data or not isinstance(ctx.user_data["state"], UserState):
            ctx.user_data["state"] = UserState(ctx)
        user_state: UserState = ctx.user_data["state"]

        credential_repo = ctx.bot_data.get("credential_repo")

        if update.callback_query and update.callback_query.from_user:
            telegram_id = update.callback_query.from_user.id
            username = update.callback_query.from_user.username or ""
        elif update.message and update.message.from_user:
            telegram_id = update.message.from_user.id
            username = update.message.from_user.username or ""
        else:
            return

        if credential_repo:
            try:
                existing = await credential_repo.get(str(telegram_id))
                if existing and existing.get("email_address"):
                    msg = f"✅ Already linked as {existing['email_address']}. Logout first to link a new account."
                    if update.callback_query:
                        await update.callback_query.edit_message_text(msg)
                    elif update.message:
                        await update.message.reply_text(msg)
                    return
            except Exception as e:
                logger.error(f"[MEGA][AUTH] Error checking existing creds for {telegram_id}: {e}", exc_info=True)

        if update.message and user_state.is_state(UserStateEnum.EXPECTING_MEGA_EMAIL):
            await self._handle_email_input(update, user_state)
            return

        if update.message and user_state.is_state(UserStateEnum.EXPECTING_MEGA_PASSWORD):
            await self._handle_password_input(update, user_state, telegram_id, credential_repo, username)
            return

        if update.message and user_state.is_state(UserStateEnum.EXPECTING_MEGA_2FA):
            await self._handle_2fa_input(update, user_state, telegram_id, credential_repo, username)
            return

        await self._start_login_flow(update, user_state)

    async def _process_logout_confirmation(self, q: Any, action: str, telegram_id: int, credential_repo: Any) -> None:
        if action == "yes":
            try:
                if telegram_id in self.sessions:
                    del self.sessions[telegram_id]
                
                if credential_repo:
                    await credential_repo.delete(str(telegram_id))
                await q.edit_message_text("✅ Successfully logged out of your Mega.nz account.")
                logger.info(f"[MEGA][AUTH] User {telegram_id} logged out of Mega")
            except Exception as e:
                logger.error(f"[MEGA][AUTH] Logout failed for {telegram_id}: {e}", exc_info=True)
                await q.edit_message_text("❌ Logout failed. Please try again.")
        else:
            await q.edit_message_text("Logout cancelled.")

    async def _show_logout_prompt(self, q: Any) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        await q.edit_message_text(
            "⚠️ Are you sure you want to logout of your Mega.nz account?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes", callback_data="confirm_logout:yes"),
                 InlineKeyboardButton("❌ No", callback_data="confirm_logout:no")]
            ])
        )

    async def do_logout(self, update: Any, ctx: Any) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        if ctx.user_data is None:
            ctx.user_data = {}

        if not (update.callback_query and update.callback_query.from_user):
            return

        q = update.callback_query
        telegram_id = q.from_user.id
        credential_repo = ctx.bot_data.get("credential_repo")

        if q.data and q.data.startswith("confirm_logout:"):
            await q.answer()
            action = q.data.split(":")[1]
            await self._process_logout_confirmation(q, action, telegram_id, credential_repo)
            return

        has_creds = False
        if credential_repo:
            try:
                existing = await credential_repo.get(str(telegram_id))
                has_creds = bool(existing and existing.get("email_address"))
            except Exception as e:
                logger.warning(f"[MEGA][AUTH] Error checking credentials during logout for {telegram_id}: {e}")

        if not has_creds:
            await q.edit_message_text(
                "ℹ️ No linked Mega account found.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="SETTINGS")]])
            )
            return

        await self._show_logout_prompt(q)
