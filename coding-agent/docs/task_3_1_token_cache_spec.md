# 任务 3.1: Token 计数缓存 - 详细实现方案

## 需求规格

1. 对短文本（<100 chars）缓存 token 计数结果
2. LRU 缓存，大小 10000
3. 性能提升：1000 次计数从 50ms → 5ms

---

## 实现方案

```python
# src/coding_agent/tokens.py

from functools import lru_cache


class TiktokenCounter:
    """Token counter with caching."""
    
    def __init__(self, model: str = "gpt-4"):
        self.model = model
        self._encoding = None
        self._tiktoken_available = None
    
    @lru_cache(maxsize=10000)
    def _count_cached(self, text: str) -> int:
        """Cached token count for short texts."""
        if not self._tiktoken_available:
            return len(text) // 4
        return len(self._encoding.encode(text, disallowed_special=()))
    
    def count(self, text: str) -> int:
        """Count tokens with caching for short texts."""
        # Only cache short texts
        if len(text) < 100:
            return self._count_cached(text)
        
        # Long texts: compute directly
        if not self._tiktoken_available:
            return len(text) // 4
        return len(self._encoding.encode(text, disallowed_special=()))
```

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| 添加 @lru_cache | 10 min |
| 缓存策略（短文本判断） | 10 min |
| 性能测试 | 10 min |
| **总计** | **~30 min** |
