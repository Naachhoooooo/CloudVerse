import asyncio
from typing import Any, Coroutine, Set

from shared.core.Logger import get_logger

logger = get_logger(__name__)

# Maintain strong references to background tasks to prevent them from being garbage collected
# before they complete. This is a common pitfall in asyncio.
_background_tasks: Set[asyncio.Task] = set()

def track_task(coro: Coroutine[Any, Any, Any], name: str = None) -> asyncio.Task:
    """
    Schedules an asyncio coroutine to run in the background and tracks it to prevent
    garbage collection. Also adds a callback to log any unhandled exceptions.
    
    Args:
        coro: The coroutine to run.
        name: An optional name for the task (useful for debugging).
        
    Returns:
        The created asyncio.Task.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    
    # Callback to remove the task from the set when it's done and log errors
    def _on_completion(t: asyncio.Task):
        _background_tasks.discard(t)
        try:
            # We don't need the result, but checking it raises any unhandled exceptions
            t.result()
        except asyncio.CancelledError:
            logger.debug(f"Task '{t.get_name()}' was cancelled.")
        except Exception as e:
            logger.error(f"Task '{t.get_name()}' failed with an unhandled exception: {e}", exc_info=True)
            
    task.add_done_callback(_on_completion)
    return task
