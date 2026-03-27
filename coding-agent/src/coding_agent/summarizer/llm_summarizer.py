"""LLM-based context summarizer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from coding_agent.summarizer.base import Summary

if TYPE_CHECKING:
    from coding_agent.providers.base import ChatProvider


class LLMSummarizer:
    """LLM-based context summarizer.
    
    Uses a ChatProvider to generate intelligent summaries of conversation history.
    """
    
    def __init__(self, provider: ChatProvider):
        """Initialize with a chat provider.
        
        Args:
            provider: The chat provider to use for summarization
        """
        self.provider = provider
    
    async def summarize(
        self,
        messages: list[dict],
        max_tokens: int = 500,
    ) -> Summary:
        """Summarize messages using LLM.
        
        Args:
            messages: List of message dicts to summarize
            max_tokens: Maximum tokens for the summary
            
        Returns:
            Summary object with the generated summary
        """
        if not messages:
            return Summary(
                content="No messages to summarize.",
                original_tokens=0,
                summary_tokens=0,
                key_points=[],
            )
        
        # Build the summary prompt
        prompt = self._build_summary_prompt(messages)
        
        # Call LLM for summarization
        summary_text = await self._call_llm(prompt, max_tokens)
        
        # Extract key points
        key_points = self._extract_key_points(summary_text)
        
        return Summary(
            content=summary_text,
            original_tokens=self._count_tokens(messages),
            summary_tokens=self._count_tokens([{"content": summary_text}]),
            key_points=key_points,
        )
    
    def _build_summary_prompt(self, messages: list[dict]) -> str:
        """Build prompt for summarization.
        
        Args:
            messages: Messages to include in the prompt
            
        Returns:
            Formatted prompt string
        """
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
        """Format messages for prompt.
        
        Args:
            messages: Messages to format
            
        Returns:
            Formatted conversation string
        """
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # Truncate very long content
            if len(content) > 200:
                content = content[:200] + "..."
            
            # Include tool call info for assistant messages
            if role == "assistant" and msg.get("tool_calls"):
                tool_names = [tc.get("function", {}).get("name", "unknown") 
                             for tc in msg["tool_calls"]]
                content = f"{content} [tools: {', '.join(tool_names)}]"
            
            lines.append(f"{role}: {content}")
        return "\n".join(lines)
    
    async def _call_llm(self, prompt: str, max_tokens: int) -> str:
        """Call LLM for summarization.
        
        Args:
            prompt: The prompt to send
            max_tokens: Maximum tokens for response
            
        Returns:
            Generated summary text
        """
        summary_messages = [
            {"role": "system", "content": "You are a helpful assistant that summarizes conversations concisely."},
            {"role": "user", "content": prompt},
        ]
        
        result = []
        async for event in self.provider.stream(summary_messages):
            if event.text:
                result.append(event.text)
        
        return "".join(result) or "No summary generated."
    
    def _extract_key_points(self, summary: str) -> list[str]:
        """Extract key points from summary.
        
        Args:
            summary: The summary text
            
        Returns:
            List of extracted key points
        """
        points = []
        for line in summary.split("\n"):
            line = line.strip()
            if line.startswith(("**", "- ", "• ", "* ")):
                # Remove markdown markers
                point = line.lstrip("*•- ").strip()
                if point:
                    points.append(point)
        return points if points else [summary[:100] + "..." if len(summary) > 100 else summary]
    
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
