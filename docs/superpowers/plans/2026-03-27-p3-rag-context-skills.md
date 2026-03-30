# P3: RAG + Context Compaction + Skills — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add RAG knowledge base search, three-layer context compaction for long sessions, and a skill loader that injects SKILL.md documents as tool results on demand.

**Architecture:** KB search is a built-in tool that wraps the existing `kb/server.py` sidecar via HTTP. Context compaction adds selective pruning (Layer 2) and LLM-based summarization (Layer 3) on top of the existing anchor-based truncation (Layer 1). Skills are SKILL.md files loaded lazily — frontmatter parsed at startup, body injected via tool_result when invoked.

**Tech Stack:** Python 3.12+, uv, asyncio, httpx (KB client), tiktoken (token counting), PyYAML (skill frontmatter), pytest

**Spec:** `docs/superpowers/specs/2026-03-26-python-coding-agent-design.md` (Sections 4.13, 4.14, 7, 14-P3)

---

## File Map

```
coding-agent/
  pyproject.toml                              # Add tiktoken, pyyaml deps
  src/coding_agent/
    core/
      context.py                              # Upgrade: 3-layer compaction + token counter
      session.py                              # New: session lifecycle + persistence
    tools/
      kb.py                                   # New: kb_search tool (HTTP client to sidecar)
    skills/
      __init__.py                             # New: SkillLoader + SkillDef
      load_skill.py                           # New: register load_skill tool
  tests/
    core/
      test_context_compaction.py              # New: Layer 2 + Layer 3 tests
      test_session.py                         # New: session create/load/close
    tools/
      test_kb.py                              # New: kb_search tool tests
    skills/
      __init__.py
      test_skill_loader.py                    # New: skill loading + injection tests
```

---

## Task 1: Add Dependencies (tiktoken, pyyaml)

**Files:**
- Modify: `coding-agent/pyproject.toml`

- [ ] **Step 1: Add tiktoken and pyyaml to dependencies**

```toml
dependencies = [
    "openai>=1.50.0",
    "anthropic>=0.40.0",
    "httpx>=0.27.0",
    "pydantic>=2.0.0",
    "structlog>=24.0.0",
    "click>=8.0.0",
    "rich>=13.0.0",
    "prompt-toolkit>=3.0.0",
    "tiktoken>=0.7.0",
    "pyyaml>=6.0.0",
]
```

- [ ] **Step 2: Install updated dependencies**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv sync --all-extras
```

Expected: installs `tiktoken` and `pyyaml` successfully

- [ ] **Step 3: Commit**

```bash
git add coding-agent/pyproject.toml coding-agent/uv.lock
git commit -m "chore(p3): add tiktoken and pyyaml dependencies"
```

---

## Task 2: Token Counter

**Files:**
- Create: `coding-agent/src/coding_agent/core/tokens.py`
- Test: `coding-agent/tests/core/test_tokens.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_tokens.py`:

```python
"""Tests for token counting."""

from __future__ import annotations

import pytest

from coding_agent.core.tokens import (
    ApproximateCounter,
    TiktokenCounter,
    TokenCounter,
)


class TestTiktokenCounter:
    def test_count_short_string(self):
        counter = TiktokenCounter(model="gpt-4o")
        count = counter.count("Hello, world!")
        assert count > 0
        assert count < 20  # ~3-4 tokens

    def test_count_empty_string(self):
        counter = TiktokenCounter(model="gpt-4o")
        assert counter.count("") == 0

    def test_count_messages(self):
        counter = TiktokenCounter(model="gpt-4o")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        count = counter.count_messages(messages)
        assert count > 0

    def test_unknown_model_falls_back(self):
        counter = TiktokenCounter(model="unknown-model-xyz")
        count = counter.count("test string")
        assert count > 0  # Should fall back to cl100k_base


class TestApproximateCounter:
    def test_count_string(self):
        counter = ApproximateCounter()
        # "Hello, world!" = 13 chars, ~13/4 = 3 tokens
        count = counter.count("Hello, world!")
        assert count == 13 // 4

    def test_count_empty(self):
        counter = ApproximateCounter()
        assert counter.count("") == 0

    def test_count_messages(self):
        counter = ApproximateCounter()
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        count = counter.count_messages(messages)
        assert count > 0

    def test_messages_include_overhead(self):
        counter = ApproximateCounter()
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        count = counter.count_messages(messages)
        # More than just text content (includes role, overhead)
        text_only = counter.count("Hi") + counter.count("Hello")
        assert count > text_only
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_tokens.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`coding-agent/src/coding_agent/core/tokens.py`:

```python
"""Token counting for context budget management."""

from __future__ import annotations

import json
from typing import Any, Protocol


class TokenCounter(Protocol):
    """Protocol for token counters."""

    def count(self, text: str) -> int:
        """Count tokens in a text string."""
        ...

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        """Count tokens in a list of messages (includes per-message overhead)."""
        ...


class TiktokenCounter:
    """Accurate token counter using tiktoken (for OpenAI models)."""

    # Per-message overhead (role, separators)
    _MSG_OVERHEAD = 4

    def __init__(self, model: str = "gpt-4o"):
        import tiktoken

        try:
            self._enc = tiktoken.encoding_for_model(model)
        except KeyError:
            self._enc = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._enc.encode(text))

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            total += self._MSG_OVERHEAD
            if msg.get("content"):
                total += self.count(str(msg["content"]))
            if msg.get("role"):
                total += self.count(msg["role"])
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    if func.get("name"):
                        total += self.count(func["name"])
                    if func.get("arguments"):
                        total += self.count(str(func["arguments"]))
            if msg.get("tool_call_id"):
                total += self.count(msg["tool_call_id"])
        total += 2  # reply priming
        return total


class ApproximateCounter:
    """Fallback counter: 1 token ≈ 4 chars. For unknown models."""

    _CHARS_PER_TOKEN = 4
    _MSG_OVERHEAD = 4

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(text) // self._CHARS_PER_TOKEN

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            total += self._MSG_OVERHEAD
            if msg.get("content"):
                total += self.count(str(msg["content"]))
            if msg.get("role"):
                total += self.count(msg["role"])
            if msg.get("tool_calls"):
                total += self.count(json.dumps(msg["tool_calls"]))
            if msg.get("tool_call_id"):
                total += self.count(msg["tool_call_id"])
        total += 2
        return total


def make_counter(model: str) -> TokenCounter:
    """Create appropriate counter for a model name."""
    try:
        return TiktokenCounter(model=model)
    except Exception:
        return ApproximateCounter()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_tokens.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/tokens.py coding-agent/tests/core/test_tokens.py
git commit -m "feat(p3): add token counter with tiktoken and approximate fallback"
```

