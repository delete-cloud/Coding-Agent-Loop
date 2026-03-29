# 任务 1.1: API 重试机制 - 详细实现方案

## 需求规格

### 功能需求
1. 对 Provider API 调用实现自动重试机制
2. 支持指数退避（exponential backoff）
3. 只对特定 HTTP 状态码重试：429, 500, 502, 503, 529
4. 最大重试次数：3 次
5. 基础延迟：1 秒，带随机抖动（jitter）

### 非功能需求
1. 不重试非幂等的请求（如 POST 创建资源）- 但我们的场景全是 POST 到 completions API
2. 可配置（通过 Config）
3. 记录重试日志

---

## 设计方案

### 方案 A: 装饰器模式（推荐）

```python
# src/coding_agent/utils/retry.py
import functools
import random
import time
from typing import Callable, TypeVar

T = TypeVar('T')

def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    retryable_statuses: set[int] = {429, 500, 502, 503, 529},
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator for API retry with exponential backoff."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    
                    # Check if should retry
                    status = getattr(e, 'status_code', None)
                    if status not in retryable_statuses:
                        raise
                    
                    if attempt >= max_retries:
                        raise
                    
                    # Calculate delay with jitter
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"API call failed (attempt {attempt + 1}/{max_retries + 1}), "
                        f"status={status}, retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
            
            raise last_exception
        
        return wrapper
    return decorator
```

**优点**:
- 非侵入式，不修改原有 Provider 类
- 可复用到其他异步函数
- 配置灵活

**缺点**:
- 需要为每个方法单独装饰
- 异常类型判断依赖具体 Provider 的实现

---

### 方案 B: Provider 基类封装（备选）

在 `ChatProvider` 协议中增加重试逻辑，所有 Provider 实现继承。

**优点**:
- 统一管理
- 可以访问 Provider 内部状态

**缺点**:
- 需要修改所有 Provider 实现
- 侵入性较强

---

## 推荐实现：方案 A（装饰器）

### 文件修改清单

#### 1. 新建: `src/coding_agent/utils/retry.py`
```python
"""Retry utilities with exponential backoff."""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Any, Callable, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')

# HTTP status codes that should trigger retry
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 529})


class RetryableError(Exception):
    """Error that can be retried."""
    
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_statuses: set[int] | frozenset[int] = RETRYABLE_STATUS_CODES,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> Callable[[Callable[..., Coroutine[Any, Any, T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """Decorator that adds retry with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay between retries in seconds (default: 1.0)
        max_delay: Maximum delay between retries in seconds (default: 60.0)
        retryable_statuses: HTTP status codes that should trigger retry
        on_retry: Optional callback for retry events: fn(attempt, exception, delay)
    
    Example:
        @with_retry(max_retries=3, base_delay=1.0)
        async def call_api() -> Response:
            return await http_client.post(...)
    """
    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]]
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
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
                    
                    # Check if error is retryable
                    if status_code is not None and status_code not in retryable_statuses:
                        logger.debug(f"Status {status_code} not retryable, raising immediately")
                        raise
                    
                    # Calculate delay with exponential backoff and jitter
                    delay = min(base_delay * (2 ** attempt), max_delay)
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


def _extract_status_code(exception: Exception) -> int | None:
    """Extract HTTP status code from various exception types."""
    # Check for status_code attribute (OpenAI SDK, httpx)
    if hasattr(exception, 'status_code'):
        return getattr(exception, 'status_code')
    
    # Check for response.status_code (some SDKs)
    if hasattr(exception, 'response'):
        response = getattr(exception, 'response')
        if hasattr(response, 'status_code'):
            return getattr(response, 'status_code')
    
    # Check for status attribute (anthropic SDK)
    if hasattr(exception, 'status'):
        return getattr(exception, 'status')
    
    return None
```

---

#### 2. 修改: `src/coding_agent/providers/openai_compat.py`

```python
"""OpenAI-compatible provider implementation."""

# ... existing imports ...
from coding_agent.utils.retry import with_retry

class OpenAICompatProvider:
    """Provider for OpenAI-compatible APIs."""
    
    def __init__(...):
        # ... existing code ...
        # Reuse httpx client for connection pooling
        self._http_client = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    
    @with_retry(max_retries=3, base_delay=1.0)
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream completions with automatic retry."""
        # ... existing implementation ...
    
    async def close(self) -> None:
        """Close HTTP client."""
        await self._http_client.aclose()
```

