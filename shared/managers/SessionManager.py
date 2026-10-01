import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, AuthKeyUnregisteredError, UnauthorizedError
from telegram import Update
from telegram.ext import ContextTypes

from shared.core.Logger import get_logger
from shared.database.repositories.SessionRepository import SessionRepository
from shared.managers.ServerManager import get_server_manager

logger = get_logger(__name__)

SESSION_COUNT = 3
# Default session names — overridden by initialize_session_manager(session_name_prefix=...)
SESSION_NAMES = [f"CloudVerse Bot v1.0 -{i}" for i in range(1, SESSION_COUNT + 1)]
SESSIONS_DIR = Path(__file__).parent.parent.parent / "data" / "sessions"

SESSIONS_DIR.mkdir(exist_ok=True)

class TelethonSessionManager:
    def __init__(self, db_path: str, cipher, group_chat_id: Optional[str] = None,
                 maintenance_topic_id: Optional[str] = None, super_admin_id: Optional[str] = None):
        # Always use the global server database for session management
        self._db_path = str(Path(__file__).parent.parent.parent / "data" / "databases" / "cloudverse_server.db")
        self._cipher = cipher
        self._group_chat_id = group_chat_id
        self._maintenance_topic_id = maintenance_topic_id
        self._super_admin_id = super_admin_id
        self.sessions = {}
        self.clients: Dict[int, TelegramClient] = {}
        self.session_rotation_index = 0

    async def initialize(self):
        try:
            await self._load_sessions()
        except Exception as e:
            logger.warning(f"Could not load sessions during initialization: {e}")
            self.sessions = {}
        await self._ensure_sessions()

    async def _load_sessions(self):
        repo = SessionRepository(self._db_path, self._cipher)
        all_sessions = await repo.get_all()
        self.sessions = {s['session_id']: s for s in all_sessions}

    async def _ensure_sessions(self):
        current_count = len(self.sessions)
        if current_count >= SESSION_COUNT:
            return
        default_created_by = self._super_admin_id.split(',')[0].strip() if self._super_admin_id else '0'
        repo = SessionRepository(self._db_path, self._cipher)
        
        for i in range(current_count, SESSION_COUNT):
            session_name = SESSION_NAMES[i]
            if any(s and s.get('session_name') == session_name for s in self.sessions.values()):
                continue
                
            try:
                new_id = await repo.create(
                    session_name=session_name, phone_number=None,
                    session_file_path=None, api_id=None, api_hash=None,
                    created_by=default_created_by, health_status='invalid'
                )
                path = str(SESSIONS_DIR / f"session_{new_id}.session")
                await repo.update(new_id, session_file_path=path)
                new_session = await repo.get(new_id)
                self.sessions[new_id] = new_session
            except ValueError as e:
                if "Maximum" in str(e):
                    break
            except Exception as e:
                logger.error(f"Error ensuring session {session_name}: {e}")

    def get_session_file_path(self, session_id: int) -> Optional[Path]:
        session = self.sessions.get(session_id)
        if session and session['session_file_path']:
            return Path(session['session_file_path'])
        return None

    async def set_session_api_credentials(self, session_id: int, api_id: str, api_hash: str) -> bool:
        try:
            repo = SessionRepository(self._db_path, self._cipher)
            await repo.update(session_id, api_id=api_id, api_hash=api_hash)
            self.sessions[session_id] = await repo.get(session_id)
            return True
        except Exception as e:
            logger.error(f"Error setting API credentials for session {session_id}: {e}")
            return False

    async def create_client(self, session_id: int) -> Optional[TelegramClient]:
        session = self.sessions.get(session_id)
        if not session:
            logger.error(f"Session {session_id} not found")
            return None
        api_id = session.get('api_id')
        api_hash = session.get('api_hash')
        if not api_id or not api_hash:
            logger.error(f"API credentials not configured for session {session_id}")
            return None
        try:
            session_file = self.get_session_file_path(session_id)
            if not session_file:
                return None
            client = TelegramClient(str(session_file), int(api_id), str(api_hash))
            return client
        except Exception as e:
            logger.error(f"Error creating client for session {session_id}: {e}")
            return None

    def select_optimal_session(self, available_sessions):
        """Select a session using round-robin distribution (no usage tracking)."""
        if not available_sessions:
            return None
        if len(available_sessions) == 1:
            return available_sessions[0]
            
        selected_session = available_sessions[self.session_rotation_index % len(available_sessions)]
        self.session_rotation_index += 1
        return selected_session

    async def authenticate_session(self, session_id: int, phone: str, code: str = None,
                                   password: str = None, phone_code_hash: str = None) -> Tuple[bool, str]:
        try:
            client = await self.create_client(session_id)
            if not client:
                return False, "Failed to create client"
            await client.connect()
            import asyncio as _asyncio
            
            # Robust connection logic
            connected = False
            for _ in range(3):
                try:
                    await client.connect()
                    if client.is_connected():
                        connected = True
                        break
                except Exception:
                    pass
                await _asyncio.sleep(1)
                
            if not connected:
                return False, "Client failed to connect to Telegram servers. Check network or API credentials."

            if not code:
                # Add retry loop for the send_code_request as Telethon MTProto may drop right after connect
                last_error = None
                for attempt in range(3):
                    try:
                        if not client.is_connected():
                            await client.connect()
                        sent = await client.send_code_request(phone)
                        await client.disconnect()
                        return True, sent.phone_code_hash
                    except Exception as e:
                        last_error = e
                        if "disconnected" in str(e).lower():
                            await _asyncio.sleep(2)
                            continue
                        break # Break on other errors (like FloodWait)
                
                await client.disconnect()
                await get_server_manager().send_error_notification(
                    error_message=f"Failed to send code for session {session_id}: {last_error}",
                    error_type="Session Auth",
                    severity="MEDIUM"
                )
                return False, f"Failed to send code: {str(last_error)}"
            else:
                last_error = None
                for attempt in range(3):
                    try:
                        if not client.is_connected():
                            await client.connect()
                        if password:
                            await client.sign_in(phone=phone, password=password)
                        else:
                            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
                        
                        await client.disconnect()
                        repo = SessionRepository(self._db_path, self._cipher)
                        await repo.update(session_id, phone_number=phone, health_status='active', 
                                           is_expired=0, last_validated=datetime.now().isoformat())
                        await repo.reset_error_count(session_id)
                        self.sessions[session_id] = await repo.get(session_id)
                        return True, "Session authenticated successfully"
                    except SessionPasswordNeededError as e:
                        await client.disconnect()
                        return False, "2FA Password required"
                    except Exception as e:
                        last_error = e
                        if "disconnected" in str(e).lower():
                            await _asyncio.sleep(2)
                            continue
                        break
                        
                await client.disconnect()
                await get_server_manager().send_error_notification(
                    error_message=f"Authentication failed for session {session_id}: {last_error}",
                    error_type="Session Auth",
                    severity="HIGH"
                )
                return False, f"Authentication failed: {str(last_error)}"
        except Exception as e:
            logger.error(f"Error authenticating session {session_id}: {e}")
            await get_server_manager().send_error_notification(
                error_message=f"Critical error authenticating session {session_id}: {e}",
                error_type="Session Auth",
                severity="CRITICAL"
            )
            return False, f"Authentication error: {str(e)}"

    async def check_session_validity(self, session_id: int) -> Tuple[bool, str]:
        try:
            session_file = self.get_session_file_path(session_id)
            if not session_file or not session_file.exists():
                return False, "Session file not found"
            client = await self.create_client(session_id)
            if not client:
                return False, "Failed to create client"
            await client.connect()
            try:
                me = await client.get_me()
                await client.disconnect()
                if me:
                    repo = SessionRepository(self._db_path, self._cipher)
                    await repo.update(session_id, health_status='active', 
                                       is_expired=0, last_validated=datetime.now().isoformat())
                    await repo.reset_error_count(session_id)
                    self.sessions[session_id] = await repo.get(session_id)
                    return True, "Session is valid"
                else:
                    return False, "Unable to get user info"
            except (AuthKeyUnregisteredError, UnauthorizedError) as e:
                await client.disconnect()
                repo = SessionRepository(self._db_path, self._cipher)
                error_count = await repo.increment_error_count(session_id)
                if error_count >= 3:
                    await repo.update_status(session_id, 'invalid')
                    from shared.managers.AlertManager import get_alert_manager
                    await get_alert_manager().send_error_notification(
                        f"Session {session_id} marked invalid after {error_count} consecutive Auth/Unauthorized errors.",
                        "Session Alert", "CRITICAL"
                    )
                self.sessions[session_id] = await repo.get(session_id)
                return False, f"Session expired/unauthorized: {str(e)}"
        except Exception as e:
            logger.error(f"Error checking session {session_id} validity: {e}")
            await get_server_manager().send_error_notification(
                error_message=f"Session validity check failed for session {session_id}: {e}",
                error_type="Session Monitor",
                severity="MEDIUM"
            )
            return False, f"Check failed: {str(e)}"

    async def logout_session(self, session_id: int) -> Tuple[bool, str]:
        try:
            client = await self.create_client(session_id)
            if client:
                await client.connect()
                try:
                    await client.log_out()
                except Exception as e:
                    logger.debug(f"Failed to logout client during cleanup: {e}")
                await client.disconnect()
            session_file = self.get_session_file_path(session_id)
            if session_file and session_file.exists():
                session_file.unlink()
            repo = SessionRepository(self._db_path, self._cipher)
            await repo.update(session_id, phone_number=None, health_status='invalid', 
                               is_expired=0, last_validated=None)
            self.sessions[session_id] = await repo.get(session_id)
            return True, "Session logged out successfully"
        except Exception as e:
            logger.error(f"Error logging out session {session_id}: {e}")
            return False, f"Logout failed: {str(e)}"

    def get_session_status(self, session_id: int) -> Dict:
        session = self.sessions.get(session_id)
        if not session:
            return {"error": "Session not found"}
        status = {
            "session_id": session_id,
            "name": session['session_name'],
            "phone": session['phone_number'],
            "authenticated": session['health_status'] == 'active',
            "is_expired": bool(session['is_expired']),
            "last_check": session['last_validated'].strftime("%Y-%m-%d %H:%M:%S") if hasattr(session['last_validated'], 'strftime') else str(session['last_validated']) if session['last_validated'] else "Never",
            "expiry_date": "Unknown",
            "session_file_exists": Path(session['session_file_path']).exists() if session['session_file_path'] else False,
            "api_id": session['api_id'],
            "api_hash": session['api_hash'],
            "has_api_credentials": bool(session['api_id'] and session['api_hash'])
        }
        return status

    def get_all_sessions_status(self) -> List[Dict]:
        return [self.get_session_status(sid) for sid in sorted(self.sessions.keys())]

    async def send_expiry_alert(self, context: ContextTypes.DEFAULT_TYPE, session_id: int,
                                reason: str, details: str = None):
        try:
            session = self.sessions.get(session_id)
            if not session:
                return
            full_reason = f"{reason}: {details}" if details else reason
            message = (
                f"⚠️ Session Expiry Alert\n\n"
                f"- Session Name: {session['session_name']}\n"
                f"- Session ID: {session_id}\n"
                f"- Phone: {session['phone_number'] or 'Unknown'}\n"
                f"- Expired Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"Reason: {full_reason}\n\n"
                f"🔑 Please re-authenticate this session to continue using Telethon features."
            )
            if self._group_chat_id and self._maintenance_topic_id:
                await context.bot.send_message(
                    chat_id=self._group_chat_id,
                    message_thread_id=int(self._maintenance_topic_id),
                    text=message,
                    parse_mode='HTML'
                )
            elif self._group_chat_id:
                await context.bot.send_message(
                    chat_id=self._group_chat_id,
                    text=message,
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"Error sending expiry alert: {e}")

    async def monitor_sessions(self, context: ContextTypes.DEFAULT_TYPE):
        while True:
            try:
                for session_id in list(self.sessions.keys()):
                    session = self.sessions[session_id]
                    if session and session.get('health_status') == 'active' and not session.get('is_expired'):
                        is_valid, message = await self.check_session_validity(session_id)
                        if not is_valid and "expired" in message.lower():
                            await self.send_expiry_alert(context, session_id, "Session validation failed", message)
                await asyncio.sleep(1800)  # Check every 30 minutes
            except Exception as e:
                logger.error(f"Error in session monitoring: {e}")
                await asyncio.sleep(1800)  # Retry after 30 minutes on error

_session_manager_instance = None


def initialize_session_manager(db_path: str, cipher, group_chat_id: Optional[str] = None,
                                maintenance_topic_id: Optional[str] = None,
                                super_admin_id: Optional[str] = None,
                                session_name_prefix: str = "CloudVerse Bot") -> "TelethonSessionManager":
    """Initialize the global SessionManager with bot-specific config. Call once at startup."""
    global _session_manager_instance, SESSION_NAMES
    # Allow each bot to brand its own Telethon sessions
    SESSION_NAMES = [f"{session_name_prefix} v1.0 -{i}" for i in range(1, SESSION_COUNT + 1)]
    _session_manager_instance = TelethonSessionManager(
        db_path=db_path, cipher=cipher,
        group_chat_id=group_chat_id,
        maintenance_topic_id=maintenance_topic_id,
        super_admin_id=super_admin_id,
    )
    return _session_manager_instance


def get_session_manager() -> TelethonSessionManager:
    """Return the already-initialized global SessionManager. Raises if not initialized."""
    if _session_manager_instance is None:
        raise RuntimeError(
            "SessionManager has not been initialized. "
            "Call initialize_session_manager(db_path, cipher, ...) at startup."
        )
    return _session_manager_instance


