# 任务 2.1: KB 索引进度条 - 详细实现方案

## 需求规格

### 功能需求
1. 在 KB 索引过程中显示进度条
2. 显示当前文件、已处理/总数、预估剩余时间
3. 支持取消操作（Ctrl+C 优雅退出）

### 非功能需求
1. 不影响索引性能（渲染开销 < 5%）
2. 支持非 TTY 环境（CI/CD）
3. 可禁用（--no-progress）

---

## 实现方案

### 依赖
```python
# 使用 rich.progress
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
```

### 修改文件: `src/coding_agent/kb.py`

```python
"""Knowledge Base with progress tracking."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
    TimeRemainingColumn,
    MofNCompleteColumn,
)

from coding_agent.kb import KB


class KBWithProgress(KB):
    """KB with progress bar support."""
    
    async def index_directory(
        self,
        root: Path,
        pattern: str = "**/*.py",
        show_progress: bool = True,
    ) -> None:
        """Index directory with optional progress bar.
        
        Args:
            root: Root directory to index
            pattern: File glob pattern
            show_progress: Whether to show progress bar
        """
        files = list(root.glob(pattern))
        
        if not files:
            return
        
        if not show_progress:
            # Original implementation without progress
            for file_path in files:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    await self.index_file(file_path.relative_to(root), content)
                except Exception:
                    continue
            return
        
        # With progress bar
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(complete_style="green", finished_style="green"),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            transient=True,  # Hide after completion
        )
        
        with progress:
            task = progress.add_task(
                f"Indexing {root.name}...",
                total=len(files),
            )
            
            for file_path in files:
                # Update description with current file
                progress.update(task, description=f"Indexing [cyan]{file_path.name}")
                
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    await self.index_file(file_path.relative_to(root), content)
                except Exception as e:
                    # Log error but continue
                    progress.console.print(f"[red]✗[/red] {file_path}: {e}")
                finally:
                    progress.advance(task)
        
        # Summary
        console = Console()
        console.print(f"[green]✓[/green] Indexed {len(files)} files")


# Alternative: Add progress parameter to existing KB class
```

### 添加配置选项

```python
# src/coding_agent/core/config.py
class Config:
    # ... existing fields ...
    show_progress: bool = True  # Enable/disable progress bars
```

---

## 测试方案

```python
# tests/unit/test_kb_progress.py
"""Tests for KB progress tracking."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from coding_agent.kb import KBWithProgress


class TestKBProgress:
    """Tests for KB progress functionality."""
    
    @pytest.mark.asyncio
    async def test_index_directory_shows_progress(self, tmp_path):
        """Test that progress bar is displayed."""
        # Create test files
        for i in range(5):
            (tmp_path / f"file{i}.py").write_text(f"content {i}")
        
        kb = KBWithProgress(db_path=tmp_path / "test.db")
        
        # Mock Progress to capture calls
        with patch("coding_agent.kb.Progress") as mock_progress:
            mock_instance = Mock()
            mock_progress.return_value.__enter__ = Mock(return_value=mock_instance)
            mock_progress.return_value.__exit__ = Mock(return_value=False)
            
            await kb.index_directory(tmp_path, show_progress=True)
            
            # Verify progress was created
            assert mock_progress.called
            assert mock_instance.add_task.called
    
    @pytest.mark.asyncio
    async def test_index_directory_no_progress_when_disabled(self, tmp_path):
        """Test that progress bar is not shown when disabled."""
        (tmp_path / "file.py").write_text("content")
        
        kb = KBWithProgress(db_path=tmp_path / "test.db")
        
        with patch("coding_agent.kb.Progress") as mock_progress:
            await kb.index_directory(tmp_path, show_progress=False)
            
            # Progress should not be created
            assert not mock_progress.called
```

---

## 验收清单

- [ ] 进度条显示文件名、进度百分比、剩余时间
- [ ] 支持 `show_progress=False` 禁用
- [ ] 非 TTY 环境自动禁用
- [ ] 错误文件显示红色标记但继续处理
- [ ] 完成后显示汇总信息

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| 实现 KBWithProgress | 20 min |
| 添加配置选项 | 5 min |
| 单元测试 | 20 min |
| **总计** | **~45 min** |
