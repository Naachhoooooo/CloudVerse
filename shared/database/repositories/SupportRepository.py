import time
from typing import Optional, Dict

class SupportRepository:
    def __init__(self, db):
        self.db = db

    # ==========================
    # Ticket Methods
    # ==========================

    def _int_to_ticket_code(self, seq_id: int) -> str:
        if seq_id < 1:
            seq_id = 1
        letters_idx = (seq_id - 1) // 99999
        num = (seq_id - 1) % 99999 + 1

        l1 = chr(ord('A') + (letters_idx // 26))
        l2 = chr(ord('A') + (letters_idx % 26))

        if ord(l1) > ord('Z'):
            return f"CVXX{int(time.time() % 99999):05d}"

        return f"CV{l1}{l2}{num:05d}"

    async def generate_next_ticket_code(self) -> str:
        async with self.db.get_async_connection() as conn:
            cursor = await conn.execute("INSERT INTO support_tickets (telegram_id) VALUES (NULL)")
            seq_id = cursor.lastrowid
            ticket_code = self._int_to_ticket_code(seq_id)
            await conn.execute("UPDATE support_tickets SET ticket_code = ? WHERE rowid = ?", (ticket_code, seq_id))
            await conn.commit()
            return ticket_code

    async def get_active_ticket(self, telegram_id: int) -> Optional[Dict]:
        row = await self.db.execute_async_query(
            "SELECT * FROM support_tickets WHERE telegram_id = ? AND status = 'OPEN'",
            (str(telegram_id),),
            fetch_one=True
        )
        return dict(row) if row else None

    async def get_ticket_by_code(self, ticket_code: str) -> Optional[Dict]:
        row = await self.db.execute_async_query(
            "SELECT * FROM support_tickets WHERE ticket_code = ?",
            (ticket_code,),
            fetch_one=True
        )
        return dict(row) if row else None

    async def create_ticket(self, ticket_code: str, telegram_id: int):
        await self.db.execute_async_query(
            """INSERT OR REPLACE INTO support_tickets (ticket_code, telegram_id, status)
               VALUES (?, ?, 'OPEN')""",
            (ticket_code, str(telegram_id))
        )

    async def update_ticket_status(self, ticket_code: str, status: str, note: str = None, admin_details: str = None):
        await self.db.execute_async_query(
            """UPDATE support_tickets
               SET status = ?, note = ?, admin_details = ?, updated_at = CURRENT_TIMESTAMP
               WHERE ticket_code = ?""",
            (status, note, admin_details, ticket_code)
        )

    async def mark_admin_replied(self, ticket_code: str):
        await self.db.execute_async_query(
            "UPDATE support_tickets SET admin_replied = 1, updated_at = CURRENT_TIMESTAMP WHERE ticket_code = ?",
            (ticket_code,)
        )

    # ==========================
    # User Methods
    # ==========================

    async def authorize_user(self, telegram_id: int, username: str, name: str, source_system: str):
        # We no longer store username/name/source_system in support_users, only telegram_id.
        await self.db.execute_async_query(
            """INSERT INTO support_users (telegram_id, joined_at, last_active)
               VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
               ON CONFLICT(telegram_id) DO UPDATE SET
                  last_active=CURRENT_TIMESTAMP""",
            (str(telegram_id),)
        )

    async def purge_auth(self, telegram_id: int):
        # Removes the user from support_users
        await self.db.execute_async_query(
            "DELETE FROM support_users WHERE telegram_id = ?",
            (str(telegram_id),)
        )

    async def is_authorized(self, telegram_id: int) -> bool:
        row = await self.db.execute_async_query(
            "SELECT is_banned FROM support_users WHERE telegram_id = ?",
            (str(telegram_id),),
            fetch_one=True
        )
        if row:
            return not bool(row['is_banned'])
        return False

    async def get_user(self, telegram_id: int) -> Optional[Dict]:
        row = await self.db.execute_async_query(
            "SELECT * FROM support_users WHERE telegram_id = ?",
            (str(telegram_id),),
            fetch_one=True
        )
        return dict(row) if row else None

    async def set_user_topic(self, telegram_id: int, topic_id: int):
        # Also update the active ticket with the topic_id so it can be queried by get_user_by_topic
        await self.db.execute_async_query(
            "UPDATE support_tickets SET topic_id = ?, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = ? AND status = 'OPEN'",
            (topic_id, str(telegram_id))
        )

    async def get_user_by_topic(self, topic_id: int) -> Optional[Dict]:
        # Fetch the active ticket for this topic to find the telegram_id
        row = await self.db.execute_async_query(
            "SELECT telegram_id FROM support_tickets WHERE topic_id = ? AND status = 'OPEN' ORDER BY updated_at DESC LIMIT 1",
            (topic_id,),
            fetch_one=True
        )
        if row and row['telegram_id']:
            return await self.get_user(int(row['telegram_id']))
        return None
