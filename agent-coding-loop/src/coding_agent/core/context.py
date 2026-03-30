"""Context management for conversation history and token budget."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from coding_agent.tokens import ApproximateCounter, TokenCounter

if TYPE_CHECKING:
    from coding_agent.kb import KB, KBSearchResult

# Maximum tokens allowed for a single tool result
MAX_TOOL_RESULT_TOKENS = 1000


class PlanManager:
    """Manages the execution plan for the agent.
    
    This is a placeholder implementation. In a real scenario, this would
    manage task planning, execution order, and progress tracking.
    """

    def __init__(self) -> None:
        """Initialize the plan manager."""
        self.tasks: list[dict[str, Any]] = []
        self.current_task: dict[str, Any] | None = None

    def add_task(self, description: str, **kwargs: Any) -> None:
        """Add a new task to the plan.
        
        Args:
            description: Task description.
            **kwargs: Additional task metadata.
        """
        task = {"description": description, **kwargs}
        self.tasks.append(task)

    def get_current_task(self) -> dict[str, Any] | None:
        """Get the currently active task.
        
        Returns:
            The current task or None if no task is active.
        """
        return self.current_task


class Context:
    """Manages conversation context with token budget constraints.
    
    This class tracks messages, tool results, and ensures that the total
    context stays within the specified token budget by truncating
    long tool results when necessary.
    """

    def __init__(
        self,
        max_tokens: int,
        system_prompt: str,
        planner: PlanManager | None = None,
        token_counter: TokenCounter | None = None,
        kb: KB | None = None,
    ):
        """Initialize the context manager.
        
        Args:
            max_tokens: Maximum total tokens allowed in context.
            system_prompt: The system prompt to include in messages.
            planner: Optional plan manager for task tracking.
            token_counter: Optional token counter implementation.
                         Defaults to ApproximateCounter if not provided.
            kb: Optional knowledge base for RAG search.
        """
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.planner = planner
        self.token_counter = token_counter or ApproximateCounter()
        self.kb = kb
        
        # Initialize message history with system prompt
        self._messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]
        
        # Store tool results separately for potential truncation
        self._tool_results: list[dict[str, Any]] = []

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation history.
        
        Args:
            role: Message role (e.g., 'user', 'assistant', 'system').
            content: Message content.
        """
        self._messages.append({"role": role, "content": content})

    def add_tool_result(self, tool_name: str, content: str) -> None:
        """Add a tool result to the context.
        
        Tool results may be truncated if they exceed the token budget.
        
        Args:
            tool_name: Name of the tool that was called.
            content: Tool output content.
        """
        self._tool_results.append({
            "tool_name": tool_name,
            "content": content,
        })

    def _truncate_tool_result(self, content: str, max_tokens: int) -> str:
        """Truncate tool result to fit within token budget.
        
        Preserves the beginning and end of the content, truncating the middle.
        
        Args:
            content: The tool result content to potentially truncate.
            max_tokens: Maximum tokens allowed for this content.
            
        Returns:
            Truncated content if it exceeds max_tokens, otherwise original content.
        """
        token_count = self.token_counter.count(content)
        
        if token_count <= max_tokens:
            return content
        
        # Calculate how many tokens we can keep (roughly half at start, half at end)
        # We reserve some tokens for the truncation marker
        marker = "\n...(truncated)\n"
        marker_tokens = self.token_counter.count(marker)
        
        # Tokens available for actual content (split between start and end)
        available_tokens = max_tokens - marker_tokens
        if available_tokens <= 0:
            # Edge case: max_tokens is smaller than marker
            # Just truncate to fit within max_tokens
            chars_to_keep = max_tokens * 4  # Approximate: 4 chars per token
            return content[:chars_to_keep]
        
        half_available = available_tokens // 2
        
        # Convert token counts to character counts (approximate)
        chars_per_token = 4
        start_chars = half_available * chars_per_token
        end_chars = (available_tokens - half_available) * chars_per_token
        
        # Ensure minimum viable truncation - if the truncation wouldn't save
        # enough tokens, just take start_chars from the beginning
        min_chars_to_remove = 100  # Minimum characters that should be removed
        if start_chars + end_chars + min_chars_to_remove >= len(content):
            # Content is too short for two-part truncation, just truncate from end
            return content[:start_chars] + marker
        
        start_part = content[:start_chars]
        end_part = content[-end_chars:] if end_chars > 0 else ""
        
        return f"{start_part}{marker}{end_part}"

    async def search_knowledge_base(
        self, 
        query: str, 
        k: int = 5
    ) -> list[KBSearchResult]:
        """Search the knowledge base for relevant information.
        
        Args:
            query: The search query.
            k: Number of results to return. Defaults to 5.
            
        Returns:
            List of search results sorted by relevance.
            Returns empty list if no KB is configured.
        """
        if self.kb is None:
            return []
        return await self.kb.search(query, k=k)

    def add_kb_context_to_working_set(
        self, 
        working_set: list[dict],
        search_results: list[KBSearchResult],
        max_tokens: int = 2000,
    ) -> None:
        """Add knowledge base search results to the working set.
        
        Formats the search results and adds them as a system message
        to provide context to the LLM. The content is truncated if
        it exceeds the specified token limit.
        
        Args:
            working_set: The list of messages to add KB context to.
            search_results: The KB search results to include.
            max_tokens: Maximum tokens for the KB context content.
        """
        if not search_results:
            return
        
        # Format the search results
        lines = ["# Knowledge Base Context", ""]
        
        for i, result in enumerate(search_results, 1):
            lines.append(f"## Result {i} (score: {result.score:.4f})")
            lines.append(f"Source: {result.chunk.source}")
            lines.append("```")
            lines.append(result.chunk.content)
            lines.append("```")
            lines.append("")
        
        content = "\n".join(lines)
        
        # Truncate if necessary
        truncated_content = self._truncate_tool_result(content, max_tokens)
        
        # Add as a system message
        working_set.append({
            "role": "system",
            "content": truncated_content,
        })

    def build_working_set(
        self,
        kb_results: list[KBSearchResult] | None = None,
    ) -> list[dict[str, str]]:
        """Build the working set of messages for the LLM.
        
        This method constructs the final message list, applying truncation
        to tool results that exceed the MAX_TOOL_RESULT_TOKENS limit.
        Optionally includes knowledge base search results as context.
        
        Args:
            kb_results: Optional KB search results to include as context.
            
        Returns:
            List of messages ready to be sent to the LLM.
        """
        messages = self._messages.copy()
        
        # Add KB context if provided
        if kb_results:
            self.add_kb_context_to_working_set(messages, kb_results)
        
        # Process and add tool results as assistant messages
        for tool_result in self._tool_results:
            content = tool_result["content"]
            tool_name = tool_result["tool_name"]
            
            # Truncate if necessary
            truncated_content = self._truncate_tool_result(
                content, MAX_TOOL_RESULT_TOKENS
            )
            
            # Format as a tool result message
            formatted_content = f"Tool '{tool_name}' result:\n{truncated_content}"
            messages.append({"role": "assistant", "content": formatted_content})
        
        return messages

    def get_token_count(self, kb_results: list[KBSearchResult] | None = None) -> int:
        """Get the current token count of the working set.
        
        Args:
            kb_results: Optional KB search results to include in the count.
            
        Returns:
            Total token count of messages including processed tool results.
        """
        working_set = self.build_working_set(kb_results=kb_results)
        return self.token_counter.count_messages(working_set)

    def clear_tool_results(self) -> None:
        """Clear all stored tool results.
        
        This is useful when starting a new turn or when tool results
        should no longer be included in the context.
        """
        self._tool_results.clear()

    def get_messages(self) -> list[dict[str, str]]:
        """Get a copy of the current message history (excluding tool results).
        
        Returns:
            Copy of the base message list.
        """
        return self._messages.copy()
