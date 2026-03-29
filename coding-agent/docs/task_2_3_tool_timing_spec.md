# 任务 2.3: 工具执行时间显示 - 详细实现方案

## 需求规格

### 功能需求
1. 在 TUI 中显示每个 tool 的执行耗时
2. < 1s: 默认颜色
3. 1-5s: 黄色高亮
4. > 5s: 红色高亮 + ⚠️ 标记

### 非功能需求
1. 精度：毫秒级
2. 不影响性能（开销可忽略）
3. 与现有 TUI 风格一致

---

## 实现方案

### 修改文件: `src/coding_agent/ui/rich_tui.py`

```python
"""Rich TUI with tool execution timing."""

import time
from typing import Dict

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree


class ToolExecutionTracker:
    """Track tool execution times."""
    
    def __init__(self):
        self._start_times: Dict[str, float] = {}
        self._durations: Dict[str, float] = {}
    
    def start(self, call_id: str) -> None:
        """Start tracking a tool call."""
        self._start_times[call_id] = time.perf_counter()
    
    def end(self, call_id: str) -> float:
        """End tracking and return duration."""
        if call_id not in self._start_times:
            return 0.0
        
        duration = time.perf_counter() - self._start_times[call_id]
        self._durations[call_id] = duration
        return duration
    
    def format_duration(self, duration: float) -> Text:
        """Format duration with color coding."""
        if duration < 1.0:
            # < 1s: default color
            return Text(f"({duration:.2f}s)", style="dim")
        elif duration < 5.0:
            # 1-5s: yellow
            return Text(f"({duration:.2f}s)", style="yellow")
        else:
            # > 5s: red with warning
            return Text(f"({duration:.2f}s) ⚠️", style="red bold")


class CodingAgentTUI:
    """TUI with tool timing display."""
    
    def __init__(self):
        self.console = Console()
        self._tool_tracker = ToolExecutionTracker()
    
    def show_tool_call(self, call_id: str, tool: str, args: dict) -> None:
        """Display tool call start."""
        self._tool_tracker.start(call_id)
        
        # ... existing display logic ...
    
    def show_tool_result(self, call_id: str, result: str) -> None:
        """Display tool result with timing."""
        duration = self._tool_tracker.end(call_id)
        timing_text = self._tool_tracker.format_duration(duration)
        
        # Create panel with timing
        content = Text()
        content.append(f"✓ {tool_name} ", style="green")
        content.append(timing_text)
        
        panel = Panel(content, border_style="green")
        self.console.print(panel)
```

---

## 测试方案

```python
# tests/ui/test_tool_timing.py
"""Tests for tool execution timing."""

import time
from unittest.mock import Mock

from coding_agent.ui.rich_tui import ToolExecutionTracker


class TestToolExecutionTracker:
    """Tests for tool timing."""
    
    def test_start_and_end(self):
        """Test basic timing."""
        tracker = ToolExecutionTracker()
        
        tracker.start("call_1")
        time.sleep(0.01)  # 10ms
        duration = tracker.end("call_1")
        
        assert duration >= 0.01
        assert duration < 0.1  # Should be quick
    
    def test_format_duration_colors(self):
        """Test color formatting."""
        tracker = ToolExecutionTracker()
        
        # < 1s: dim
        text = tracker.format_duration(0.5)
        assert "dim" in str(text.style)
        
        # 1-5s: yellow
        text = tracker.format_duration(2.5)
        assert "yellow" in str(text.style)
        
        # > 5s: red
        text = tracker.format_duration(10.0)
        assert "red" in str(text.style)
```

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| ToolExecutionTracker 类 | 15 min |
| 集成到 CodingAgentTUI | 20 min |
| 单元测试 | 15 min |
| **总计** | **~50 min** |
