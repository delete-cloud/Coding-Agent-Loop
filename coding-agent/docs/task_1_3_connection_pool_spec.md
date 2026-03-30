# 任务 1.3: Provider 连接池 - 详细实现方案

## 需求规格

### 功能需求
1. 复用 HTTP 连接，避免每次请求新建连接
2. 支持配置最大连接数和 keep-alive
3. 程序退出时正确关闭连接

### 非功能需求
1. 线程/协程安全
2. 连接超时配置
3. 不泄漏资源

---

## 实现方案

### 修改文件 1: `src/coding_agent/providers/openai_compat.py`

```python
"""OpenAI-compatible provider with connection pooling."""

from __future__ import annotations

import httpx
from typing import AsyncIterator

from coding_agent.providers.base import ChatProvider, Message, StreamEvent, ToolSchema
from coding_agent.utils.retry import with_retry


class OpenAICompatProvider(ChatProvider):
    """Provider for OpenAI-compatible APIs with connection pooling."""
    
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        max_connections: int = 10,
        max_keepalive: int = 5,
        timeout: float = 60.0,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        
        # Create reusable HTTP client with connection pool
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        )
        timeout_config = httpx.Timeout(timeout)
        
        self._client = httpx.AsyncClient(
            limits=limits,
            timeout=timeout_config,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        
        # Track if we need to close the client
        self._owns_client = True
    
    @with_retry(max_retries=3, base_delay=1.0)
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream completions with connection reuse."""
        url = f"{self.base_url or 'https://api.openai.com/v1'}/chat/completions"
        
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        
        async with self._client.stream("POST", url, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    # Parse and yield event...
                    yield self._parse_event(data)
    
    def _parse_event(self, data: str) -> StreamEvent:
        """Parse SSE event data."""
        import json
        parsed = json.loads(data)
        # ... existing parsing logic ...
        return StreamEvent(type="delta", text=parsed["choices"][0]["delta"].get("content", ""))
    
    async def close(self) -> None:
        """Close HTTP client and release connections."""
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None
    
    def __del__(self):
        """Destructor to warn if not properly closed."""
        if self._client and self._owns_client:
            import warnings
            warnings.warn(
                f"OpenAICompatProvider was not properly closed. "
                f"Use 'async with' or call close()",
                ResourceWarning,
                stacklevel=2,
            )
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
```

---

### 修改文件 2: `src/coding_agent/providers/anthropic.py`

```python
"""Anthropic provider with connection pooling."""

from __future__ import annotations

from typing import AsyncIterator

from coding_agent.providers.base import ChatProvider, Message, StreamEvent, ToolSchema
from coding_agent.utils.retry import with_retry


class AnthropicProvider(ChatProvider):
    """Provider for Anthropic Claude API with connection pooling."""
    
    def __init__(
        self,
        model: str,
        api_key: str,
        max_connections: int = 10,
        timeout: float = 60.0,
    ):
        self.model = model
        self.api_key = api_key
        
        # Anthropic SDK handles connection pooling internally
        # We just need to ensure proper cleanup
        self._client = None
        self._client_options = {
            "api_key": api_key,
            "max_retries": 0,  # We handle retries ourselves
            "timeout": timeout,
            # Connection pool settings (if SDK supports)
            "connection_pool_limits": {
                "max_connections": max_connections,
            } if hasattr(self, "_set_connection_pool") else None,
        }
    
    def _get_client(self):
        """Lazy initialization of Anthropic client."""
        if self._client is None:
            from anthropic import AsyncAnthropic
            
            # Filter out None values
            options = {k: v for k, v in self._client_options.items() if v is not None}
            self._client = AsyncAnthropic(**options)
        return self._client
    
    @with_retry(max_retries=3, base_delay=1.0)
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream completions with connection reuse."""
        client = self._get_client()
        
        # Anthropic SDK manages its own connection pool
        async with client.messages.stream(
            model=self.model,
            messages=messages,
            tools=tools,
            max_tokens=4096,
        ) as stream:
            async for event in stream:
                yield self._convert_event(event)
    
    def _convert_event(self, event) -> StreamEvent:
        """Convert Anthropic event to StreamEvent."""
        # ... existing conversion logic ...
        return StreamEvent(type="delta", text=event.delta.text if hasattr(event, 'delta') else "")
    
    async def close(self) -> None:
        """Close client and release connections."""
        if self._client:
            await self._client.close()
            self._client = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
```