---

## Task 3: Context Compaction — Layer 2 (Selective Pruning)

**Files:**
- Modify: `coding-agent/src/coding_agent/core/context.py`
- Create: `coding-agent/tests/core/test_context_compaction.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_context_compaction.py`:

```python
"""Tests for context compaction layers."""

from __future__ import annotations

import json

import pytest

from coding_agent.core.context import Context
from coding_agent.core.planner import PlanManager
from coding_agent.core.tape import Entry, Tape
from coding_agent.core.tokens import ApproximateCounter


class TestLayer2SelectivePruning:
    """Layer 2: prune low-value entries within current anchor window."""

    def _make_context(self, max_tokens: int = 500) -> Context:
        counter = ApproximateCounter()
        return Context(
            max_tokens=max_tokens,
            system_prompt="sys",
            token_counter=counter,
        )

    def test_duplicate_file_reads_pruned(self):
        """Consecutive file_read for same file — keep only the latest."""
        ctx = self._make_context(max_tokens=200)
        tape = Tape()

        tape.append(Entry.message("user", "Read foo.py"))
        # First read of foo.py
        tape.append(Entry.tool_call("c1", "file_read", {"path": "foo.py"}))
        tape.append(Entry.tool_result("c1", json.dumps({"content": "old content " * 20})))
        # Second read of foo.py (after edit)
        tape.append(Entry.tool_call("c2", "file_read", {"path": "foo.py"}))
        tape.append(Entry.tool_result("c2", json.dumps({"content": "new content"})))
        tape.append(Entry.message("assistant", "Done"))

        messages = ctx.build_working_set(tape)
        # The first file_read result should be pruned (summarized)
        tool_results = [m for m in messages if m.get("role") == "tool"]
        # Should have 2 tool results but first one should be summarized
        full_results = [m for m in tool_results if "old content" in str(m.get("content", ""))]
        assert len(full_results) == 0  # old content pruned

    def test_error_tool_results_summarized(self):
        """Tool results with errors — keep only error message, prune full output."""
        ctx = self._make_context(max_tokens=200)
        tape = Tape()

        tape.append(Entry.message("user", "Run tests"))
        tape.append(Entry.tool_call("c1", "bash", {"command": "go test"}))
        # Large error output
        error_output = json.dumps({"error": "test failed", "output": "x" * 500})
        tape.append(Entry.tool_result("c1", error_output))
        tape.append(Entry.message("assistant", "Tests failed"))

        messages = ctx.build_working_set(tape)
        tool_results = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_results) == 1
        # Error result should be present but truncated
        result_content = str(tool_results[0].get("content", ""))
        assert len(result_content) < len(error_output)

    def test_events_excluded(self):
        """Event entries should not appear in working set."""
        ctx = self._make_context()
        tape = Tape()

        tape.append(Entry.message("user", "Hi"))
        tape.append(Entry.event("cache_hit", {"tool": "file_read"}))
        tape.append(Entry.message("assistant", "Hello"))

        messages = ctx.build_working_set(tape)
        non_system = [m for m in messages if m["role"] != "system"]
        assert len(non_system) == 2  # user + assistant, no event

    def test_recent_entries_preserved(self):
        """The last N entries should never be pruned."""
        ctx = self._make_context(max_tokens=100)
        tape = Tape()

        tape.append(Entry.message("user", "Start"))
        # Add many entries to exceed budget
        for i in range(10):
            tape.append(Entry.tool_call(f"c{i}", "file_read", {"path": f"file{i}.py"}))
            tape.append(Entry.tool_result(f"c{i}", "x" * 50))
        tape.append(Entry.message("assistant", "Latest response"))

        messages = ctx.build_working_set(tape)
        # Last assistant message should always be present
        non_system = [m for m in messages if m["role"] != "system"]
        assert any("Latest response" in str(m.get("content", "")) for m in non_system)


class TestLayer1AnchorTruncation:
    """Layer 1: entries before last anchor are dropped."""

    def test_anchor_truncation(self):
        counter = ApproximateCounter()
        ctx = Context(max_tokens=1000, system_prompt="sys", token_counter=counter)
        tape = Tape()

        tape.append(Entry.message("user", "old message"))
        tape.append(Entry.message("assistant", "old response"))
        tape.handoff("phase_2", {"summary": "Phase 1 done: read 3 files"})
        tape.append(Entry.message("user", "new message"))
        tape.append(Entry.message("assistant", "new response"))

        messages = ctx.build_working_set(tape)
        non_system = [m for m in messages if m["role"] != "system"]
        # old message/response should be gone; anchor + new messages present
        contents = " ".join(str(m.get("content", "")) for m in non_system)
        assert "old message" not in contents
        assert "new message" in contents
        assert "Phase 1 done" in " ".join(str(m.get("content", "")) for m in messages)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_context_compaction.py -v
```

Expected: FAIL — `Context` doesn't accept `token_counter` param yet

- [ ] **Step 3: Modify context.py to add Layer 2 pruning**

Update `coding-agent/src/coding_agent/core/context.py`:

Add `token_counter` parameter to `__init__`:

```python
from coding_agent.core.tokens import ApproximateCounter, TokenCounter
```

Replace the `__init__` signature:

```python
def __init__(
    self,
    max_tokens: int,
    system_prompt: str,
    planner: PlanManager | None = None,
    token_counter: TokenCounter | None = None,
):
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be positive, got {max_tokens}")
    self.max_tokens = max_tokens
    self.system_prompt = system_prompt
    self.planner = planner
    self._counter: TokenCounter = token_counter or ApproximateCounter()
    self._max_chars = max_tokens * self.CHARS_PER_TOKEN  # Keep for backward compat
```

