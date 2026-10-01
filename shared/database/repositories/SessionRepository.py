"""
Shared SessionRepository — manages cloudverse_sessions in shared.db.
AES-128 encryption/decryption applied to api_hash on every read/write.
"""
from typing import Optional, Dict, Any, List
from shared.database.repositories.BaseRepository import BaseRepository
from shared.core.Logger import get_logger
logger = get_logger(__name__)


class SessionRepository(BaseRepository):
    def __init__(self, db_path: str, cipher):
        from pathlib import Path
        server_db_path = str(Path(__file__).parent.parent.parent.parent / "data" / "databases" / "cloudverse_server.db")
        super().__init__(server_db_path)
        self._cipher = cipher
        self.table_name = "cloudverse_sessions"

    def _encrypt(self, value: str) -> str:
        return self._cipher.encrypt(value.encode()).decode()

    def _decrypt(self, blob: str) -> str:
        return self._cipher.decrypt(blob.encode()).decode()

    def _decrypt_session(self, row: Dict) -> Dict:
        if row and row.get('api_hash'):
            row['api_hash'] = self._decrypt(row['api_hash'])
        return row

    async def create(self, session_name: str, phone_number: str, session_file_path: str,
                     api_id: str, api_hash: str, created_by: str,
                     health_status: str = 'active') -> int:
        """Insert a new session (encrypted api_hash). Max 3 per DB enforced."""
        logger.info(f"[AUTH] Creating session {session_name}")
        count_row = await self.fetch_one(
            "SELECT COUNT(*) as count FROM cloudverse_sessions")
        if count_row and count_row.get('count', 0) >= 3:
            raise ValueError("Maximum of 3 sessions allowed")
        encrypted_hash = self._encrypt(api_hash) if api_hash else None
        query = """
            INSERT INTO cloudverse_sessions
                (session_name, phone_number, health_status, session_file_path,
                 api_id, api_hash, created_by, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        result = await self.execute(
            query, (session_name, phone_number, health_status,
                    session_file_path, api_id, encrypted_hash, str(created_by)))
        row_id = result if isinstance(result, int) else getattr(result, 'lastrowid', 0)
        logger.info(f"[AUTH] Session created: id={row_id}")
        return row_id

    async def get(self, session_id: int) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_sessions WHERE session_id = ?"
        row = await self.fetch_one(query, (session_id,))
        result = row
        return self._decrypt_session(result) if result else None

    async def get_active(self) -> Optional[Dict[str, Any]]:
        """Return the least-used active session with valid credentials."""
        query = """
            SELECT * FROM cloudverse_sessions
            WHERE health_status = 'active' AND api_id IS NOT NULL AND api_hash IS NOT NULL
            ORDER BY last_used ASC LIMIT 1
        """
        row = await self.fetch_one(query)
        result = row
        return self._decrypt_session(result) if result else None
    
    async def get_best_available(self) -> List[Dict[str, Any]]:
        """Get the best available sessions for downloads based on load balancing"""
        query = """
            SELECT * FROM cloudverse_sessions 
            WHERE health_status = 'active' 
            AND api_id IS NOT NULL 
            AND api_hash IS NOT NULL 
            ORDER BY last_used ASC
        """
        rows = await self.fetch_all(query)
        return [self._decrypt_session(r) for r in rows]

    async def get_all(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_sessions ORDER BY session_id"
        rows = await self.fetch_all(query)
        return [self._decrypt_session(r) for r in rows]

    async def get_all_active(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_sessions WHERE health_status = 'active' ORDER BY last_used ASC"
        rows = await self.fetch_all(query)
        return [self._decrypt_session(r) for r in rows]

    async def get_by_phone(self, phone_number: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM cloudverse_sessions WHERE phone_number = ?"
        row = await self.fetch_one(query, (phone_number,))
        result = row
        return self._decrypt_session(result) if result else None

    async def update(self, session_id: int, **kwargs) -> bool:
        """Update arbitrary fields of a session."""
        if not kwargs:
            return True
            
        update_fields = []
        update_values = []
        for key, value in kwargs.items():
            if key == 'api_hash' and value is not None:
                value = self._encrypt(value)
            update_fields.append(f"{key} = ?")
            update_values.append(value)
            
        update_fields.append("last_updated = CURRENT_TIMESTAMP")
        update_values.append(session_id)
        
        query = f"UPDATE cloudverse_sessions SET {', '.join(update_fields)} WHERE session_id = ?"  # nosec B608
        await self.execute(query, tuple(update_values))
        logger.info(f"[AUTH] Successfully updated session {session_id}")
        return True

    async def update_status(self, session_id: int, health_status: str) -> bool:
        is_expired = 1 if health_status == 'expired' else 0
        query = """
            UPDATE cloudverse_sessions
            SET health_status = ?, is_expired = ?, last_updated = CURRENT_TIMESTAMP
            WHERE session_id = ?
        """
        await self.execute(query, (health_status, is_expired, session_id))
        logger.info(f"[AUTH] Session {session_id} status → {health_status}")
        return True

    async def update_usage(self, session_id: int) -> bool:
        """Update last_used timestamp."""
        query = """
            UPDATE cloudverse_sessions
            SET last_used = CURRENT_TIMESTAMP,
                last_updated = CURRENT_TIMESTAMP
            WHERE session_id = ?
        """
        await self.execute(query, (session_id,))
        return True

    async def update_validated(self, session_id: int) -> bool:
        query = """
            UPDATE cloudverse_sessions
            SET last_validated = CURRENT_TIMESTAMP, last_updated = CURRENT_TIMESTAMP
            WHERE session_id = ?
        """
        await self.execute(query, (session_id,))
        return True

    async def delete(self, session_id: int) -> bool:
        query = "DELETE FROM cloudverse_sessions WHERE session_id = ?"
        await self.execute(query, (session_id,))
        logger.info(f"[AUTH] Deleted session {session_id}")
        return True

    async def cleanup_expired(self) -> bool:
        """Delete all sessions with health_status='expired'."""
        query = "DELETE FROM cloudverse_sessions WHERE health_status = 'expired'"
        await self.execute(query)
        logger.info("[AUTH] Cleaned up expired sessions")
        return True

    async def is_expired(self, session_id: int) -> bool:
        query = "SELECT is_expired FROM cloudverse_sessions WHERE session_id = ?"
        row = await self.fetch_one(query, (session_id,))
        return bool(row.get('is_expired')) if row else False

    async def increment_error_count(self, session_id: int) -> int:
        """Increment error count and return the new count."""
        query = """
            UPDATE cloudverse_sessions
            SET error_count = error_count + 1, last_updated = CURRENT_TIMESTAMP
            WHERE session_id = ?
            RETURNING error_count
        """
        row = await self.fetch_one(query, (session_id,))
        if row:
            new_count = row.get('error_count', 0)
            logger.warning(f"[AUTH] Session {session_id} error count increased to {new_count}")
            return new_count
        return 0

    async def reset_error_count(self, session_id: int) -> bool:
        """Reset error count to 0 upon successful validation/use."""
        query = """
            UPDATE cloudverse_sessions
            SET error_count = 0, last_updated = CURRENT_TIMESTAMP
            WHERE session_id = ?
        """
        await self.execute(query, (session_id,))
        return True
