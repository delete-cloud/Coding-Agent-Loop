# 任务 3.3: 上下文智能摘要 - 完整实现方案

## 需求分析

### 核心需求
1. **触发条件**: 当上下文超过 80% token 预算时自动触发
2. **摘要内容**: 保留任务目标、重要决策、待办事项
3. **实现方式**: 使用 LLM 对历史消息进行摘要
4. **成本控制**: 摘要本身也要消耗 token，需要权衡

### 设计决策
- **何时触发**: 在 `Context.build_working_set()` 中检查 token 使用量
- **摘要范围**: 保留最近 5 轮对话，摘要更早的历史
- **摘要模型**: 使用轻量级模型（如 gpt-4o-mini）降低成本
- **缓存策略**: 摘要结果缓存，避免重复生成

---

## 架构设计

### 新增模块

```
src/coding_agent/
├── summarizer/
│   ├── __init__.py
│   ├── base.py           # 摘要器基类
│   ├── llm_summarizer.py # LLM 实现
│   └── rule_summarizer.py # 规则摘要（fallback）
```

### 核心类设计

```python
# src/coding_agent/summarizer/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Summary:
    """Summary result."""
    content: str
    original_tokens: int
    summary_tokens: int
    key_points: list[str]


class Summarizer(Protocol):
    """Protocol for context summarizers."""
    
    async def summarize(
        self,
        messages: list[dict],
        max_tokens: int = 500,
    ) -> Summary: ...


# src/coding_agent/summarizer/llm_summarizer.py
from coding_agent.providers.base import ChatProvider


class LLMSummarizer:
    """LLM-based context summarizer."""
    
    def __init__(self, provider: ChatProvider):
        self.provider = provider
    
    async def summarize(
        self,
        messages: list[dict],
        max_tokens: int = 500,
    ) -> Summary:
        """Summarize messages using LLM."""
        
        # 构建摘要提示
        prompt = self._build_summary_prompt(messages)
        
        # 调用轻量级模型
        summary_text = await self._call_llm(prompt, max_tokens)
        
        # 解析关键要点
        key_points = self._extract_key_points(summary_text)
        
        return Summary(
            content=summary_text,
            original_tokens=self._count_tokens(messages),
            summary_tokens=self._count_tokens([{"content": summary_text}]),
            key_points=key_points,
        )
    
    def _build_summary_prompt(self, messages: list[dict]) -> str:
        """Build prompt for summarization."""
        conversation = self._format_conversation(messages)
        
        return f"""Summarize the following conversation into key points.
Focus on:
1. Original task/goal
2. Important decisions made
3. Current TODOs or pending items
4. Key findings or conclusions

Conversation:
{conversation}

Provide a concise summary in this format:
**Task**: [original goal]
**Decisions**: [key decisions]
**TODOs**: [pending items]
**Summary**: [brief summary]
"""
    
    def _format_conversation(self, messages: list[dict]) -> str:
        """Format messages for prompt."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]  # 截断长内容
            lines.append(f"{role}: {content}")
        return "\n".join(lines)
    
    async def _call_llm(self, prompt: str, max_tokens: int) -> str:
        """Call LLM for summarization."""
        summary_messages = [
            {"role": "system", "content": "You are a helpful assistant that summarizes conversations concisely."},
            {"role": "user", "content": prompt},
        ]
        
        result = []
        async for event in self.provider.stream(summary_messages):
            if event.text:
                result.append(event.text)
        
        return "".join(result)
    
    def _extract_key_points(self, summary: str) -> list[str]:
        """Extract key points from summary."""
        points = []
        for line in summary.split("\n"):
            line = line.strip()
            if line.startswith(("**", "- ", "• ")):
                points.append(line.lstrip("*-• ").strip())
        return points
    
    def _count_tokens(self, messages: list[dict]) -> int:
        """Estimate token count."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += len(content) // 4  # 粗略估计
        return total


# src/coding_agent/summarizer/rule_summarizer.py
class RuleSummarizer:
    """Rule-based summarizer (fallback, no LLM needed)."""
    
    async def summarize(
        self,
        messages: list[dict],
        max_tokens: int = 500,
    ) -> Summary:
        """Summarize using simple rules."""
        
        # 提取系统消息（通常包含任务目标）
        system_msgs = [m for m in messages if m.get("role") == "system"]
        
        # 提取工具调用（通常是实际行动）
        tool_calls = [m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")]
        
        # 提取用户消息中的问题
        user_msgs = [m for m in messages if m.get("role") == "user"]
        
        # 构建简单摘要
        summary_parts = ["**Conversation Summary**"]
        
        if system_msgs:
            task = system_msgs[0].get("content", "")[:100]
            summary_parts.append(f"**Task**: {task}...")
        
        summary_parts.append(f"**Messages**: {len(messages)} total")
        summary_parts.append(f"**Tool Calls**: {len(tool_calls)} actions")
        summary_parts.append(f"**User Queries**: {len(user_msgs)} interactions")
        
        summary_text = "\n".join(summary_parts)
        
        return Summary(
            content=summary_text,
            original_tokens=self._count_tokens(messages),
            summary_tokens=self._count_tokens([{"content": summary_text}]),
            key_points=summary_parts[1:],
        )
    
    def _count_tokens(self, messages: list[dict]) -> int:
        """Estimate token count."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += len(content) // 4
        return total
```