Replace `_estimate_tokens` to use the counter:

```python
def _estimate_tokens(self, text: str) -> int:
    return self._counter.count(text)
```

Add pruning method before `build_working_set`:

```python
def _prune_entries(self, entries: list[Entry]) -> list[Entry]:
    """Layer 2: Selective pruning within current anchor window.

    - Deduplicate: consecutive file_read for same file → keep latest
    - Summarize: error tool results → keep only error key
    - Exclude: event entries (already handled by _entry_to_message)
    """
    if len(entries) <= 6:
        return entries  # Too few to prune

    # Track latest file_read per path
    latest_read: dict[str, int] = {}  # path → index in entries
    for i, entry in enumerate(entries):
        if (
            entry.kind == "tool_call"
            and entry.payload.get("tool") == "file_read"
        ):
            path = entry.payload.get("args", {}).get("path", "")
            if path:
                latest_read[path] = i

    # Build set of stale tool_call indices and their corresponding results
    stale_call_ids: set[str] = set()
    for path, latest_idx in latest_read.items():
        for i, entry in enumerate(entries):
            if (
                i < latest_idx
                and entry.kind == "tool_call"
                and entry.payload.get("tool") == "file_read"
                and entry.payload.get("args", {}).get("path") == path
            ):
                stale_call_ids.add(entry.payload.get("call_id", ""))

    pruned: list[Entry] = []
    for entry in entries:
        if entry.kind == "tool_call" and entry.payload.get("call_id") in stale_call_ids:
            # Replace stale file_read call with summary
            pruned.append(Entry.tool_call(
                entry.payload["call_id"],
                entry.payload["tool"],
                entry.payload["args"],
                id=entry.id,
            ))
            continue
        if entry.kind == "tool_result" and entry.payload.get("call_id") in stale_call_ids:
            # Summarize stale file_read result
            pruned.append(Entry.tool_result(
                entry.payload["call_id"],
                "[pruned: earlier read of same file]",
                id=entry.id,
            ))
            continue
        if entry.kind == "tool_result":
            # Summarize large error results
            result_str = str(entry.payload.get("result", ""))
            if len(result_str) > 500 and '"error"' in result_str:
                try:
                    parsed = json.loads(result_str)
                    if "error" in parsed:
                        summary = json.dumps({"error": parsed["error"]})
                        pruned.append(Entry.tool_result(
                            entry.payload["call_id"],
                            summary,
                            id=entry.id,
                        ))
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
        pruned.append(entry)
    return pruned
```

Update `build_working_set` to call `_prune_entries` on the entries slice:

In the `build_working_set` method, after `entries[start_idx:]` is determined, add:

```python
# Layer 2: Selective pruning
window_entries = self._prune_entries(entries[start_idx:])
```

Then iterate over `window_entries` instead of `entries[start_idx:]`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_context_compaction.py tests/core/test_context.py tests/core/test_context_limits.py -v
```

Expected: ALL pass (new + existing context tests)

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/context.py coding-agent/tests/core/test_context_compaction.py
git commit -m "feat(p3): add Layer 2 selective pruning to context compaction"
```

---

## Task 4: Context Compaction — Layer 3 (LLM Summarization)

**Files:**
- Modify: `coding-agent/src/coding_agent/core/context.py`
- Modify: `coding-agent/tests/core/test_context_compaction.py`

- [ ] **Step 1: Write the failing tests**

Add to `coding-agent/tests/core/test_context_compaction.py`:

```python
class TestLayer3LLMSummarization:
    """Layer 3: LLM-based summarization when still over budget after pruning."""

    @pytest.mark.asyncio
    async def test_summarization_creates_synthetic_anchor(self):
        """When over budget, summarize old entries into a synthetic anchor."""
        counter = ApproximateCounter()
        ctx = Context(
            max_tokens=50,  # Very small budget to force summarization
            system_prompt="sys",
            token_counter=counter,
        )

        # Mock summarizer
        async def mock_summarize(entries_text: str) -> str:
            return "Summary: read files and ran tests"

        ctx.set_summarizer(mock_summarize)

        tape = Tape()
        # Fill tape with enough entries to exceed budget
        for i in range(15):
            tape.append(Entry.message("user", f"Step {i}: " + "x" * 30))
            tape.append(Entry.message("assistant", f"Result {i}: " + "y" * 30))
        tape.append(Entry.message("user", "Final question"))

        messages = ctx.build_working_set(tape)
        # Should have a synthetic anchor with summary
        system_msgs = [m for m in messages if m["role"] == "system"]
        system_text = " ".join(str(m.get("content", "")) for m in system_msgs)
        assert "Summary" in system_text or "Final question" in " ".join(
            str(m.get("content", "")) for m in messages
        )

    @pytest.mark.asyncio
    async def test_no_summarization_when_under_budget(self):
        """No summarization call when entries fit in budget."""
        counter = ApproximateCounter()
        ctx = Context(
            max_tokens=10000,  # Large budget
            system_prompt="sys",
            token_counter=counter,
        )

        summarize_called = False

        async def mock_summarize(entries_text: str) -> str:
            nonlocal summarize_called
            summarize_called = True
            return "should not be called"

        ctx.set_summarizer(mock_summarize)

        tape = Tape()
        tape.append(Entry.message("user", "Hello"))
        tape.append(Entry.message("assistant", "Hi"))

        ctx.build_working_set(tape)
        assert not summarize_called

    @pytest.mark.asyncio
    async def test_summarization_preserves_recent_entries(self):
        """Recent entries should be preserved even after summarization."""
        counter = ApproximateCounter()
        ctx = Context(
            max_tokens=60,
            system_prompt="sys",
            token_counter=counter,
        )

        async def mock_summarize(entries_text: str) -> str:
            return "Summarized earlier work"

        ctx.set_summarizer(mock_summarize)

        tape = Tape()
        for i in range(20):
            tape.append(Entry.message("user", f"old {i} " + "x" * 20))
            tape.append(Entry.message("assistant", f"old reply {i}"))
        tape.append(Entry.message("user", "FINAL_QUESTION"))

        messages = ctx.build_working_set(tape)
        all_content = " ".join(str(m.get("content", "")) for m in messages)
        assert "FINAL_QUESTION" in all_content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_context_compaction.py::TestLayer3LLMSummarization -v
```

