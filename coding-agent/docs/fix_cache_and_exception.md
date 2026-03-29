# 修复方案：缓存限制和异常处理

## 问题 1: 缓存无限制

### 当前代码
```python
self._summary_cache: dict[str, Summary] = {}  # 无限制
```

### 解决方案 A: LRU Cache（推荐）

使用 `functools.lru_cache` 或 OrderedDict 实现 LRU 淘汰策略。

```python
from collections import OrderedDict

class Context:
    MAX_SUMMARY_CACHE = 100  # 最多缓存 100 个摘要
    
    def __init__(self, ...):
        self._summary_cache: OrderedDict[str, Summary] = OrderedDict()
    
    def _add_to_cache(self, key: str, summary: Summary) -> None:
        """Add to cache with LRU eviction."""
        if key in self._summary_cache:
            # Move to end (most recently used)
            self._summary_cache.move_to_end(key)
        else:
            # Evict oldest if at capacity
            if len(self._summary_cache) >= self.MAX_SUMMARY_CACHE:
                self._summary_cache.popitem(last=False)
            self._summary_cache[key] = summary
```

### 解决方案 B: 简单数量限制

```python
def _cleanup_cache_if_needed(self) -> None:
    """Remove random entries if cache too large."""
    MAX_CACHE_SIZE = 100
    if len(self._summary_cache) > MAX_CACHE_SIZE:
        # Remove 20% oldest entries
        keys_to_remove = list(self._summary_cache.keys())[:MAX_CACHE_SIZE // 5]
        for key in keys_to_remove:
            del self._summary_cache[key]
```

**推荐 A**：LRU 更符合使用模式，保留最近使用的摘要。

---

## 问题 2: 异常处理过于宽泛

### 当前代码
```python
try:
    summary = await self._summarizer.summarize(...)
except Exception:  # 太宽泛！
    fallback = RuleSummarizer()
    summary = await fallback.summarize(old)
```

### 问题
- 捕获 `KeyboardInterrupt`（用户想退出）
- 捕获 `SystemExit`（程序要退出）
- 捕获 `MemoryError`（严重问题，不应静默处理）

### 解决方案

```python
# 只捕获可预期的异常
RETRYABLE_EXCEPTIONS = (
    ConnectionError,      # 网络问题
    TimeoutError,         # 超时
    RuntimeError,         # LLM 运行时错误
    ValueError,           # 参数错误
)

try:
    summary = await self._summarizer.summarize(...)
except RETRYABLE_EXCEPTIONS as e:
    logger.warning(f"LLM summarization failed: {e}, using fallback")
    fallback = RuleSummarizer()
    summary = await fallback.summarize(old)
except Exception:
    # 不应发生的错误，记录后重新抛出
    logger.exception("Unexpected error in summarization")
    raise
```

---

## 推荐方案

1. **缓存**：使用 OrderedDict + LRU 策略
2. **异常**：明确定义可重试的异常类型，其他异常记录后抛出

这样既能防止内存泄漏，又能正确区分可恢复错误和严重错误。
