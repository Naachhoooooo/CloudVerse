import time
from threading import Lock
from collections import OrderedDict

class TTLCache:
    def __init__(self, default_ttl=60, maxsize=1000):
        self.cache = OrderedDict()
        self.default_ttl = default_ttl
        self.maxsize = maxsize
        self.lock = Lock()

    def get(self, key):
        with self.lock:
            if key in self.cache:
                value, expires_at = self.cache[key]
                if time.time() < expires_at:
                    self.cache.move_to_end(key)
                    return value
                else:
                    del self.cache[key]
            return None

    def set(self, key, value, ttl=None):
        if ttl is None:
            ttl = self.default_ttl
        with self.lock:
            self.cache[key] = (value, time.time() + ttl)
            self.cache.move_to_end(key)
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

    def delete(self, key):
        with self.lock:
            if key in self.cache:
                del self.cache[key]

    def clear(self):
        with self.lock:
            self.cache.clear()

# Global instances for different cache domains
fm_cache = TTLCache(default_ttl=60, maxsize=1000)

def invalidate_folder_cache(folder_id: str):
    """Iterates through fm_cache and deletes any key ending with _{folder_id}"""
    with fm_cache.lock:
        keys_to_delete = [k for k in fm_cache.cache.keys() if k.endswith(f"_{folder_id}")]
        for k in keys_to_delete:
            del fm_cache.cache[k]

from functools import wraps
import asyncio

def async_lru_cache(maxsize=500):
    """
    LRU cache decorator designed specifically for async methods.
    Assumes the first argument is `self` and uses the second argument as the key.
    If no second argument, uses args/kwargs stringification as the key.
    """
    def decorator(func):
        cache = OrderedDict()
        lock = Lock()

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Try to grab the first non-self argument (e.g., telegram_id)
            if len(args) > 1:
                key = str(args[1])
            else:
                key = str(args) + str(kwargs)
                
            with lock:
                if key in cache:
                    cache.move_to_end(key)
                    return cache[key]

            # Not in cache, await the function
            result = await func(*args, **kwargs)
            
            with lock:
                # Add to cache and enforce maxsize
                cache[key] = result
                if len(cache) > maxsize:
                    cache.popitem(last=False)
            return result

        # Provide a cache deletion function
        def cache_delete(k):
            with lock:
                cache.pop(str(k), None)
                
        def cache_clear():
            with lock:
                cache.clear()
                
        wrapper.cache_delete = cache_delete
        wrapper.cache_clear = cache_clear
        return wrapper
    return decorator
