# 任务 3.3: 上下文智能摘要（可选）- 详细实现方案

## 需求规格

1. 当上下文超过 80% token 预算时触发
2. 使用 LLM 将历史消息摘要为关键点
3. 保留：任务目标、重要决策、待办事项

---

## 实现方案

```python
# src/coding_agent/core/context.py

class Context:
    """Context with intelligent summarization."""
    
    async def build_working_set(
        self,
        tape: Tape,
        provider: ChatProvider | None = None,
    ) -> list[dict]:
        """Build working set with auto-summarization."""
        messages = self._build_basic(tape)
        
        # Check if we need summarization
        total_tokens = sum(
            self._estimate_tokens(m.get("content", ""))
            for m in messages
        )
        
        if total_tokens > self.max_tokens * 0.8 and provider:
            # Trigger summarization
            messages = await self._summarize_history(messages, provider)
        
        return messages
    
    async def _summarize_history(
        self,
        messages: list[dict],
        provider: ChatProvider,
    ) -> list[dict]:
        """Summarize old messages."""
        # Keep recent messages
        keep_recent = 5
        recent = messages[-keep_recent:]
        old = messages[:-keep_recent]
        
        # Create summary prompt
        summary_prompt = """Summarize the following conversation history into key points:
- What was the original task/goal?
- What important decisions were made?
- What are the pending TODOs?

Conversation:
{conversation}

Provide a concise summary:"""
        
        conversation_text = "\n".join(
            f"{m['role']}: {m.get('content', '')[:200]}"
            for m in old
        )
        
        # Call LLM for summary
        summary_messages = [
            {"role": "system", "content": "You are a helpful assistant that summarizes conversations."},
            {"role": "user", "content": summary_prompt.format(conversation=conversation_text)},
        ]
        
        # Get summary (non-streaming)
        summary = ""
        async for event in provider.stream(summary_messages):
            if event.text:
                summary += event.text
        
        # Return summary + recent messages
        return [
            {"role": "system", "content": f"[Previous context summary]\n{summary}"},
            *recent,
        ]
```

---

## 预估工作量

| 任务 | 时间 |
|------|------|
| 摘要逻辑实现 | 1 hour |
| 提示词优化 | 1 hour |
| 集成测试 | 1 hour |
| **总计** | **~3 hours** |

---

## 说明

此任务为 **可选**，因为：
1. 实现复杂度高
2. 需要额外的 API 调用（成本）
3. 摘要质量难以保证

建议在其他任务完成后再考虑实现。
