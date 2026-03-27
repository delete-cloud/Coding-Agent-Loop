"""Retry utilities with exponential backoff."""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import random
from typing import Any, AsyncIterator, Callable, Coroutine, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP status codes that should trigger retry
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 529})


class RetryableError(Exception):
    """Error that can be retried."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _extract_status_code(exception: Exception) -> int | None:
    """Extract HTTP status code from various exception types.

    Handles different SDK exception structures:
    - OpenAI SDK: exception.status_code
    - Anthropic SDK: exception.status
    - httpx: exception.response.status_code

    Args:
        exception: The exception to extract status code from.

    Returns:
        The HTTP status code if found, None otherwise.
    """
    # Check for status_code attribute (OpenAI SDK, httpx)
    if hasattr(exception, "status_code"):
        return getattr(exception, "status_code")

    # Check for response.status_code (some SDKs)
    if hasattr(exception, "response"):
        response = getattr(exception, "response")
        if hasattr(response, "status_code"):
            return getattr(response, "status_code")

    # Check for status attribute (anthropic SDK)
    if hasattr(exception, "status"):
        return getattr(exception, "status")

    return None


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_statuses: set[int] | frozenset[int] = RETRYABLE_STATUS_CODES,
    on_retry: Callable[[int, Exception, float], None] | None = None,
):
    """Decorator that adds retry with exponential backoff.

    Retries the decorated function when specific HTTP status codes are encountered.
    Uses exponential backoff with random jitter to avoid thundering herd.

    Works with both regular async functions and async generators.

    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay between retries in seconds (default: 1.0)
        max_delay: Maximum delay between retries in seconds (default: 60.0)
        retryable_statuses: HTTP status codes that should trigger retry
        on_retry: Optional callback for retry events: fn(attempt, exception, delay)

    Returns:
        Decorated async function or async generator with retry capability.

    Example:
        @with_retry(max_retries=3, base_delay=1.0)
        async def call_api() -> Response:
            return await http_client.post(...)

        @with_retry(max_retries=3, base_delay=1.0)
        async def stream_api() -> AsyncIterator[Event]:
            async for event in http_client.stream():
                yield event
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        # Check if function is an async generator
        if inspect.isasyncgenfunction(func):
            # Handle async generator functions
            @functools.wraps(func)
            async def asyncgen_wrapper(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
                last_exception: Exception | None = None

                for attempt in range(max_retries + 1):
                    try:
                        # Async generator - iterate through it
                        async for item in func(*args, **kwargs):
                            yield item
                        return  # Successfully completed

                    except Exception as e:
                        last_exception = e

                        # Check if this is the last attempt
                        if attempt >= max_retries:
                            logger.debug(f"Max retries ({max_retries}) exceeded")
                            break

                        # Extract status code from exception
                        status_code = _extract_status_code(e)

                        # Only retry if we have a status code and it's in retryable list
                        if status_code is None:
                            logger.debug(
                                f"No status code found in {type(e).__name__}, raising immediately"
                            )
                            raise

                        if status_code not in retryable_statuses:
                            logger.debug(
                                f"Status {status_code} not retryable, raising immediately"
                            )
                            raise

                        # Calculate delay with exponential backoff and jitter
                        delay = min(base_delay * (2**attempt), max_delay)
                        delay += random.uniform(0, 1)  # Add jitter

                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): "
                            f"{type(e).__name__}{f' (status={status_code})' if status_code else ''}, "
                            f"retrying in {delay:.2f}s..."
                        )

                        # Call optional callback
                        if on_retry:
                            try:
                                on_retry(attempt + 1, e, delay)
                            except Exception:
                                pass  # Don't let callback errors break retry logic

                        await asyncio.sleep(delay)

                # All retries exhausted
                if last_exception:
                    raise last_exception
                raise RuntimeError("Unexpected: no exception but retries exhausted")

            return asyncgen_wrapper
        else:
            # Handle regular async functions
            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                last_exception: Exception | None = None

                for attempt in range(max_retries + 1):
                    try:
                        return await func(*args, **kwargs)

                    except Exception as e:
                        last_exception = e

                        # Check if this is the last attempt
                        if attempt >= max_retries:
                            logger.debug(f"Max retries ({max_retries}) exceeded")
                            break

                        # Extract status code from exception
                        status_code = _extract_status_code(e)

                        # Only retry if we have a status code and it's in retryable list
                        if status_code is None:
                            logger.debug(
                                f"No status code found in {type(e).__name__}, raising immediately"
                            )
                            raise

                        if status_code not in retryable_statuses:
                            logger.debug(
                                f"Status {status_code} not retryable, raising immediately"
                            )
                            raise

                        # Calculate delay with exponential backoff and jitter
                        delay = min(base_delay * (2**attempt), max_delay)
                        delay += random.uniform(0, 1)  # Add jitter

                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): "
                            f"{type(e).__name__}{f' (status={status_code})' if status_code else ''}, "
                            f"retrying in {delay:.2f}s..."
                        )

                        # Call optional callback
                        if on_retry:
                            try:
                                on_retry(attempt + 1, e, delay)
                            except Exception:
                                pass  # Don't let callback errors break retry logic

                        await asyncio.sleep(delay)

                # All retries exhausted
                if last_exception:
                    raise last_exception
                raise RuntimeError("Unexpected: no exception but retries exhausted")

            return wrapper

    return decorator
