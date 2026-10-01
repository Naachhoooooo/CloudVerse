import aiosqlite
from typing import List, Any, Dict, TypeVar, Tuple
from shared.core.Logger import get_logger
logger = get_logger(__name__)

T = TypeVar('T')

class BaseRepository:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        from shared.database.DatabaseConnectionManager import get_db_manager
        self.db_manager = get_db_manager(self.db_path)

    async def execute_query(self, query: str, params: Tuple = (), fetch_one: bool = False, fetch_all: bool = False) -> Any:
        try:
            # The async query method handles row factory natively via get_async_connection wrapper
            return await self.db_manager.execute_async_query(
                query=query, 
                params=params, 
                fetch_one=fetch_one, 
                fetch_all=fetch_all
            )
        except Exception as e:
            logger.error(f"Database error executing query: {query}. Error: {e}", exc_info=True)
            raise

    async def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        try:
            return await self.db_manager.execute_async_batch(query, params_list)
        except Exception as e:
            logger.error(f"Database error executing batch query: {query}. Error: {e}", exc_info=True)
            raise

    async def execute(self, query: str, params: Tuple = ()) -> Any:
        return await self.execute_query(query, params)

    async def fetch_one(self, query: str, params: Tuple = ()) -> Any:
        row = await self.execute_query(query, params, fetch_one=True)
        return dict(row) if row else None

    async def fetch_all(self, query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        rows = await self.execute_query(query, params, fetch_all=True)
        return [dict(row) for row in rows] if rows else []
