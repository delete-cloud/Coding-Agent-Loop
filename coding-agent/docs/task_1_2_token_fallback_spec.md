# 任务 1.2: Token 计数优雅降级 - 详细实现方案

## 需求规格

### 功能需求
1. 当 `tiktoken` 不可用时，自动 fallback 到 `ApproximateCounter`
2. 发出警告日志告知用户
3. 不影响程序正常运行

### 非功能需求
1. 延迟加载（只在首次使用时检查）
2. 不增加启动时间
3. 向后兼容

---

## 实现方案

### 修改文件: `src/coding_agent/tokens.py`

#### 当前代码问题
```python
# 当前：直接在 __init__ 中导入 tiktoken
class TiktokenCounter:
    def __init__(self, model: str = "gpt-4"):
        import tiktoken  # 如果失败，整个类都无法使用
        self.encoding = tiktoken.encoding_for_model(model)
```

#### 改进后代码
```python
"""Token counting utilities for context budget management."""

from __future__ import annotations

import logging
import warnings
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class TokenCounter(Protocol):
    """Protocol for token counters."""
    
    def count(self, text: str) -> int: ...
    def count_messages(self, messages: list[dict]) -> int: ...


class TiktokenCounter:
    """Exact token counting using tiktoken for OpenAI models.
    
    Falls back to ApproximateCounter if tiktoken is not available.
    """
    
    _encoding: Any | None = None
    _fallback_counter: ApproximateCounter | None = None
    _tiktoken_available: bool | None = None
    
    def __init__(self, model: str = "gpt-4"):
        self.model = model
        self._check_tiktoken()
    
    def _check_tiktoken(self) -> bool:
        """Check if tiktoken is available, setup fallback if not."""
        if TiktokenCounter._tiktoken_available is not None:
            return TiktokenCounter._tiktoken_available
        
        try:
            import tiktoken
            
            # Try to get encoding for the model
            try:
                self._encoding = tiktoken.encoding_for_model(self.model)
            except KeyError:
                # Model not found, use default
                logger.debug(f"Model {self.model} not found in tiktoken, using cl100k_base")
                self._encoding = tiktoken.get_encoding("cl100k_base")
            
            TiktokenCounter._tiktoken_available = True
            return True
            
        except ImportError:
            TiktokenCounter._tiktoken_available = False
            TiktokenCounter._fallback_counter = ApproximateCounter()
            
            warnings.warn(
                f"tiktoken is not installed. Using ApproximateCounter for token counting. "
                f"Install tiktoken for exact counts: pip install tiktoken",
                UserWarning,
                stacklevel=3
            )
            logger.warning(
                "tiktoken not available, falling back to ApproximateCounter. "
                "Token counts will be approximate (±20%)."
            )
            return False
    
    def count(self, text: str) -> int:
        """Count tokens in text."""
        if TiktokenCounter._tiktoken_available:
            return len(self._encoding.encode(text, disallowed_special=()))
        else:
            return TiktokenCounter._fallback_counter.count(text)
    
    def count_messages(self, messages: list[dict]) -> int:
        """Count tokens in a list of messages."""
        if TiktokenCounter._tiktoken_available:
            return self._count_messages_exact(messages)
        else:
            return TiktokenCounter._fallback_counter.count_messages(messages)
    
    def _count_messages_exact(self, messages: list[dict]) -> int:
        """Exact message counting using tiktoken."""
        # ... existing implementation ...


class ApproximateCounter:
    """Fallback counter: 1 token ≈ 4 characters."""
    
    CHARS_PER_TOKEN = 4
    
    def count(self, text: str) -> int:
        """Approximate token count."""
        return len(text) // self.CHARS_PER_TOKEN + (1 if len(text) % self.CHARS_PER_TOKEN else 0)
    
    def count_messages(self, messages: list[dict]) -> int:
        """Approximate token count for messages."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += self.count(str(content))
            # Add overhead per message
            total += 4
        # Add framing tokens
        total += 3
        return total


def create_token_counter(model: str = "gpt-4") -> TokenCounter:
    """Factory function to create the best available token counter.
    
    Tries TiktokenCounter first, falls back to ApproximateCounter.
    """
    try:
        import tiktoken
        return TiktokenCounter(model)
    except ImportError:
        logger.warning("tiktoken not available, using ApproximateCounter")
        return ApproximateCounter()
```

---

## 测试方案

### 单元测试: `tests/unit/core/test_tokens_fallback.py`

```python
"""Tests for token counter fallback behavior."""

import warnings
from unittest.mock import patch

import pytest

from coding_agent.tokens import TiktokenCounter, ApproximateCounter, create_token_counter


class TestTiktokenFallback:
    """Tests for tiktoken fallback behavior."""
    
    def test_tiktoken_available_uses_exact(self):
        """Test that tiktoken is used when available."""
        try:
            import tiktoken
            counter = TiktokenCounter("gpt-4")
            
            # Should use exact counting
            count = counter.count("Hello, world!")
            assert count > 0
            
        except ImportError:
            pytest.skip("tiktoken not installed")
    
    def test_tiktoken_unavailable_uses_fallback(self):
        """Test fallback to ApproximateCounter when tiktoken missing."""
        # Mock tiktoken as unavailable
        with patch.dict("sys.modules", {"tiktoken": None}):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                
                counter = TiktokenCounter("gpt-4")
                
                # Should issue warning
                assert len(w) == 1
                assert "tiktoken is not installed" in str(w[0].message)
                
                # Should still work with fallback
                text = "Hello, world!"
                count = counter.count(text)
                assert count > 0
                
                # Approximate: len // 4
                assert count == len(text) // 4 + 1
    
    def test_count_consistency_between_implementations(self):
        """Test that both implementations return reasonable counts."""
        text = "This is a test message with some content."
        
        approx_counter = ApproximateCounter()
        approx_count = approx_counter.count(text)
        
        # Approximate count should be in reasonable range
        # True count is around len // 4 for English text
        assert approx_count > 0
        assert abs(approx_count - len(text) // 4) <= 2


class TestCreateTokenCounter:
    """Tests for create_token_counter factory."""
    
    def test_factory_returns_tiktoken_when_available(self):
        """Test factory returns TiktokenCounter when available."""
        try:
            import tiktoken
            counter = create_token_counter("gpt-4")
            assert isinstance(counter, TiktokenCounter)
        except ImportError:
            pytest.skip("tiktoken not installed")
    
    def test_factory_returns_approximate_when_tiktoken_missing(self):
        """Test factory returns ApproximateCounter when tiktoken missing."""
        with patch.dict("sys.modules", {"tiktoken": None}):
            counter = create_token_counter("gpt-4")
            assert isinstance(counter, ApproximateCounter)
```

---

## 验收清单

- [ ] 无 tiktoken 时自动使用 ApproximateCounter
- [ ] 发出 UserWarning 提示用户
- [ ] 日志记录降级信息
- [ ] 两种实现的结果差异在合理范围（±20%）
- [ ] 不增加启动时间（延迟检查）
- [ ] 单元测试覆盖两种场景

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| 修改 TiktokenCounter | 10 min |
| 添加 create_token_counter 工厂 | 5 min |
| 单元测试 | 15 min |
| **总计** | **~30 min** |
