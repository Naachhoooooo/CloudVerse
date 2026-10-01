import asyncio
import aiosqlite
import threading
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from shared.core.Logger import get_logger
from shared.database.QueryCache import QueryCache

logger = get_logger(__name__)

__all__ = ["DatabaseConnectionManager", "get_db_manager"]


class DatabaseConnectionManager:
    """Centralized asynchronous database connection manager with pooling and optimization."""
    
    def __init__(self, db_path: str, max_connections: int = 10, timeout: int = 30):
        self.db_path = db_path
        self.max_connections = max_connections
        self.timeout = timeout
        
        self._stats = {
            'total_queries': 0,
            'connection_reuses': 0
        }
        self.cache = QueryCache(max_size=500, ttl=300)
    
    @asynccontextmanager
    async def get_async_connection(self):
        """Get a long-lived async connection from the pool — safe for concurrent coroutines."""
        if not hasattr(self, '_async_pool_lock'):
            self._async_pool_lock = asyncio.Lock()
            
        async with self._async_pool_lock:
            if not getattr(self, '_async_pool', None):
                self._async_pool = asyncio.Queue(maxsize=self.max_connections)
                for _ in range(self.max_connections):
                    conn = await aiosqlite.connect(
                        self.db_path,
                        timeout=self.timeout
                    )
                    await conn.execute("PRAGMA journal_mode=WAL")
                    await conn.execute("PRAGMA synchronous=NORMAL")
                    await conn.execute("PRAGMA cache_size=10000")
                    await conn.execute("PRAGMA temp_store=MEMORY")
                    conn.row_factory = aiosqlite.Row
                    self._async_pool.put_nowait(conn)

        conn = await self._async_pool.get()
        try:
            self._stats['connection_reuses'] += 1
            yield conn
        except Exception as e:
            logger.error(f"Async database connection error: {e}")
            raise
        finally:
            self._async_pool.put_nowait(conn)
    
    async def execute_async_query(self, query: str, params: tuple = (), fetch_one: bool = False,
                                 fetch_all: bool = False, cache_key: Optional[str] = None) -> Any:
        """Execute an asynchronous query with optional caching."""
        self._stats['total_queries'] += 1
        
        # Check cache first for SELECT queries
        if cache_key and query.strip().upper().startswith('SELECT'):
            cached_result = self.cache.get(cache_key)
            if cached_result is not None:
                return cached_result
        
        async with self.get_async_connection() as conn:
            try:
                cursor = await conn.execute(query, params)
                
                if fetch_one:
                    result = await cursor.fetchone()
                elif fetch_all:
                    result = await cursor.fetchall()
                else:
                    await conn.commit()
                    # Return lastrowid for INSERT/REPLACE (repositories need it for row ID tracking)
                    normalized = query.strip().upper()
                    if normalized.startswith('INSERT') or normalized.startswith('REPLACE'):
                        result = cursor.lastrowid
                    else:
                        result = cursor.rowcount
                
                # Cache SELECT results
                if cache_key and query.strip().upper().startswith('SELECT'):
                    self.cache.set(cache_key, result)
                
                return result
            except Exception:
                await conn.rollback()
                raise
    
    async def execute_async_batch(self, query: str, params_list: List[tuple]) -> int:
        """Execute async batch operations for better performance."""
        async with self.get_async_connection() as conn:
            try:
                cursor = await conn.executemany(query, params_list)
                await conn.commit()
                return cursor.rowcount
            except Exception:
                await conn.rollback()
                raise

    @asynccontextmanager
    async def async_transaction(self):
        """Async transaction context manager for atomic multi-statement writes."""
        async with self.get_async_connection() as conn:
            try:
                await conn.execute("BEGIN")
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
    
    def invalidate_cache(self, pattern: Optional[str] = None):
        """Invalidate cache entries, optionally by pattern."""
        self.cache.invalidate(pattern)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection manager statistics."""
        stats = {
            'total_queries': self._stats['total_queries'],
            'connection_reuses': self._stats['connection_reuses'],
        }
        stats.update(self.cache.get_stats())
        
        if getattr(self, '_async_pool', None):
            stats['pool_size'] = self._async_pool.qsize()
        else:
            stats['pool_size'] = 0
            
        return stats

    async def close_all_async_connections(self):
        """Close all async database connections (must be called from an event loop)."""
        if getattr(self, '_async_pool', None):
            closed = 0
            while not self._async_pool.empty():
                try:
                    conn = self._async_pool.get_nowait()
                    await conn.close()
                    closed += 1
                except Exception:
                    pass
            logger.info(f"Closed {closed} async database connections")


# Per-path registry — each db_path gets exactly one manager instance.
# Bots call get_db_manager(DB_PATH) from their own config so each gets isolation.
_db_managers: dict = {}
_db_manager_lock = threading.Lock()


def get_db_manager(db_path: str) -> "DatabaseConnectionManager":
    """
    Return the DatabaseConnectionManager for the given db_path.

    Each unique db_path gets exactly one manager instance (per-path singleton).
    Call once at startup with the bot's own DB_PATH; subsequent calls with the
    same path return the cached instance (zero overhead).

    Args:
        db_path: Absolute path to the SQLite database file.
    """
    db_path = str(db_path)
    if db_path not in _db_managers:
        with _db_manager_lock:
            # Double-checked locking
            if db_path not in _db_managers:
                _db_managers[db_path] = DatabaseConnectionManager(db_path)
    return _db_managers[db_path]
