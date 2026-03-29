"""Base classes for LLM providers with retry support."""

import asyncio
import logging
import random
from typing import Any, Callable, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

# HTTP status codes that are safe to retry
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 529})


def _extract_status_code(exception: Exception) -> int | None:
    """Extract HTTP status code from various exception types.
    
    Args:
        exception: The exception to extract status code from.
        
    Returns:
        The HTTP status code if found, None otherwise.
    """
    # OpenAI-style exceptions
    if hasattr(exception, "status_code"):
        return getattr(exception, "status_code")
    
    # HTTPX/Requests style
    if hasattr(exception, "response"):
        response = getattr(exception, "response")
        if hasattr(response, "status_code"):
            return getattr(response, "status_code")
    
    # Anthropic-style exceptions
    if hasattr(exception, "status"):
        status = getattr(exception, "status")
        if isinstance(status, int):
            return status
    
    return None


class RetryableProvider:
    """Base class for providers with retry support.
    
    This class provides a reusable retry mechanism for API calls that may
    fail due to transient errors like rate limiting or temporary server issues.
    
    Example:
        ```python
        class MyProvider(RetryableProvider):
            async def call_api(self, messages):
                return await self._execute_with_retry(
                    self._make_api_call,
                    messages
                )
        ```
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
    ):
        """Initialize the provider with retry configuration.
        
        Args:
            max_retries: Maximum number of retry attempts after initial failure.
            base_delay: Base delay in seconds for exponential backoff.
            max_delay: Maximum delay in seconds between retries.
        """
        self._max_retries = max_retries
        self._retry_base_delay = base_delay
        self._retry_max_delay = max_delay
    
    async def _execute_with_retry(
        self,
        operation: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute an operation with exponential backoff retry logic.
        
        This method will retry the operation if it fails with a retryable
        HTTP status code (429, 500, 502, 503, 529).
        
        Args:
            operation: The async function to execute.
            *args: Positional arguments to pass to the operation.
            **kwargs: Keyword arguments to pass to the operation.
            
        Returns:
            The result of the operation.
            
        Raises:
            The last exception encountered if all retries are exhausted.
        """
        last_exception: Exception | None = None
        
        for attempt in range(self._max_retries + 1):
            try:
                return await operation(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                # Don't retry if we've exhausted all attempts
                if attempt >= self._max_retries:
                    break
                
                # Check if this is a retryable error
                status_code = _extract_status_code(e)
                if status_code is None or status_code not in RETRYABLE_STATUS_CODES:
                    # Not a retryable error, fail immediately
                    break
                
                # Calculate delay with exponential backoff and jitter
                delay = min(
                    self._retry_base_delay * (2 ** attempt),
                    self._retry_max_delay,
                )
                # Add random jitter to avoid thundering herd
                delay += random.uniform(0, 1)
                
                logger.warning(
                    f"API call failed (attempt {attempt + 1}/{self._max_retries + 1}), "
                    f"status={status_code}, retrying in {delay:.2f}s..."
                )
                
                await asyncio.sleep(delay)
        
        # All retries exhausted or non-retryable error
        if last_exception is not None:
            raise last_exception
        raise RuntimeError("Unexpected: no exception but retries exhausted")
