"""Context: assemble LLM-ready messages from tape entries."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from coding_agent.core.planner import PlanManager
from coding_agent.core.tape import Entry, Tape
from coding_agent.summarizer.base import Summary, Summarizer
from coding_agent.summarizer.llm_summarizer import LLMSummarizer
from coding_agent.summarizer.rule_summarizer import RuleSummarizer

if TYPE_CHECKING:
    from coding_agent.kb import KB, KBSearchResult
    from coding_agent.providers.base import ChatProvider
    from coding_agent.tokens import TokenCounter


# Maximum tokens allowed for a single tool result
MAX_TOOL_RESULT_TOKENS = 1000


logger = logging.getLogger(__name__)


class Context:
    """Builds a working set of messages from tape entries.

    Strategy for P0 (basic):
    1. Find the most recent anchor → start from there
    2. Convert entries to OpenAI-format messages
    3. Exclude event entries (not useful for LLM reasoning)
    4. Prepend system prompt
    5. Enforce max_tokens budget (approximate, ~4 chars per token)
    
    Strategy for P2 (summarization):
    - When context exceeds threshold, summarize old messages
    - Keep recent messages complete
    - Cache summaries to avoid regeneration
    """

    # Approximate chars per token for budget estimation
    CHARS_PER_TOKEN = 4
    
    # Summarization constants
    SUMMARIZE_THRESHOLD = 0.8  # Trigger summarization at 80% of budget
    KEEP_RECENT = 5  # Number of recent messages to keep un-summarized
    SUMMARY_MAX_TOKENS = 300  # Max tokens for summary
    MAX_SUMMARY_CACHE = 100  # Max number of summaries to cache

    def __init__(
        self,
        max_tokens: int,
        system_prompt: str,
        planner: PlanManager | None = None,
        token_counter: TokenCounter | None = None,
        kb: KB | None = None,
        summarizer: Summarizer | None = None,
    ):
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {max_tokens}")
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.planner = planner
        self._token_counter = token_counter
        self._kb = kb
        self._summarizer = summarizer
        self._summary_cache: OrderedDict[str, Summary] = OrderedDict()  # LRU cache for summaries
        self._max_chars = max_tokens * self.CHARS_PER_TOKEN

    def _estimate_tokens(self, text_or_messages: str | list[dict]) -> int:
        """Estimate token count from character count or message list.
        
        Args:
            text_or_messages: Either a text string or a list of message dicts
            
        Returns:
            Estimated token count
        """
        if isinstance(text_or_messages, str):
            return len(text_or_messages) // self.CHARS_PER_TOKEN
        
        # Handle list of messages
        if self._token_counter:
            return self._token_counter.count_messages(text_or_messages)
        
        # Fallback estimation
        total = 0
        for msg in text_or_messages:
            content = msg.get("content", "")
            total += len(content) // self.CHARS_PER_TOKEN
            total += 4  # Message framing overhead
        return total
    
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

    def _compute_cache_key(self, messages: list[dict]) -> str:
        """Compute cache key for messages.
        
        Args:
            messages: List of messages to compute key for
            
        Returns:
            Hash string representing the messages
        """
        content = "\n".join(
            f"{m.get('role')}:{m.get('content', '')[:50]}"
            for m in messages
        )
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _message_to_text(self, message: dict[str, Any]) -> str:
        """Extract text content from message for token estimation."""
        parts = []
        if message.get("content"):
            parts.append(str(message["content"]))
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                if tc.get("function", {}).get("name"):
                    parts.append(tc["function"]["name"])
                if tc.get("function", {}).get("arguments"):
                    parts.append(str(tc["function"]["arguments"]))
        if message.get("tool_call_id"):
            parts.append(message["tool_call_id"])
        return " ".join(parts)

    async def build_working_set(
        self,
        tape: Tape,
        provider: ChatProvider | None = None,
    ) -> list[dict[str, Any]]:
        """Assemble LLM-ready messages from tape entries.
        
        Messages are truncated if they exceed max_tokens budget.
        System prompt is always preserved. Truncation removes oldest
        non-system messages first.
        
        When the context exceeds the threshold, old messages are summarized
        to fit within budget while preserving recent context.
        
        Args:
            tape: The tape to build working set from
            provider: Optional provider for lazy summarizer initialization
            
        Returns:
            List of messages ready for LLM consumption
        """
        # System prompt always first
        system_msg = {"role": "system", "content": self.system_prompt}
        current_chars = len(self.system_prompt)

        # Inject plan if present and non-empty
        plan_msg = None
        if self.planner and self.planner.tasks:
            plan_text = f"[Current Plan]\n{self.planner.to_text()}"
            plan_msg = {"role": "system", "content": plan_text}
            current_chars += len(plan_text)

        # Find the last anchor to start from
        entries = tape.entries()
        start_idx = 0
        for i in range(len(entries) - 1, -1, -1):
            if entries[i].kind == "anchor":
                start_idx = i
                break

        # Convert entries to messages, tracking token budget
        new_messages: list[dict[str, Any]] = []
        for entry in entries[start_idx:]:
            msg = self._entry_to_message(entry)
            if msg is not None:
                msg_text = self._message_to_text(msg)
                msg_chars = len(msg_text)
                
                # Check if adding this message would exceed budget
                if current_chars + msg_chars > self._max_chars:
                    # Budget exceeded - truncate by removing oldest non-system messages
                    # until we can fit this one, or skip if it alone exceeds budget
                    if msg_chars > self._max_chars:
                        # This single message is too large - truncate its content
                        msg = self._truncate_message(msg, self._max_chars - current_chars)
                        if msg:
                            new_messages.append(msg)
                    else:
                        # Remove oldest messages to make room
                        max_iterations = len(new_messages) + 1  # Safety limit
                        iterations = 0
                        while (new_messages and 
                               current_chars + msg_chars > self._max_chars and 
                               iterations < max_iterations):
                            removed = new_messages.pop(0)
                            current_chars -= len(self._message_to_text(removed))
                            iterations += 1
                        new_messages.append(msg)
                        current_chars += msg_chars
                else:
                    new_messages.append(msg)
                    current_chars += msg_chars

        # Combine: system + plan (if present) + conversation messages
        result = [system_msg]
        if plan_msg:
            result.append(plan_msg)
        result.extend(new_messages)
        
        # Check if summarization is needed
        total_tokens = self._estimate_tokens(result)
        threshold_tokens = int(self.max_tokens * self.SUMMARIZE_THRESHOLD)
        
        if total_tokens > threshold_tokens:
            # Initialize summarizer if needed
            if provider and self._summarizer is None:
                self._summarizer = LLMSummarizer(provider)
            
            # Perform summarization
            result = await self._summarize_messages(result, provider)
        
        return result
    
    async def _summarize_messages(
        self,
        messages: list[dict[str, Any]],
        provider: ChatProvider | None,
    ) -> list[dict[str, Any]]:
        """Summarize old messages while keeping recent ones intact.
        
        Args:
            messages: Full list of messages
            provider: Optional provider for lazy initialization
            
        Returns:
            Messages with old content summarized
        """
        # If too few messages, don't summarize
        # +2 accounts for system prompt and plan message
        if len(messages) <= self.KEEP_RECENT + 2:
            return messages
        
        # Separate system messages, recent messages, and old messages
        system_messages = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        
        if len(non_system) <= self.KEEP_RECENT:
            return messages
        
        recent = non_system[-self.KEEP_RECENT:]
        old = non_system[:-self.KEEP_RECENT]
        
        # Check cache
        cache_key = self._compute_cache_key(old)
        if cache_key in self._summary_cache:
            summary = self._summary_cache[cache_key]
            # Update LRU order on cache hit
            self._summary_cache.move_to_end(cache_key)
        else:
            # Generate summary
            RETRYABLE_EXCEPTIONS = (
                ConnectionError,
                TimeoutError,
                RuntimeError,
                ValueError,
            )
            try:
                # Initialize summarizer if needed
                if self._summarizer is None and provider is not None:
                    self._summarizer = LLMSummarizer(provider)
                
                if self._summarizer:
                    summary = await self._summarizer.summarize(
                        old,
                        max_tokens=self.SUMMARY_MAX_TOKENS,
                    )
                else:
                    # Fallback to rule-based summarizer
                    fallback = RuleSummarizer()
                    summary = await fallback.summarize(old)
                
                # Cache the result with LRU eviction
                self._add_to_cache(cache_key, summary)
            except RETRYABLE_EXCEPTIONS as e:
                logger.warning(f"LLM summarization failed: {e}, using fallback")
                fallback = RuleSummarizer()
                summary = await fallback.summarize(old)
                self._add_to_cache(cache_key, summary)
            except Exception:
                # Unexpected error - log and re-raise
                logger.exception("Unexpected error in summarization")
                raise
        
        # Build new message list with summary
        summary_message = {
            "role": "system",
            "content": (
                f"[Previous Context Summary - "
                f"{summary.original_tokens}→{summary.summary_tokens} tokens]\n"
                f"{summary.content}"
            ),
        }
        
        # Result: system messages + summary + recent messages
        return system_messages + [summary_message] + recent

    def _truncate_message(self, message: dict[str, Any], max_chars: int) -> dict[str, Any] | None:
        """Truncate a message to fit within max_chars.
        
        Returns None if message cannot be truncated meaningfully.
        """
        if max_chars <= 0:
            return None
        
        # Truncate content if present
        if message.get("content") and len(str(message["content"])) > max_chars:
            truncated = str(message["content"])[:max_chars - 3] + "..."
            result = dict(message)
            result["content"] = truncated
            return result
        
        # Truncate tool result content
        if message.get("role") == "tool" and message.get("content"):
            content = str(message["content"])
            if len(content) > max_chars:
                truncated = content[:max_chars - 3] + "..."
                result = dict(message)
                result["content"] = truncated
                return result
        
        # Truncate tool_calls if present (can be very large)
        if message.get("tool_calls"):
            tool_calls = message["tool_calls"]
            tool_calls_str = json.dumps(tool_calls)
            if len(tool_calls_str) > max_chars:
                # Truncate arguments of each tool call
                truncated_calls = []
                remaining_chars = max_chars - 50  # Reserve space for structure
                for tc in tool_calls:
                    tc_copy = dict(tc)
                    func = tc_copy.get("function", {})
                    args = func.get("arguments", "")
                    if args and len(str(args)) > remaining_chars // len(tool_calls):
                        func["arguments"] = str(args)[:remaining_chars // len(tool_calls)] + "...[truncated]"
                    truncated_calls.append(tc_copy)
                result = dict(message)
                result["tool_calls"] = truncated_calls
                return result
        
        return message

    def _entry_to_message(self, entry: Entry) -> dict[str, Any] | None:
        match entry.kind:
            case "message":
                return {
                    "role": entry.payload["role"],
                    "content": entry.payload["content"],
                }
            case "anchor":
                state = entry.payload.get("state", {})
                name = entry.payload.get("name", "checkpoint")
                summary = state.get("summary", f"Phase: {name}")
                return {
                    "role": "system",
                    "content": f"[Checkpoint: {name}] {summary}",
                }
            case "tool_call":
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": entry.payload["call_id"],
                            "type": "function",
                            "function": {
                                "name": entry.payload["tool"],
                                "arguments": json.dumps(
                                    entry.payload["args"]
                                ),
                            },
                        }
                    ],
                }
            case "tool_result":
                return {
                    "role": "tool",
                    "tool_call_id": entry.payload["call_id"],
                    "content": entry.payload["result"],
                }
            case "event":
                return None  # Events excluded from LLM context
            case _:
                return None


    # KB Integration methods (P3)

    def _ensure_kb(self) -> KB | None:
        """Lazy initialize KB if needed.
        
        Note: This is a placeholder for P3 KB integration. Currently
        always returns None as KB is not fully integrated. The full
        implementation will add configuration-based initialization.
        
        Returns:
            KB instance or None if not available
        """
        if self._kb is not None:
            return self._kb
        
        # Lazy import to avoid loading heavy dependencies at startup
        from coding_agent.kb import KB
        
        # TODO: Initialize KB with proper configuration
        # For now, return None to skip KB operations
        return None

    async def search_knowledge_base(self, query: str, k: int = 5) -> list[KBSearchResult]:
        """Search the knowledge base for relevant context.
        
        Args:
            query: Search query string
            k: Number of results to return
            
        Returns:
            List of search results with relevance scores
        """
        kb = self._ensure_kb()
        if kb is None:
            return []
        return await kb.search(query, k=k)

    async def add_kb_context_to_working_set(
        self,
        working_set: list[dict[str, Any]],
        search_results: list[KBSearchResult],
        max_tokens: int = 2000,
    ) -> None:
        """Add KB search results as context to the working set.
        
        Args:
            working_set: The message list to add context to
            search_results: KB search results to include
            max_tokens: Maximum tokens for KB context
        """
        if not search_results:
            return

        # Format search results into a context message
        context_lines = ["[Knowledge Base Context]"]
        current_tokens = 0

        for i, result in enumerate(search_results, 1):
            chunk = result.chunk
            score = result.score
            
            # Estimate tokens for this result
            result_text = f"\n--- Result {i} (score: {score:.3f}) ---\n"
            result_text += f"Source: {chunk.source}\n"
            result_text += f"Content:\n{chunk.content}\n"
            
            if self._token_counter:
                result_tokens = self._token_counter.count(result_text)
            else:
                result_tokens = len(result_text) // self.CHARS_PER_TOKEN
            
            if current_tokens + result_tokens > max_tokens:
                context_lines.append(f"\n... ({len(search_results) - i + 1} more results omitted)")
                break
            
            context_lines.append(result_text)
            current_tokens += result_tokens

        if len(context_lines) > 1:  # Only add if we have actual results
            kb_message = {
                "role": "system",
                "content": "\n".join(context_lines),
            }
            # Insert after system prompt (position 1)
            working_set.insert(1, kb_message)