Expected: FAIL — `set_summarizer` not defined

- [ ] **Step 3: Add Layer 3 summarization to context.py**

Add to the `Context` class:

```python
def set_summarizer(
    self,
    summarizer: Callable[[str], Awaitable[str]] | None,
) -> None:
    """Set an async function for LLM-based summarization (Layer 3).

    The function receives a text representation of entries to summarize
    and returns a summary string.
    """
    self._summarizer = summarizer
```

Add `_summarizer` initialization in `__init__`:

```python
self._summarizer: Callable[[str], Awaitable[str]] | None = None
```

Add import at top:

```python
from typing import Any, Awaitable, Callable
```

Update `build_working_set` to attempt Layer 3 when over budget. After building `new_messages`, before the return statement, add:

```python
# Layer 3: LLM summarization if still over budget
if self._summarizer and new_messages:
    total_tokens = self._counter.count_messages([system_msg] + ([plan_msg] if plan_msg else []) + new_messages)
    if total_tokens > self.max_tokens:
        new_messages = self._apply_layer3_summarization(new_messages)
```

Add the helper method:

```python
def _apply_layer3_summarization(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Layer 3: Summarize oldest messages to fit budget.

    Keeps the last PRESERVE_RECENT messages intact and summarizes the rest.
    This is synchronous because build_working_set is sync — the summarizer
    is called in a blocking fashion via asyncio.
    """
    import asyncio

    PRESERVE_RECENT = 6  # Keep last 6 messages (~3 exchanges)

    if len(messages) <= PRESERVE_RECENT:
        return messages

    to_summarize = messages[:-PRESERVE_RECENT]
    to_keep = messages[-PRESERVE_RECENT:]

    # Build text representation of entries to summarize
    parts = []
    for msg in to_summarize:
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))[:200]
        if msg.get("tool_calls"):
            tool_names = [tc.get("function", {}).get("name", "?") for tc in msg["tool_calls"]]
            parts.append(f"[{role}] called tools: {', '.join(tool_names)}")
        elif role == "tool":
            call_id = msg.get("tool_call_id", "")
            parts.append(f"[tool result {call_id}] {content[:100]}")
        else:
            parts.append(f"[{role}] {content}")
    entries_text = "\n".join(parts)

    # Call summarizer
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context — create task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                summary = pool.submit(
                    asyncio.run, self._summarizer(entries_text)
                ).result(timeout=30)
        else:
            summary = asyncio.run(self._summarizer(entries_text))
    except Exception:
        # If summarization fails, fall back to simple truncation
        return to_keep

    # Create synthetic anchor message
    anchor_msg = {
        "role": "system",
        "content": f"[Context Summary] {summary}",
    }
    return [anchor_msg] + to_keep
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_context_compaction.py tests/core/test_context.py tests/core/test_context_limits.py -v
```