---

## Context 集成

```python
# src/coding_agent/core/context.py

from coding_agent.summarizer.base import Summarizer
from coding_agent.summarizer.llm_summarizer import LLMSummarizer
from coding_agent.summarizer.rule_summarizer import RuleSummarizer


class Context:
    """Context with intelligent summarization support."""
    
    # 触发摘要的阈值（80% 的 max_tokens）
    SUMMARIZE_THRESHOLD = 0.8
    # 保留的最近消息数
    KEEP_RECENT = 5
    # 摘要的最大 token 数
    SUMMARY_MAX_TOKENS = 300
    
    def __init__(
        self,
        max_tokens: int,
        system_prompt: str,
        planner: PlanManager | None = None,
        token_counter: TokenCounter | None = None,
        kb: KB | None = None,
        summarizer: Summarizer | None = None,
    ):
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.planner = planner
        self._token_counter = token_counter
        self._kb = kb
        self._summarizer = summarizer
        self._summary_cache: dict[str, Summary] = {}  # 缓存摘要
    
    def build_working_set(
        self,
        tape: Tape,
        provider: ChatProvider | None = None,
    ) -> list[dict[str, Any]]:
        """Build working set with automatic summarization."""
        # 基础构建
        messages = self._build_basic(tape)
        
        # 检查是否需要摘要
        total_tokens = self._estimate_tokens(messages)
        if total_tokens > self.max_tokens * self.SUMMARIZE_THRESHOLD:
            # 需要摘要
            if provider and self._summarizer is None:
                # 延迟创建 summarizer
                self._summarizer = LLMSummarizer(provider)
            
            messages = self._summarize_messages(messages, provider)
        
        return messages
    
    def _summarize_messages(
        self,
        messages: list[dict],
        provider: ChatProvider | None,
    ) -> list[dict]:
        """Summarize old messages."""
        # 保留最近的消息
        if len(messages) <= self.KEEP_RECENT + 2:  # +2 for system messages
            return messages
        
        recent = messages[-self.KEEP_RECENT:]
        old = messages[:-self.KEEP_RECENT]
        
        # 检查缓存
        cache_key = self._compute_cache_key(old)
        if cache_key in self._summary_cache:
            summary = self._summary_cache[cache_key]
        else:
            # 生成摘要
            if self._summarizer:
                import asyncio
                summary = asyncio.run(self._summarizer.summarize(
                    old,
                    max_tokens=self.SUMMARY_MAX_TOKENS,
                ))
                self._summary_cache[cache_key] = summary
            else:
                # 使用规则摘要作为 fallback
                fallback = RuleSummarizer()
                import asyncio
                summary = asyncio.run(fallback.summarize(old))
        
        # 构建新的消息列表
        summarized = [
            {
                "role": "system",
                "content": f"[Previous Context Summary - {summary.original_tokens}→{summary.summary_tokens} tokens]\n{summary.content}",
            },
            *recent,
        ]
        
        return summarized
    
    def _compute_cache_key(self, messages: list[dict]) -> str:
        """Compute cache key for messages."""
        import hashlib
        content = "\n".join(
            f"{m.get('role')}:{m.get('content', '')[:50]}"
            for m in messages
        )
        return hashlib.md5(content.encode()).hexdigest()[:16]
    
    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Estimate total tokens."""
        if self._token_counter:
            return self._token_counter.count_messages(messages)
        # 粗略估计
        return sum(len(str(m.get("content", ""))) // 4 for m in messages)
```

---

## 配置选项

