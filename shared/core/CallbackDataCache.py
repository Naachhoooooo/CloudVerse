import os
import json
import hashlib
from shared.core.Logger import get_logger

logger = get_logger(__name__)

class CallbackDataCache:
    _instance = None
    
    def __init__(self, cache_file: str = None):
        if not cache_file:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.cache_file = os.path.join(base_dir, "cache", "id_cache.json")
        else:
            self.cache_file = cache_file
            
        self.cache = {}
        self._is_dirty = False
        self._save_task = None
        self._load_cache()

    import asyncio
    
    async def _auto_save_loop(self):
        import asyncio
        while True:
            try:
                await asyncio.sleep(10)
                if self._is_dirty:
                    self._save_cache()
                    self._is_dirty = False
            except asyncio.CancelledError:
                if self._is_dirty:
                    self._save_cache()
                break
            except Exception as e:
                logger.error(f"[SYSTEM] Auto-save loop error: {e}")
                await asyncio.sleep(10)

    def start_auto_save(self):
        import asyncio
        if self._save_task is None:
            self._save_task = asyncio.create_task(self._auto_save_loop())

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception as e:
                logger.error(f"[SYSTEM] Failed to load id_cache.json: {e}")
                self.cache = {}

    def _save_cache(self):
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f)
        except Exception as e:
            logger.error(f"[SYSTEM] Failed to save id_cache.json: {e}")

    def shorten_id(self, item_id: str) -> str:
        if not isinstance(item_id, str):
            return str(item_id) if item_id else ""
        if len(item_id) < 32:
            return item_id
            
        short_hash = hashlib.md5(item_id.encode('utf-8'), usedforsecurity=False).hexdigest()[:10]
        if short_hash not in self.cache:
            self.cache[short_hash] = item_id
            self._is_dirty = True
        return short_hash

    def resolve_id(self, short_id: str) -> str:
        if not isinstance(short_id, str):
            return short_id
        return self.cache.get(short_id, short_id)

_cache_instance = CallbackDataCache()

def shorten_id(ctx, item_id: str) -> str:
    """Shorten long IDs. ctx is ignored but kept for backwards compatibility."""
    return _cache_instance.shorten_id(item_id)

def resolve_id(ctx, short_id: str) -> str:
    """Resolve a shortened ID. ctx is ignored."""
    return _cache_instance.resolve_id(short_id)

def start_cache_auto_save():
    """Starts the background I/O debouncing loop."""
    _cache_instance.start_auto_save()