Expected: ALL pass

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/context.py coding-agent/tests/core/test_context_compaction.py
git commit -m "feat(p3): add Layer 3 LLM-based summarization to context compaction"
```

---

## Task 5: KB Search Tool

**Files:**
- Create: `coding-agent/src/coding_agent/tools/kb.py`
- Create: `coding-agent/tests/tools/test_kb.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/tools/test_kb.py`:

```python
"""Tests for KB search tool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from coding_agent.tools.kb import KBClient, register_kb_tools
from coding_agent.tools.registry import ToolRegistry


class TestKBClient:
    @pytest.mark.asyncio
    async def test_search_returns_hits(self):
        client = KBClient(base_url="http://localhost:9100")
        mock_response = {
            "hits": [
                {
                    "path": "docs/api.md",
                    "heading": "Authentication",
                    "text": "Use Bearer token for auth",
                    "score": 0.95,
                },
                {
                    "path": "docs/config.md",
                    "heading": "Settings",
                    "text": "Configure via env vars",
                    "score": 0.82,
                },
            ]
        }

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = AsyncMock()
            mock_post.return_value = mock_resp

            hits = await client.search("authentication", top_k=5)
            assert len(hits) == 2
            assert hits[0]["path"] == "docs/api.md"
            assert hits[0]["score"] == 0.95

    @pytest.mark.asyncio
    async def test_search_empty_result(self):
        client = KBClient(base_url="http://localhost:9100")

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"hits": []}
            mock_resp.raise_for_status = AsyncMock()
            mock_post.return_value = mock_resp

            hits = await client.search("nonexistent topic", top_k=5)
            assert hits == []

    @pytest.mark.asyncio
    async def test_search_handles_server_error(self):
        client = KBClient(base_url="http://localhost:9100")

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.side_effect = Exception("Connection refused")

            hits = await client.search("test", top_k=5)
            assert hits == []

    def test_not_ready_when_no_url(self):
        client = KBClient(base_url="")
        assert not client.ready

        client2 = KBClient(base_url=None)
        assert not client2.ready

    def test_ready_when_url_set(self):
        client = KBClient(base_url="http://localhost:9100")
        assert client.ready


class TestKBToolRegistration:
    @pytest.mark.asyncio
    async def test_tool_registered(self):
        registry = ToolRegistry()
        client = KBClient(base_url="http://localhost:9100")
        register_kb_tools(registry, client)
        assert "kb_search" in registry.list_tools()

    @pytest.mark.asyncio
    async def test_tool_returns_json(self):
        registry = ToolRegistry()
        client = KBClient(base_url="http://localhost:9100")
        register_kb_tools(registry, client)

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "hits": [{"path": "a.md", "heading": "H", "text": "T", "score": 0.9}]
            }
            mock_resp.raise_for_status = AsyncMock()
            mock_post.return_value = mock_resp

            result = await registry.execute("kb_search", {"query": "test"})
            parsed = json.loads(result)
            assert "hits" in parsed
            assert len(parsed["hits"]) == 1

    @pytest.mark.asyncio
    async def test_tool_not_registered_when_no_url(self):
        registry = ToolRegistry()
        client = KBClient(base_url="")
        register_kb_tools(registry, client)
        assert "kb_search" not in registry.list_tools()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_kb.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`coding-agent/src/coding_agent/tools/kb.py`:

```python
"""KB search tool: RAG vector search via the kb/server.py sidecar."""

from __future__ import annotations

import json
from typing import Any

import httpx

from coding_agent.tools.registry import ToolRegistry


class KBClient:
    """HTTP client for the KB sidecar server."""

    def __init__(self, base_url: str | None, timeout: float = 10.0):
        self._base_url = (base_url or "").rstrip("/")
        self._timeout = timeout

    @property
    def ready(self) -> bool:
        return bool(self._base_url)

    async def search(
        self, query: str, top_k: int = 8, mode: str = "hybrid"
    ) -> list[dict[str, Any]]:
        """Search the knowledge base.

        Args:
            query: Search query
            top_k: Number of results to return
            mode: Search mode — "hybrid", "vector", or "text"

        Returns:
            List of hits with path, heading, text, score
        """
        if not self.ready:
            return []

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/search",
                    json={"query": query, "top_k": top_k, "mode": mode},
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("hits", [])
        except Exception:
            return []

    async def health(self) -> bool:
        """Check if the KB sidecar is reachable."""
        if not self.ready:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.status_code == 200
        except Exception:
            return False


def register_kb_tools(registry: ToolRegistry, client: KBClient) -> None:
    """Register kb_search tool if the KB client is ready."""
    if not client.ready:
        return

    async def kb_search(query: str, top_k: int = 8) -> str:
        """Search the knowledge base for relevant documentation.

        Args:
            query: Natural language search query
            top_k: Number of results to return (default: 8)

        Returns:
            JSON with hits list containing path, heading, text, score
        """
        hits = await client.search(query, top_k=top_k)
        return json.dumps({
            "query": query,
            "hits": hits,
            "total": len(hits),
        })

    registry.register(
        name="kb_search",
        description=(
            "Search the knowledge base for relevant documentation, code patterns, "
            "or project conventions. Returns matching sections with path, heading, "
            "text, and relevance score. Use this before implementing features to "
            "check for existing patterns, conventions, or requirements."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default: 8)",
                    "default": 8,
                },
            },
            "required": ["query"],
        },
        handler=kb_search,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/tools/test_kb.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/tools/kb.py coding-agent/tests/tools/test_kb.py
git commit -m "feat(p3): add kb_search tool with HTTP client for KB sidecar"
```

---

## Task 6: Skill Loader

**Files:**
- Create: `coding-agent/src/coding_agent/skills/__init__.py`
- Create: `coding-agent/src/coding_agent/skills/load_skill.py`
- Create: `coding-agent/tests/skills/__init__.py`
- Create: `coding-agent/tests/skills/test_skill_loader.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/skills/__init__.py`: empty file

`coding-agent/tests/skills/test_skill_loader.py`:

```python
"""Tests for skill loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent.skills import SkillDef, SkillLoader
from coding_agent.skills.load_skill import register_skill_tools
from coding_agent.tools.registry import ToolRegistry


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a temporary skills directory with sample skills."""
    sd = tmp_path / "skills"
    sd.mkdir()

    # Create a valid skill
    (sd / "code-review.md").write_text(
        "---\n"
        "name: code-review\n"
        "description: Review code changes for bugs\n"
        "inputs:\n"
        "  - name: scope\n"
        "    type: string\n"
        "    description: File pattern or 'staged'\n"
        "---\n\n"
        "You are a senior code reviewer. Analyze the following changes...\n"
    )

    # Create another skill
    (sd / "test-writer.md").write_text(
        "---\n"
        "name: test-writer\n"
        "description: Generate tests for given code\n"
        "---\n\n"
        "Write comprehensive tests for the code provided.\n"
    )

    # Create invalid skill (no frontmatter)
    (sd / "broken.md").write_text("Just plain markdown, no frontmatter.\n")

    return sd


class TestSkillLoader:
    def test_load_discovers_skills(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        assert len(loader.skills) == 2  # broken.md skipped

    def test_frontmatter_parsed(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        cr = loader.get("code-review")
        assert cr is not None
        assert cr.name == "code-review"
        assert cr.description == "Review code changes for bugs"

    def test_body_loaded_on_demand(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        cr = loader.get("code-review")
        assert cr is not None
        # Body not loaded yet (lazy)
        assert cr._body is None
        # Access body triggers load
        body = cr.body
        assert "senior code reviewer" in body

    def test_get_nonexistent_returns_none(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        assert loader.get("nonexistent") is None

    def test_list_skills(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        names = loader.list_names()
        assert "code-review" in names
        assert "test-writer" in names
        assert "broken" not in names

    def test_summary_text(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        summary = loader.summary()
        assert "code-review" in summary
        assert "test-writer" in summary

    def test_empty_dir(self, tmp_path: Path):
        empty = tmp_path / "empty_skills"
        empty.mkdir()
        loader = SkillLoader(empty)
        assert len(loader.skills) == 0

    def test_nonexistent_dir(self, tmp_path: Path):
        loader = SkillLoader(tmp_path / "does_not_exist")
        assert len(loader.skills) == 0


class TestSkillTool:
    @pytest.mark.asyncio
    async def test_load_skill_tool_registered(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        registry = ToolRegistry()
        register_skill_tools(registry, loader)
        assert "load_skill" in registry.list_tools()
        assert "list_skills" in registry.list_tools()

    @pytest.mark.asyncio
    async def test_load_skill_returns_body(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        registry = ToolRegistry()
        register_skill_tools(registry, loader)

        result = await registry.execute("load_skill", {"name": "code-review"})
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert "senior code reviewer" in parsed["content"]

    @pytest.mark.asyncio
    async def test_load_skill_not_found(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        registry = ToolRegistry()
        register_skill_tools(registry, loader)

        result = await registry.execute("load_skill", {"name": "nonexistent"})
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_list_skills_tool(self, skills_dir: Path):
        loader = SkillLoader(skills_dir)
        registry = ToolRegistry()
        register_skill_tools(registry, loader)

        result = await registry.execute("list_skills", {})
        parsed = json.loads(result)
        assert "skills" in parsed
        assert len(parsed["skills"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/skills/test_skill_loader.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the skill loader**

`coding-agent/src/coding_agent/skills/__init__.py`:

```python
"""Skill loader: SKILL.md files with validated frontmatter."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SkillDef:
    """A loaded skill definition."""

    name: str
    description: str
    inputs: list[dict[str, Any]] = field(default_factory=list)
    path: Path = field(default_factory=lambda: Path("."))
    _body: str | None = field(default=None, repr=False)

    @property
    def body(self) -> str:
        """Load the skill body on demand (lazy)."""
        if self._body is None:
            self._body = self._load_body()
        return self._body

    def _load_body(self) -> str:
        """Read the markdown body (everything after the closing ---)."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except Exception:
            return ""
        # Find the second --- (closing frontmatter)
        parts = text.split("---", 2)
        if len(parts) < 3:
            return text
        return parts[2].strip()


class SkillLoader:
    """Loads SKILL.md files from a directory.

    Lazy loading: frontmatter is parsed at init, body loaded on demand.
    """

    def __init__(self, skills_dir: Path):
        self.skills: dict[str, SkillDef] = {}
        self._skills_dir = skills_dir
        self._discover()

    def _discover(self) -> None:
        """Scan skills directory for .md files with valid frontmatter."""
        if not self._skills_dir.exists():
            return

        import yaml

        for md_file in sorted(self._skills_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # Parse frontmatter
            if not text.startswith("---"):
                continue
            parts = text.split("---", 2)
            if len(parts) < 3:
                continue

            try:
                frontmatter = yaml.safe_load(parts[1])
            except Exception:
                continue

            if not isinstance(frontmatter, dict) or "name" not in frontmatter:
                continue

            skill = SkillDef(
                name=frontmatter["name"],
                description=frontmatter.get("description", ""),
                inputs=frontmatter.get("inputs", []),
                path=md_file,
                _body=None,  # Lazy
            )
            self.skills[skill.name] = skill

    def get(self, name: str) -> SkillDef | None:
        """Get a skill by name."""
        return self.skills.get(name)

    def list_names(self) -> list[str]:
        """List all available skill names."""
        return sorted(self.skills.keys())

    def summary(self) -> str:
        """Generate a summary of available skills."""
        if not self.skills:
            return "No skills available."
        lines = []
        for name in sorted(self.skills):
            skill = self.skills[name]
            lines.append(f"- {name}: {skill.description}")
        return "\n".join(lines)
```

- [ ] **Step 4: Write the skill tools**

`coding-agent/src/coding_agent/skills/load_skill.py`:

```python
"""Register skill-related tools (load_skill, list_skills)."""

from __future__ import annotations

import json

from coding_agent.skills import SkillLoader
from coding_agent.tools.registry import ToolRegistry


def register_skill_tools(registry: ToolRegistry, loader: SkillLoader) -> None:
    """Register load_skill and list_skills tools."""

    async def load_skill(name: str) -> str:
        """Load a skill's instructions by name.

        Args:
            name: Name of the skill to load

        Returns:
            JSON with skill content or error
        """
        skill = loader.get(name)
        if skill is None:
            available = loader.list_names()
            return json.dumps({
                "error": f"Skill '{name}' not found",
                "available_skills": available,
            })

        return json.dumps({
            "success": True,
            "name": skill.name,
            "description": skill.description,
            "content": skill.body,
        })

    async def list_skills() -> str:
        """List all available skills.

        Returns:
            JSON with skills list
        """
        skills_list = [
            {"name": s.name, "description": s.description}
            for s in loader.skills.values()
        ]
        return json.dumps({
            "skills": sorted(skills_list, key=lambda s: s["name"]),
            "total": len(skills_list),
        })

    registry.register(
        name="load_skill",
        description=(
            "Load a skill by name. Skills provide specialized instructions "
            "for tasks like code review, test writing, etc. Use list_skills "
            "first to see available skills."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the skill to load",
                },
            },
            "required": ["name"],
        },
        handler=load_skill,
    )

    registry.register(
        name="list_skills",
        description="List all available skills with their descriptions.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=list_skills,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/skills/test_skill_loader.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add coding-agent/src/coding_agent/skills/ coding-agent/tests/skills/
git commit -m "feat(p3): add skill loader with lazy loading and load_skill/list_skills tools"
```

---

## Task 7: Session Management

**Files:**
- Create: `coding-agent/src/coding_agent/core/session.py`
- Create: `coding-agent/tests/core/test_session.py`

- [ ] **Step 1: Write the failing tests**

`coding-agent/tests/core/test_session.py`:

```python
"""Tests for session management."""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.core.config import Config
from coding_agent.core.session import Session
from coding_agent.core.tape import Entry


class TestSessionCreate:
    def test_create_assigns_uuid(self, tmp_path: Path):
        config = Config(api_key="test-key", tape_dir=tmp_path)
        session = Session.create(config)
        assert session.id  # Non-empty UUID
        assert len(session.id) == 36  # UUID format

    def test_create_creates_tape(self, tmp_path: Path):
        config = Config(api_key="test-key", tape_dir=tmp_path)
        session = Session.create(config)
        assert session.tape is not None

    def test_create_status_active(self, tmp_path: Path):
        config = Config(api_key="test-key", tape_dir=tmp_path)
        session = Session.create(config)
        assert session.status == "active"


class TestSessionLoadResume:
    def test_load_restores_tape(self, tmp_path: Path):
        config = Config(api_key="test-key", tape_dir=tmp_path)
        # Create and populate
        s1 = Session.create(config)
        s1.tape.append(Entry.message("user", "Hello"))
        s1.tape.append(Entry.message("assistant", "Hi"))
        session_id = s1.id

        # Load from disk
        s2 = Session.load(session_id, config)
        entries = s2.tape.entries()
        assert len(entries) == 2
        assert entries[0].payload["content"] == "Hello"

    def test_load_nonexistent_raises(self, tmp_path: Path):
        config = Config(api_key="test-key", tape_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            Session.load("nonexistent-id", config)


class TestSessionClose:
    def test_close_sets_status(self, tmp_path: Path):
        config = Config(api_key="test-key", tape_dir=tmp_path)
        session = Session.create(config)
        session.close()
        assert session.status == "completed"

    def test_close_interrupted(self, tmp_path: Path):
        config = Config(api_key="test-key", tape_dir=tmp_path)
        session = Session.create(config)
        session.close(status="interrupted")
        assert session.status == "interrupted"

    def test_tape_preserved_after_close(self, tmp_path: Path):
        config = Config(api_key="test-key", tape_dir=tmp_path)
        session = Session.create(config)
        session.tape.append(Entry.message("user", "Test"))
        session.close()

        # Tape file still exists
        s2 = Session.load(session.id, config)
        assert len(s2.tape.entries()) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_session.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`coding-agent/src/coding_agent/core/session.py`:

```python
"""Session: manages conversation session lifecycle and persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from coding_agent.core.config import Config
from coding_agent.core.tape import Tape


