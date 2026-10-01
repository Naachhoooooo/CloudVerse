"""
Adaptive Chunk Sizing Logic for Uploads and Downloads.
Moved from Utilities.py.
"""
from shared.core.Logger import get_logger

logger = get_logger(__name__)

def get_adaptive_chunk_size(last_chunk_time, current_chunk_size, logger_instance=None, context=None):
    min_chunk = 64 * 1024      # 64KB
    max_chunk = 8 * 1024 * 1024 # 8MB
    reason = 'unchanged'
    new_chunk = current_chunk_size
  
    if last_chunk_time < 0.5:
        new_chunk = min(current_chunk_size * 2, max_chunk)
        reason = 'fast'
    elif last_chunk_time > 2.0:
        new_chunk = max(current_chunk_size // 2, min_chunk)
        reason = 'slow'
  
    try:
        from shared.managers.MemoryManager import get_memory_pressure_level
        pressure_level = get_memory_pressure_level()
      
        if pressure_level.value == 'emergency':
            new_chunk = max(min_chunk, new_chunk // 4)
            reason = f'{reason}_emergency_memory'
        elif pressure_level.value == 'critical':
            new_chunk = max(min_chunk, new_chunk // 2)
            reason = f'{reason}_critical_memory'
        elif pressure_level.value == 'warning':
            new_chunk = max(min_chunk, int(new_chunk * 0.75))
            reason = f'{reason}_warning_memory'
    except ImportError:
        pass
  
    if logger_instance is not None and new_chunk != current_chunk_size:
        logger_instance.info(f"[ChunkSize] {context or ''} Chunk size changed from {current_chunk_size} to {new_chunk} due to {reason} chunk (time: {last_chunk_time:.2f}s)")
  
    return new_chunk

def get_upload_adaptive_chunk_size(telegram_id: int, file_size: int, chunk_size_cache: dict, 
                                  min_chunk_size: int = 1 * 1024 * 1024, 
                                  max_chunk_size: int = 32 * 1024 * 1024) -> int:
    """Get adaptive chunk size based on user history and file size."""
    # Check cache first
    if telegram_id in chunk_size_cache:
        cached_size = chunk_size_cache[telegram_id]
        # Adjust based on file size
        if file_size < 10 * 1024 * 1024:  # < 10MB
            return min(cached_size, 2 * 1024 * 1024)  # Max 2MB
        elif file_size < 100 * 1024 * 1024:  # < 100MB
            return min(cached_size, 8 * 1024 * 1024)  # Max 8MB
        else:
            return cached_size
    
    # Default adaptive sizing based on file size
    if file_size < 10 * 1024 * 1024:  # < 10MB
        chunk_size = min_chunk_size
    elif file_size > 10 * 1024 * 1024 * 1024:
        chunk_size = max_chunk_size  # Up to 32MB
    elif file_size < 100 * 1024 * 1024:  # < 100MB
        chunk_size = 4 * 1024 * 1024  # 4MB
    elif file_size < 500 * 1024 * 1024:  # < 500MB
        chunk_size = 8 * 1024 * 1024  # 8MB
    else:
        chunk_size = max_chunk_size  # 32MB
    
    chunk_size_cache[telegram_id] = chunk_size
    return chunk_size


def adjust_upload_chunk_size(telegram_id: int, current_chunk_size: int, 
                           last_chunk_duration_seconds: float, chunk_size_cache: dict,
                           min_chunk_size: int = 1 * 1024 * 1024,
                           max_chunk_size: int = 32 * 1024 * 1024):
    """Adjust chunk size dynamically based on the network velocity of the last chunk."""
    if last_chunk_duration_seconds <= 0:
        return
        
    # If the chunk transferred very quickly (< 1.0s), increase the chunk size to maximize throughput
    if last_chunk_duration_seconds < 1.0 and current_chunk_size < max_chunk_size:
        new_chunk_size = min(current_chunk_size * 1.5, max_chunk_size)
        chunk_size_cache[telegram_id] = int(new_chunk_size)
        
    # If the chunk took a long time (> 5.0s), decrease the chunk size to prevent TCP drops/timeouts
    elif last_chunk_duration_seconds > 5.0 and current_chunk_size > min_chunk_size:
        new_chunk_size = max(current_chunk_size * 0.5, min_chunk_size)
        chunk_size_cache[telegram_id] = int(new_chunk_size)
