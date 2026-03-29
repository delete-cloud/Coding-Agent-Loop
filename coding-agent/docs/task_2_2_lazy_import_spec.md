# 任务 2.2: 启动加速（Lazy Import）- 详细实现方案

## 需求规格

### 功能需求
1. 延迟加载重模块（lancedb, tiktoken, numpy）
2. CLI --help 启动 < 1s（当前 ~2.5s）
3. 只在首次使用时导入

### 非功能需求
1. 不破坏现有 API
2. 类型检查仍然有效
3. 错误信息清晰（如果缺少依赖）

---

## 实现方案

### 策略：TYPE_CHECKING + 延迟导入

```python
# src/coding_agent/__init__.py
"""Coding Agent package with lazy imports."""

from __future__ import annotations

from typing import TYPE_CHECKING

# TYPE_CHECKING 只在类型检查时导入，运行时不导入
if TYPE_CHECKING:
    from coding_agent.tokens import TiktokenCounter
    from coding_agent.kb import KB


def get_token_counter(*args, **kwargs):
    """Lazy import token counter."""
    from coding_agent.tokens import TiktokenCounter
    return TiktokenCounter(*args, **kwargs)


def get_kb(*args, **kwargs):
    """Lazy import KB."""
    from coding_agent.kb import KB
    return KB(*args, **kwargs)
```

### 修改文件: `src/coding_agent/__main__.py`

```python
"""CLI entry point with lazy imports."""

import click

# 延迟导入重模块
# 不要在这里导入：from coding_agent.kb import KB

@click.group()
def main():
    """Coding Agent CLI."""
    pass

@main.command()
def index():
    """Index codebase (heavy import happens here)."""
    # 命令内部才导入
    from coding_agent.kb import KB
    # ...
```

### 修改文件: `src/coding_agent/core/context.py`

```python
"""Context with lazy KB import."""

from __future__ import annotations

from typing import TYPE_CHECKING

# 延迟导入
if TYPE_CHECKING:
    from coding_agent.kb import KB


class Context:
    def __init__(self, ..., kb: KB | None = None):
        self._kb = kb
    
    async def search_knowledge_base(self, query: str, k: int = 5):
        """Search KB (lazy import if needed)."""
        if self._kb is None:
            # Lazy initialization
            from coding_agent.kb import KB
            self._kb = KB(...)  # 或使用传入的
        return await self._kb.search(query, k=k)
```

---

## 测量启动时间

```python
# scripts/benchmark_startup.py
"""Benchmark CLI startup time."""

import subprocess
import time

def main():
    start = time.perf_counter()
    result = subprocess.run(
        ["python", "-m", "coding_agent", "--help"],
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - start
    
    print(f"Startup time: {elapsed:.2f}s")
    print(f"Return code: {result.returncode}")

if __name__ == "__main__":
    main()
```

---

## 验收清单

- [ ] `python -m coding_agent --help` < 1s
- [ ] 类型检查仍然通过（mypy）
- [ ] 功能测试全部通过
- [ ] 启动时间对比数据记录

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| 添加 TYPE_CHECKING 导入 | 15 min |
| 修改 CLI 延迟导入 | 15 min |
| 测试类型检查 | 15 min |
| 性能基准测试 | 15 min |
| **总计** | **~60 min** |