class Session:
    """Manages a conversation session's lifecycle and persistence."""

    def __init__(
        self,
        session_id: str,
        tape: Tape,
        config: Config,
        status: Literal["active", "completed", "interrupted"] = "active",
    ):
        self.id = session_id
        self.tape = tape
        self.config = config
        self.status = status
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = self.created_at

    @classmethod
    def create(cls, config: Config) -> Session:
        """Start a new session."""
        session_id = str(uuid.uuid4())
        tape_dir = Path(config.tape_dir)
        tape_dir.mkdir(parents=True, exist_ok=True)
        tape_path = tape_dir / f"{session_id}.jsonl"
        tape = Tape(tape_path)
        return cls(session_id=session_id, tape=tape, config=config)

    @classmethod
    def load(cls, session_id: str, config: Config) -> Session:
        """Resume an existing session from its tape file."""
        tape_dir = Path(config.tape_dir)
        tape_path = tape_dir / f"{session_id}.jsonl"
        if not tape_path.exists():
            raise FileNotFoundError(f"No session found: {session_id}")
        tape = Tape(tape_path)
        return cls(session_id=session_id, tape=tape, config=config)

    def close(self, status: str = "completed") -> None:
        """Mark session as complete."""
        self.status = status
        self.updated_at = datetime.now(timezone.utc)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/core/test_session.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/core/session.py coding-agent/tests/core/test_session.py
