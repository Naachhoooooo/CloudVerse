import time
import asyncio
from typing import Optional
from enum import Enum
from shared.core.Logger import get_logger

__all__ = ["CircuitBreaker"]
logger = get_logger(__name__)


class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker pattern implementation for external API calls."""
    
    def __init__(self, failure_threshold: int = 5, timeout: int = 60, success_threshold: int = 2):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.success_threshold = success_threshold
        
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = CircuitBreakerState.CLOSED
        # Protects state mutations from interleaved coroutine access
        self._lock = asyncio.Lock()
        
    async def can_execute(self) -> bool:
        """Check if the circuit breaker allows execution."""
        async with self._lock:
            current_time = time.time()
            
            if self.state == CircuitBreakerState.CLOSED:
                return True
            elif self.state == CircuitBreakerState.OPEN:
                if self.last_failure_time and (current_time - self.last_failure_time) >= self.timeout:
                    self.state = CircuitBreakerState.HALF_OPEN
                    self.success_count = 0
                    logger.info("Circuit breaker transitioning to HALF_OPEN state")
                    return True
                return False
            elif self.state == CircuitBreakerState.HALF_OPEN:
                return True
            
            return False
    
    async def record_success(self):
        """Record a successful operation."""
        async with self._lock:
            if self.state == CircuitBreakerState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    self.state = CircuitBreakerState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
                    logger.info("Circuit breaker transitioning to CLOSED state")
            elif self.state == CircuitBreakerState.CLOSED:
                self.failure_count = max(0, self.failure_count - 1)
    
    async def record_failure(self):
        """Record a failed operation."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitBreakerState.CLOSED:
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitBreakerState.OPEN
                    logger.warning(f"Circuit breaker transitioning to OPEN state after {self.failure_count} failures")
            elif self.state == CircuitBreakerState.HALF_OPEN:
                self.state = CircuitBreakerState.OPEN
                logger.warning("Circuit breaker transitioning back to OPEN state due to failure in HALF_OPEN")
    
    def get_state(self) -> str:
        """Get current circuit breaker state."""
        return self.state.value
    
    def get_stats(self) -> dict:
        """Get circuit breaker statistics."""
        return {
            'state': self.state.value,
            'failure_count': self.failure_count,
            'success_count': self.success_count,
            'last_failure_time': self.last_failure_time,
            'time_until_retry': max(0, self.timeout - (time.time() - (self.last_failure_time or 0))) if self.state == CircuitBreakerState.OPEN else 0
        }
    
    async def reset(self):
        """Reset circuit breaker to initial state."""
        async with self._lock:
            self.state = CircuitBreakerState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None
            logger.info("Circuit breaker manually reset to CLOSED state")
