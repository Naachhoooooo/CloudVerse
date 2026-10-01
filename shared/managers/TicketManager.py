import os
from shared.core.Logger import get_logger

from shared.database.repositories.SupportRepository import SupportRepository

logger = get_logger(__name__)

class TicketManager:
    """Manages business logic and data access for tickets and users."""
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.support_repo = SupportRepository(self.db_manager)

    async def authorize_user(self, telegram_id: int, username: str, name: str, source_system: str):
        await self.support_repo.authorize_user(telegram_id, username, name, source_system)

    async def is_authorized(self, telegram_id: int) -> bool:
        return await self.support_repo.is_authorized(telegram_id)

    async def get_user(self, telegram_id: int):
        return await self.support_repo.get_user(telegram_id)

    async def get_user_by_topic(self, topic_id: int):
        return await self.support_repo.get_user_by_topic(topic_id)

    async def get_active_ticket(self, telegram_id: int):
        return await self.support_repo.get_active_ticket(telegram_id)

    async def generate_next_ticket_code(self) -> str:
        return await self.support_repo.generate_next_ticket_code()

    async def create_ticket(self, ticket_code: str, telegram_id: int):
        await self.support_repo.create_ticket(ticket_code, telegram_id)

    async def set_user_topic(self, telegram_id: int, topic_id: int):
        await self.support_repo.set_user_topic(telegram_id, topic_id)

    async def mark_admin_replied(self, ticket_code: str):
        await self.support_repo.mark_admin_replied(ticket_code)

    async def close_ticket(self, ticket_code: str, status: str, note: str, admin_details: str):
        await self.support_repo.update_ticket_status(ticket_code, status, note, admin_details)

    async def purge_auth(self, telegram_id: int):
        await self.support_repo.purge_auth(telegram_id)