git commit -m "feat(p3): add session management (create, load, close)"
```

---

## Task 8: CLI Integration

**Files:**
- Modify: `coding-agent/src/coding_agent/__main__.py`
- Modify: `coding-agent/src/coding_agent/core/config.py`

- [ ] **Step 1: Add KB and skills config fields**

In `coding-agent/src/coding_agent/core/config.py`, add to the `Config` class:

```python
# RAG
kb_base_url: str | None = None

# Skills
skills_dir: Path = Path.home() / ".coding-agent" / "skills"
```

Add to `_ENV_MAP`:

```python
"AGENT_KB_BASE_URL": "kb_base_url",
"AGENT_SKILLS_DIR": "skills_dir",
```

- [ ] **Step 2: Update CLI to register KB, skills, and session**

Update `coding-agent/src/coding_agent/__main__.py` `run` command to add options:

```python
@click.option("--kb-base-url", default=None, envvar="AGENT_KB_BASE_URL", help="KB sidecar base URL")
@click.option("--skills-dir", default=None, help="Skills directory path")
@click.option("--resume", "session_id", default=None, help="Resume a previous session")
```

Update `_run` to create/resume session and register KB + skills:

```python
async def _run(config, goal, session_id=None):
    from coding_agent.core.loop import AgentLoop
    from coding_agent.core.planner import PlanManager
    from coding_agent.core.session import Session
    from coding_agent.core.tokens import make_counter
    from coding_agent.tools.registry import ToolRegistry
    from coding_agent.tools.file import register_file_tools
    from coding_agent.tools.shell import register_shell_tools
    from coding_agent.tools.search import register_search_tools
    from coding_agent.tools.planner import register_planner_tools
    from coding_agent.tools.subagent import register_subagent_tool
    from coding_agent.tools.kb import KBClient, register_kb_tools
    from coding_agent.skills import SkillLoader
    from coding_agent.skills.load_skill import register_skill_tools
    from coding_agent.core.context import Context
    from coding_agent.ui.headless import HeadlessConsumer

    # Session management
    if session_id:
        session = Session.load(session_id, config)
    else:
        session = Session.create(config)
    click.echo(f"Session: {session.id}")

    provider = _create_provider(config)
    counter = make_counter(config.model)

    planner = PlanManager()
    registry = ToolRegistry()
    register_file_tools(registry, repo_root=config.repo)
    register_shell_tools(registry, cwd=config.repo)
    register_search_tools(registry, repo_root=config.repo)
    register_planner_tools(registry, planner)

    # KB tools
    kb_client = KBClient(base_url=config.kb_base_url)
    register_kb_tools(registry, kb_client)

    # Skill tools
    skill_loader = SkillLoader(config.skills_dir)
    register_skill_tools(registry, skill_loader)

    consumer = HeadlessConsumer()

    # Subagent
    register_subagent_tool(
        registry=registry,
        provider=provider,
        tape=session.tape,
        consumer=consumer,
        max_steps=config.subagent_max_steps,
        max_depth=config.max_subagent_depth,
    )

    system_prompt = (
        "You are a coding agent. You can read files, edit files, "
        "run shell commands, search the codebase, create task plans, "
        "dispatch sub-agents, search the knowledge base, and load skills.\n\n"
        "Always create a plan (todo_write) before starting complex work. "
        "Use kb_search to check for project conventions before implementing."
    )
    context = Context(
        provider.max_context_size,
        system_prompt,
        planner=planner,
        token_counter=counter,
    )

    loop = AgentLoop(
        provider=provider,
        tools=registry,
        tape=session.tape,
        context=context,
        consumer=consumer,
        max_steps=config.max_steps,
    )

    result = await loop.run_turn(goal)
    session.close()
    click.echo(f"\n--- Result ({result.stop_reason}) ---")
    click.echo(f"Session ID: {session.id} (use --resume to continue)")
    if result.final_message:
        click.echo(result.final_message)
```

- [ ] **Step 3: Verify CLI works**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run python -m coding_agent run --help
```

Expected: Shows `--kb-base-url`, `--skills-dir`, `--resume` options

