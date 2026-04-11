"""Tests for retry utilities."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coding_agent.utils.retry import RETRYABLE_STATUS_CODES, RetryableError, with_retry


class StatusError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


@dataclass
class ResponseStub:
    status_code: int


class ResponseStatusError(Exception):
    def __init__(self, message: str, response: ResponseStub):
        super().__init__(message)
        self.response = response


class TestWithRetry:
    """Tests for with_retry decorator."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        """Test successful call doesn't retry."""
        mock_func = AsyncMock(return_value="success")

        @with_retry(max_retries=3)
        async def test_func():
            return await mock_func()

        result = await test_func()

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_retryable_status(self):
        """Test retry on retryable status code."""
        # First 2 calls fail with 429, 3rd succeeds
        mock_func = AsyncMock(
            side_effect=[
                self._make_exception(429),
                self._make_exception(429),
                "success",
            ]
        )

        @with_retry(max_retries=3, base_delay=0.01)  # Fast for testing
        async def test_func():
            return await mock_func()

        result = await test_func()

        assert result == "success"
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_status(self):
        """Test no retry on non-retryable status."""
        mock_func = AsyncMock(side_effect=self._make_exception(400))

        @with_retry(max_retries=3)
        async def test_func():
            return await mock_func()

        with pytest.raises(RetryableError) as exc_info:
            await test_func()

        assert exc_info.value.status_code == 400
        assert mock_func.call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        """Test failure after max retries."""
        mock_func = AsyncMock(side_effect=self._make_exception(500))

        @with_retry(max_retries=2, base_delay=0.01)
        async def test_func():
            return await mock_func()

        with pytest.raises(RetryableError) as exc_info:
            await test_func()

        assert exc_info.value.status_code == 500
        assert mock_func.call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Test exponential backoff timing."""
        mock_func = AsyncMock(
            side_effect=[
                self._make_exception(503),
                self._make_exception(503),
                "success",
            ]
        )

        sleep_calls = []

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        @with_retry(max_retries=3, base_delay=1.0)
        async def test_func():
            return await mock_func()

        with patch("asyncio.sleep", mock_sleep):
            await test_func()

        # Check exponential backoff: ~1s, ~2s (with jitter)
        assert len(sleep_calls) == 2
        assert 1.0 <= sleep_calls[0] < 2.0  # First retry: ~1s + jitter
        assert 2.0 <= sleep_calls[1] < 4.0  # Second retry: ~2s + jitter

    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        """Test on_retry callback is called."""
        mock_func = AsyncMock(side_effect=[self._make_exception(429), "success"])
        callback_calls = []

        def on_retry(attempt, exception, delay):
            callback_calls.append((attempt, type(exception).__name__, delay))

        @with_retry(max_retries=3, base_delay=0.01, on_retry=on_retry)
        async def test_func():
            return await mock_func()

        await test_func()

        assert len(callback_calls) == 1
        assert callback_calls[0][0] == 1  # First retry attempt

    @pytest.mark.asyncio
    async def test_callback_error_not_breaking(self):
        """Test that callback errors don't break retry logic."""
        mock_func = AsyncMock(side_effect=[self._make_exception(429), "success"])

        def failing_callback(attempt, exception, delay):
            raise RuntimeError("Callback failed!")

        @with_retry(max_retries=3, base_delay=0.01, on_retry=failing_callback)
        async def test_func():
            return await mock_func()

        # Should not raise despite callback error
        result = await test_func()
        assert result == "success"
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_status_attribute(self):
        """Test retry using 'status' attribute (Anthropic SDK style)."""
        exc = StatusError("Overloaded", status=529)

        mock_func = AsyncMock(side_effect=[exc, "success"])

        @with_retry(max_retries=3, base_delay=0.01)
        async def test_func():
            return await mock_func()

        result = await test_func()
        assert result == "success"
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_response_status_code(self):
        """Test retry using response.status_code (httpx style)."""
        exc = ResponseStatusError("Bad Gateway", response=ResponseStub(status_code=502))

        mock_func = AsyncMock(side_effect=[exc, "success"])

        @with_retry(max_retries=3, base_delay=0.01)
        async def test_func():
            return await mock_func()

        result = await test_func()
        assert result == "success"
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        mock_func = AsyncMock(
            side_effect=[
                self._make_exception(500),
                self._make_exception(500),
                self._make_exception(500),
                "success",
            ]
        )

        sleep_calls = []

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        @with_retry(max_retries=3, base_delay=1.0, max_delay=2.0)
        async def test_func():
            return await mock_func()

        with patch("asyncio.sleep", mock_sleep):
            await test_func()

        # With base_delay=1.0 and max_delay=2.0:
        # - First retry: min(1.0 * 2^0, 2.0) = 1.0 + jitter
        # - Second retry: min(1.0 * 2^1, 2.0) = 2.0 + jitter
        # - Third retry: min(1.0 * 2^2, 2.0) = 2.0 + jitter
        assert len(sleep_calls) == 3
        assert sleep_calls[0] < 2.0
        assert sleep_calls[1] < 3.0  # 2.0 + jitter
        assert sleep_calls[2] < 3.0  # 2.0 + jitter (capped)

    @pytest.mark.asyncio
    async def test_exception_without_status_not_retryable(self):
        """Test that exceptions without status code are not retried."""
        mock_func = AsyncMock(side_effect=ValueError("Some error"))

        @with_retry(max_retries=3)
        async def test_func():
            return await mock_func()

        with pytest.raises(ValueError) as exc_info:
            await test_func()

        assert str(exc_info.value) == "Some error"
        assert mock_func.call_count == 1  # No retry

    def _make_exception(self, status_code: int):
        """Create an exception with status_code attribute."""
        return RetryableError(f"HTTP {status_code}", status_code=status_code)


class TestRetryableStatuses:
    """Tests for retryable status codes."""

    def test_retryable_statuses_include_expected(self):
        """Test that expected status codes are retryable."""
        assert 429 in RETRYABLE_STATUS_CODES  # Rate limit
        assert 500 in RETRYABLE_STATUS_CODES  # Server error
        assert 502 in RETRYABLE_STATUS_CODES  # Bad gateway
        assert 503 in RETRYABLE_STATUS_CODES  # Service unavailable
        assert 529 in RETRYABLE_STATUS_CODES  # Overloaded

    def test_non_retryable_statuses(self):
        """Test that 4xx errors (except 429) are not retryable."""
        assert 400 not in RETRYABLE_STATUS_CODES
        assert 401 not in RETRYABLE_STATUS_CODES
        assert 403 not in RETRYABLE_STATUS_CODES
        assert 404 not in RETRYABLE_STATUS_CODES

    def test_retryable_is_frozenset(self):
        """Test that RETRYABLE_STATUS_CODES is immutable."""
        assert isinstance(RETRYABLE_STATUS_CODES, frozenset)