---

### 修改文件 3: `src/coding_agent/core/loop.py`

在 AgentLoop 中确保 Provider 正确关闭：

```python
class AgentLoop:
    """Main agent loop with proper resource management."""
    
    async def run_turn(self, user_input: str) -> TurnOutcome:
        """Run a single turn with resource cleanup."""
        try:
            # ... existing logic ...
            pass
        finally:
            # Ensure provider is closed if we own it
            if hasattr(self.provider, 'close'):
                await self.provider.close()
    
    async def __aenter__(self):
        """Async context manager."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup resources on exit."""
        if hasattr(self.provider, 'close'):
            await self.provider.close()
```

---

## 测试方案

### 单元测试: `tests/unit/providers/test_connection_pool.py`

```python
"""Tests for connection pooling in providers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from coding_agent.providers.openai_compat import OpenAICompatProvider


class TestConnectionPool:
    """Tests for connection pool configuration."""
    
    @pytest.mark.asyncio
    async def test_provider_creates_client_with_pool(self):
        """Test that provider creates HTTP client with connection pool."""
        provider = OpenAICompatProvider(
            model="gpt-4",
            api_key="test-key",
            max_connections=20,
            max_keepalive=10,
        )
        
        # Check client was created with correct limits
        assert provider._client is not None
        assert provider._client.limits.max_connections == 20
        assert provider._client.limits.max_keepalive_connections == 10
        
        await provider.close()
    
    @pytest.mark.asyncio
    async def test_connection_reuse_across_requests(self):
        """Test that connections are reused across multiple requests."""
        provider = OpenAICompatProvider(
            model="gpt-4",
            api_key="test-key",
        )
        
        # Mock the stream method to track connection creation
        connection_count = 0
        original_stream = provider.stream
        
        async def counting_stream(*args, **kwargs):
            nonlocal connection_count
            connection_count += 1
            # Yield empty to avoid actual network call
            from coding_agent.providers.base import StreamEvent
            yield StreamEvent(type="done")
        
        with patch.object(provider, 'stream', counting_stream):
            # Make multiple requests
            for _ in range(5):
                async for _ in provider.stream([]):
                    pass
        
        # Should reuse same client/connections
        # Connection count should be 5, but actual TCP connections should be fewer
        assert connection_count == 5
        
        await provider.close()
    
    @pytest.mark.asyncio
    async def test_provider_cleanup_on_close(self):
        """Test that provider properly closes HTTP client."""
        provider = OpenAICompatProvider(
            model="gpt-4",
            api_key="test-key",
        )
        
        # Close should work without error
        await provider.close()
        
        # Client should be None after close
        assert provider._client is None
    
    @pytest.mark.asyncio
    async def test_context_manager_properly_closes(self):
        """Test that async context manager properly closes provider."""
        async with OpenAICompatProvider(
            model="gpt-4",
            api_key="test-key",
        ) as provider:
            assert provider._client is not None
        
        # After exiting context, client should be closed
        assert provider._client is None
```

---

## 验证方法

```bash
# 1. 检查连接复用
netstat -an | grep ESTABLISHED | wc -l
# 100 次 API 调用前: 1
# 100 次 API 调用中: 2-5
# 100 次 API 调用后: 1

# 2. 性能对比
# 优化前: 100 次调用，每次新建连接 → ~45s
# 优化后: 100 次调用，复用连接 → ~25s
```

---

## 验收清单

- [ ] HTTP 客户端配置连接池
- [ ] 最大连接数和 keepalive 可配置
- [ ] Provider 支持 async context manager
- [ ] 程序退出时正确关闭连接
- [ ] 无资源泄漏警告
- [ ] 单元测试验证连接复用

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| OpenAI provider 连接池 | 15 min |
| Anthropic provider 连接池 | 10 min |
| AgentLoop 资源清理 | 10 min |
| 单元测试 | 20 min |
| **总计** | **~55 min** |