- [ ] **Step 4: Run full test suite**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest -v
```

Expected: ALL tests pass

- [ ] **Step 5: Commit**

```bash
git add coding-agent/src/coding_agent/__main__.py coding-agent/src/coding_agent/core/config.py
git commit -m "feat(p3): wire KB, skills, session, and token counter into CLI"
```

---

## Task 9: E2E Integration Test

**Files:**
- Create: `coding-agent/tests/test_e2e_p3.py`

- [ ] **Step 1: Write E2E tests**

`coding-agent/tests/test_e2e_p3.py`:

```python
"""E2E integration tests for P3: KB + skills + context compaction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from coding_agent.core.context import Context
from coding_agent.core.loop import AgentLoop
from coding_agent.core.planner import PlanManager
from coding_agent.core.tape import Entry, Tape
from coding_agent.core.tokens import ApproximateCounter
from coding_agent.providers.base import StreamEvent, ToolCall, ToolSchema
from coding_agent.skills import SkillLoader
from coding_agent.skills.load_skill import register_skill_tools
from coding_agent.tools.kb import KBClient, register_kb_tools
from coding_agent.tools.registry import ToolRegistry
from coding_agent.wire import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
)


class MockConsumer:
    def __init__(self):
        self.messages: list[WireMessage] = []

    async def emit(self, msg: WireMessage) -> None:
        self.messages.append(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(call_id=req.call_id, decision="approve")


class MockProvider:
    def __init__(self, responses: list[list[StreamEvent]]):
        self._responses = responses
        self._call_index = 0

    @property
    def model_name(self) -> str:
        return "mock"

    @property
    def max_context_size(self) -> int:
        return 128000

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        if self._call_index < len(self._responses):
            events = self._responses[self._call_index]
            self._call_index += 1
            for event in events:
                yield event
        else:
            yield StreamEvent(type="delta", text="Done")
            yield StreamEvent(type="done")


class TestE2EP3KBSearch:
    @pytest.mark.asyncio
    async def test_agent_uses_kb_search(self):
        """Agent calls kb_search tool and gets results."""
        provider = MockProvider([
            # Step 1: Call kb_search
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="call_1",
                        name="kb_search",
                        arguments={"query": "error handling"},
                    ),
                ),
                StreamEvent(type="done"),
            ],
            # Step 2: Final response
            [
                StreamEvent(type="delta", text="Found conventions for error handling."),
                StreamEvent(type="done"),
            ],
        ])

        registry = ToolRegistry()
        kb_client = KBClient(base_url="http://localhost:9100")
        register_kb_tools(registry, kb_client)

        tape = Tape()
        consumer = MockConsumer()
        counter = ApproximateCounter()
        context = Context(128000, "You are a coding agent.", token_counter=counter)

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "hits": [{"path": "docs/errors.md", "heading": "Error Format", "text": "Use structured errors", "score": 0.9}]
            }
            mock_resp.raise_for_status = AsyncMock()
            mock_post.return_value = mock_resp

            loop = AgentLoop(
                provider=provider,
                tools=registry,
                tape=tape,
                context=context,
                consumer=consumer,
            )
            result = await loop.run_turn("How should I handle errors?")

        assert result.stop_reason == "no_tool_calls"
        entries = tape.entries()
        tool_calls = [e for e in entries if e.kind == "tool_call"]
        assert any(e.payload["tool"] == "kb_search" for e in tool_calls)


class TestE2EP3Skills:
    @pytest.mark.asyncio
    async def test_agent_loads_skill(self, tmp_path: Path):
        """Agent calls load_skill and gets skill instructions."""
        # Create skill
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "review.md").write_text(
            "---\nname: review\ndescription: Code review\n---\n\n"
            "Review the code for bugs and security issues.\n"
        )

        provider = MockProvider([
            # Step 1: Call load_skill
            [
                StreamEvent(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="call_1",
                        name="load_skill",
                        arguments={"name": "review"},
                    ),
                ),
                StreamEvent(type="done"),
            ],
            # Step 2: Final response
            [
                StreamEvent(type="delta", text="Loaded the review skill."),
                StreamEvent(type="done"),
            ],
        ])

        registry = ToolRegistry()
        loader = SkillLoader(skills_dir)
        register_skill_tools(registry, loader)

        tape = Tape()
        consumer = MockConsumer()
        counter = ApproximateCounter()
        context = Context(128000, "You are a coding agent.", token_counter=counter)

        loop = AgentLoop(
            provider=provider,
            tools=registry,
            tape=tape,
            context=context,
            consumer=consumer,
        )
        result = await loop.run_turn("Review the latest changes")

        assert result.stop_reason == "no_tool_calls"
        entries = tape.entries()
        tool_results = [e for e in entries if e.kind == "tool_result"]
        assert any("bugs and security" in str(e.payload.get("result", "")) for e in tool_results)
```

- [ ] **Step 2: Run E2E tests**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest tests/test_e2e_p3.py -v
```

Expected: PASS

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/kina/Code/Agent/Coding-Agent-Loop/coding-agent
uv run pytest -v
```

Expected: ALL tests pass (P0 + P1 + P3)

- [ ] **Step 4: Commit**

```bash
git add coding-agent/tests/test_e2e_p3.py
git commit -m "test(p3): add E2E integration tests for KB search and skill loading"
```

---

## Summary

| Task | Component | Tests | LOC |
|------|-----------|-------|-----|
| 1 | Dependencies (tiktoken, pyyaml) | — | 5 |
| 2 | Token Counter | 8 | 100 |
| 3 | Context L2 Pruning | 4 | 80 |
| 4 | Context L3 Summarization | 3 | 70 |
| 5 | KB Search Tool | 8 | 100 |
| 6 | Skill Loader | 11 | 150 |
| 7 | Session Management | 6 | 60 |
| 8 | CLI Integration | — | 40 |
| 9 | E2E Tests | 2 | 130 |
| **Total** | | **~42** | **~735** |

P3 exit criteria: KB search works via the existing sidecar. Long sessions maintain quality via 3-layer context compaction (anchor truncation → selective pruning → LLM summarization). Skills can be loaded on demand via `load_skill` tool. Sessions can be created, resumed, and closed.