```python
# src/coding_agent/core/config.py

class Config(BaseModel):
    # ... existing fields ...
    
    # Summarization settings
    enable_summarization: bool = True
    summary_threshold: float = 0.8  # 触发摘要的阈值
    summary_max_tokens: int = 300
    summary_model: str = "gpt-4o-mini"  # 轻量级模型
```

---

## 测试方案

```python
# tests/unit/summarizer/test_llm_summarizer.py

import pytest
from unittest.mock import AsyncMock, MagicMock

from coding_agent.summarizer.llm_summarizer import LLMSummarizer
from coding_agent.providers.base import StreamEvent


class TestLLMSummarizer:
    """Tests for LLM summarizer."""
    
    @pytest.mark.asyncio
    async def test_summarize_conversation(self):
        """Test basic summarization."""
        # Mock provider
        mock_provider = MagicMock()
        mock_provider.stream = AsyncMock(return_value=[
            StreamEvent(type="delta", text="**Task**: Implement feature\n**Decisions**: Use Python\n"),
        ])
        
        summarizer = LLMSummarizer(mock_provider)
        
        messages = [
            {"role": "system", "content": "You are a coding assistant"},
            {"role": "user", "content": "Help me implement a feature"},
            {"role": "assistant", "content": "I'll help you implement it"},
        ]
        
        summary = await summarizer.summarize(messages)
        
        assert summary.content
        assert summary.original_tokens > 0
        assert summary.summary_tokens > 0
        assert len(summary.key_points) > 0
    
    @pytest.mark.asyncio
    async def test_summarize_empty_messages(self):
        """Test summarizing empty list."""
        summarizer = LLMSummarizer(MagicMock())
        
        summary = await summarizer.summarize([])
        
        assert summary.content
        assert summary.original_tokens == 0


# tests/unit/summarizer/test_rule_summarizer.py

import pytest

from coding_agent.summarizer.rule_summarizer import RuleSummarizer


class TestRuleSummarizer:
    """Tests for rule-based summarizer."""
    
    @pytest.mark.asyncio
    async def test_summarize_basic(self):
        """Test rule-based summarization."""
        summarizer = RuleSummarizer()
        
        messages = [
            {"role": "system", "content": "Task: Implement login"},
            {"role": "user", "content": "How to do it?"},
            {"role": "assistant", "content": "Here is the code", "tool_calls": [{"id": "1"}]},
        ]
        
        summary = await summarizer.summarize(messages)
        
        assert "Task" in summary.content or "task" in summary.content
        assert summary.original_tokens > 0


# tests/unit/core/test_context_summarization.py

import pytest
from unittest.mock import MagicMock

from coding_agent.core.context import Context
from coding_agent.summarizer.base import Summary


class TestContextSummarization:
    """Tests for context summarization integration."""
    
    def test_should_summarize_when_over_threshold(self):
        """Test summarization triggers when over threshold."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        # Mock messages that exceed threshold
        messages = [{"role": "user", "content": "x" * 800}]  # ~200 tokens
        
        should_summarize = context._estimate_tokens(messages) > 1000 * 0.8
        
        assert should_summarize
    
    def test_should_not_summarize_when_under_threshold(self):
        """Test summarization doesn't trigger when under threshold."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        messages = [{"role": "user", "content": "short"}]
        
        should_summarize = context._estimate_tokens(messages) > 1000 * 0.8
        
        assert not should_summarize
    
    def test_summary_cache_key_stable(self):
        """Test cache key is stable for same messages."""
        context = Context(max_tokens=1000, system_prompt="Test")
        
        messages = [{"role": "user", "content": "test message"}]
        
        key1 = context._compute_cache_key(messages)
        key2 = context._compute_cache_key(messages)
        
        assert key1 == key2
```

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| 创建 summarizer 模块 | 30 min |
| LLMSummarizer 实现 | 45 min |
| RuleSummarizer 实现 | 15 min |
| Context 集成 | 30 min |
| 单元测试 | 45 min |
| **总计** | **~165 min (2.75 hours)** |

---

## 验收标准

- [ ] 上下文超过 80% 预算时自动触发摘要
- [ ] 摘要使用 LLM 生成，包含关键信息
- [ ] 保留最近 5 轮对话完整内容
- [ ] 摘要结果缓存，避免重复生成
- [ ] 提供 RuleSummarizer 作为无 LLM fallback
- [ ] 所有单元测试通过
- [ ] 与现有 Context 类无缝集成
