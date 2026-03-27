"""Rule-based context summarizer (fallback)."""

from __future__ import annotations

from coding_agent.summarizer.base import Summary


class RuleSummarizer:
    """Rule-based summarizer (fallback, no LLM needed).
    
    Provides basic summarization without requiring an LLM call.
    Useful as a fallback when LLM is unavailable or for simple cases.
    """
    
    async def summarize(
        self,
        messages: list[dict],
        max_tokens: int = 500,
    ) -> Summary:
        """Summarize using simple rules.
        
        Args:
            messages: List of message dicts to summarize
            max_tokens: Maximum tokens (not strictly enforced, for interface compatibility)
            
        Returns:
            Summary object with rule-generated summary
        """
        if not messages:
            return Summary(
                content="No messages to summarize.",
                original_tokens=0,
                summary_tokens=0,
                key_points=[],
            )
        
        # Extract system messages (usually contain task goal)
        system_msgs = [m for m in messages if m.get("role") == "system"]
        
        # Extract tool calls (actual actions taken)
        tool_calls = [
            m for m in messages 
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        
        # Extract user messages
        user_msgs = [m for m in messages if m.get("role") == "user"]
        
        # Extract tool results
        tool_results = [m for m in messages if m.get("role") == "tool"]
        
        # Build summary parts
        summary_parts = ["**Conversation Summary**"]
        
        # Add task from first system message
        if system_msgs:
            task = system_msgs[0].get("content", "")
            if len(task) > 100:
                task = task[:100] + "..."
            summary_parts.append(f"**Task**: {task}")
        
        # Count various message types
        summary_parts.append(f"**Total Messages**: {len(messages)}")
        summary_parts.append(f"**User Queries**: {len(user_msgs)} interactions")
        summary_parts.append(f"**Tool Calls**: {len(tool_calls)} actions")
        summary_parts.append(f"**Tool Results**: {len(tool_results)} results")
        
        # List tool names if available
        if tool_calls:
            tool_names = set()
            for msg in tool_calls:
                for tc in msg.get("tool_calls", []):
                    name = tc.get("function", {}).get("name", "")
                    if name:
                        tool_names.add(name)
            if tool_names:
                summary_parts.append(f"**Tools Used**: {', '.join(sorted(tool_names))}")
        
        summary_text = "\n".join(summary_parts)
        
        return Summary(
            content=summary_text,
            original_tokens=self._count_tokens(messages),
            summary_tokens=self._count_tokens([{"content": summary_text}]),
            key_points=summary_parts[1:],  # Exclude the header
        )
    
    def _count_tokens(self, messages: list[dict]) -> int:
        """Estimate token count.
        
        Args:
            messages: Messages to count tokens for
            
        Returns:
            Estimated token count
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            # Rough estimation: 4 chars per token
            total += len(content) // 4
            # Add overhead for message structure
            total += 4
        return total
