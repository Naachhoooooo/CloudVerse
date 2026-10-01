import time
import threading
from typing import Optional, Any, Dict, Tuple

class QueryCache:
    """Lightweight LRU query cache to handle timestamp-based eviction and size limits."""
    
    def __init__(self, max_size: int = 500, ttl: int = 300):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                cached_data, timestamp = self._cache[key]
                if time.time() - timestamp < self.ttl:
                    self.hits += 1
                    return cached_data
                else:
                    del self._cache[key]
            self.misses += 1
            return None

    def set(self, key: str, value: Any):
        with self._lock:
            self._cache[key] = (value, time.time())
            if len(self._cache) > self.max_size:
                self._evict()

    def invalidate(self, pattern: Optional[str] = None):
        with self._lock:
            if pattern:
                keys_to_remove = [k for k in self._cache.keys() if pattern in k]
                for k in keys_to_remove:
                    del self._cache[k]
            else:
                self._cache.clear()

    def _evict(self):
        current_time = time.time()
        # First pass: remove expired entries
        expired_keys = [
            k for k, (_, timestamp) in self._cache.items()
            if current_time - timestamp >= self.ttl
        ]
        for k in expired_keys:
            del self._cache[k]
        
        # If still over max_size, evict the oldest entries (LRU-ish based on insertion time)
        if len(self._cache) > self.max_size:
            sorted_keys = sorted(self._cache.keys(), key=lambda k: self._cache[k][1])
            evict_count = len(self._cache) - (self.max_size // 2)
            for k in sorted_keys[:evict_count]:
                del self._cache[k]

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total_requests = self.hits + self.misses
            hit_rate = (self.hits / max(1, total_requests)) * 100
            return {
                'cache_hits': self.hits,
                'cache_misses': self.misses,
                'cache_size': len(self._cache),
                'cache_hit_rate': f"{hit_rate:.2f}%"
            }