---

#### 3. 修改: `src/coding_agent/providers/anthropic.py`

```python
"""Anthropic provider implementation."""

# ... existing imports ...
from coding_agent.utils.retry import with_retry

class AnthropicProvider:
    """Provider for Anthropic Claude API."""
    
    @with_retry(max_retries=3, base_delay=1.0)
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream completions with automatic retry."""
        # ... existing implementation ...
```

---

### 测试策略

#### 单元测试: `tests/unit/utils/test_retry.py`

```python
"""Tests for retry utilities."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from coding_agent.utils.retry import with_retry, RETRYABLE_STATUS_CODES


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
                "success"
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
        
        with pytest.raises(Exception) as exc_info:
            await test_func()
        
        assert mock_func.call_count == 1  # No retry
    
    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        """Test failure after max retries."""
        mock_func = AsyncMock(side_effect=self._make_exception(500))
        
        @with_retry(max_retries=2, base_delay=0.01)
        async def test_func():
            return await mock_func()
        
        with pytest.raises(Exception):
            await test_func()
        
        assert mock_func.call_count == 3  # Initial + 2 retries
    
    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Test exponential backoff timing."""
        mock_func = AsyncMock(
            side_effect=[
                self._make_exception(503),
                self._make_exception(503),
                "success"
            ]
        )
        
        sleep_calls = []
        
        async def mock_sleep(delay):
            sleep_calls.append(delay)
        
        @with_retry(max_retries=3, base_delay=1.0)
        async def test_func():
            return await mock_func()
        
        with patch('asyncio.sleep', mock_sleep):
            await test_func()
        
        # Check exponential backoff: ~1s, ~2s (with jitter)
        assert len(sleep_calls) == 2
        assert 1.0 <= sleep_calls[0] < 2.0  # First retry: ~1s + jitter
        assert 2.0 <= sleep_calls[1] < 4.0  # Second retry: ~2s + jitter
    
    @pytest.mark.asyncio
    async def test_on_retry_callback(self):
        """Test on_retry callback is called."""
        mock_func = AsyncMock(
            side_effect=[self._make_exception(429), "success"]
        )
        callback_calls = []
        
        def on_retry(attempt, exception, delay):
            callback_calls.append((attempt, type(exception).__name__, delay))
        
        @with_retry(max_retries=3, base_delay=0.01, on_retry=on_retry)
        async def test_func():
            return await mock_func()
        
        await test_func()
        
        assert len(callback_calls) == 1
        assert callback_calls[0][0] == 1  # First retry attempt
    
    def _make_exception(self, status_code: int):
        """Create an exception with status_code attribute."""
        exc = Exception(f"HTTP {status_code}")
        exc.status_code = status_code
        return exc


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
```

---

### 集成测试

```python
# tests/unit/providers/test_retry_integration.py

@pytest.mark.asyncio
async def test_openai_provider_retries_on_rate_limit():
    """Test that OpenAI provider retries on 429."""
    # Mock httpx to return 429 then 200
    # Verify retry happens and succeeds

@pytest.mark.asyncio  
async def test_anthropic_provider_retries_on_overloaded():
    """Test that Anthropic provider retries on 529."""
    # Mock anthropic SDK to raise OverloadedError
    # Verify retry happens
```

---

## 验收清单

- [ ] `with_retry` 装饰器实现完成
- [ ] 支持指数退避 + 抖动
- [ ] 正确识别 429, 500, 502, 503, 529 状态码
- [ ] OpenAI provider 集成重试
- [ ] Anthropic provider 集成重试
- [ ] 单元测试覆盖所有场景（成功、重试、失败、回调）
- [ ] 集成测试验证真实场景
- [ ] 日志记录清晰可读
- [ ] 文档字符串完整

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| retry.py 实现 | 20 min |
| OpenAI provider 集成 | 10 min |
| Anthropic provider 集成 | 10 min |
| 单元测试 | 30 min |
| 集成测试 | 15 min |
| **总计** | **~85 min** |

---

## 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 不同 SDK 的异常结构不一致 | `_extract_status_code` 函数处理多种情况 |
| 重试导致请求堆积 | 添加 max_delay 上限，指数退避自动稀释 |
| 无限重试风险 | 严格的 max_retries 限制 |

---

**准备就绪，可以开始实现！**
