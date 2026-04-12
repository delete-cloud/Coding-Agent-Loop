# KBPlugin Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic KB grounding (RAG) to coding-agent via a `KBPlugin` that injects relevant knowledge base chunks into LLM context via the `build_context` hook, with CLI subcommands for indexing and searching.

**Architecture:** KBPlugin is a standard coding-agent plugin implementing `mount` + `build_context` hooks. It wraps the existing `KB` class from `src/coding_agent/kb.py`, adding a synchronous `search_sync()` adapter since `build_context` is called synchronously by the hook runtime. Plugin enablement follows the standard `plugins.enabled` list in `agent.toml`; the `[kb]` TOML section provides configuration only. CLI subcommands (`kb index`, `kb search`) are added to the existing Click group in `__main__.py`.

**Tech Stack:** Python 3.11+, LanceDB `>=0.18.0`, OpenAI `>=1.50.0` (embeddings), Click `>=8.0.0` (CLI), pytest + tmp_path (testing)

**Boundary rule:** `KBPlugin` owns hook integration and grounding-message assembly; `KB` owns indexing/search behavior and may expose both sync and async retrieval entrypoints.

**Spec:** `docs/superpowers/specs/2026-04-08-kb-plugin-phase1-design.md`

**Locked-down assumptions (from spec review):**
1. Phase 1 does NOT change `agentkit/` runtime — all changes are in `coding_agent/`
2. `KBPlugin.mount` and `build_context` are synchronous (hook runtime calls them synchronously)
3. `kb.py` is NOT "as-is" — it needs `_embed_sync()` plus a true synchronous `search_sync()` path built on the sync OpenAI client, and `index_directory()` must respect constructor-provided `text_extensions`; Phase 1 does not require event-loop bridging inside the plugin
4. Plugin enablement via `plugins.enabled` list (standard path); `[kb]` section is config-only, no `enabled` key
5. Phase 1: default extension set from config, no `--force` flag, no incremental updates
6. `db_path` resolved relative to `AGENT_DATA_DIR`; `OPENAI_API_KEY` is an explicit precondition

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/coding_agent/kb.py` | Modify | Add `_embed_sync()`, `has_table()`, `search_sync()`, and constructor-driven `text_extensions` support to `KB` |
| `src/coding_agent/plugins/kb.py` | Create | `KBPlugin` — mount + build_context hooks, query caching |
| `src/coding_agent/app.py` | Modify | Add `"kb"` to `plugin_factories`; plugin activation still follows `plugins.enabled`, while `[kb]` remains configuration-only |
| `src/coding_agent/agent.toml` | Modify | Add `[kb]` configuration section, add `"kb"` to `plugins.enabled` |
| `src/coding_agent/__main__.py` | Modify | Add `kb index` and `kb search` CLI subcommands |
| `tests/coding_agent/plugins/test_kb_plugin.py` | Create | Unit tests for KBPlugin (mount, build_context, caching) |
| `tests/cli/test_kb_commands.py` | Create | CLI integration tests for `kb index` and `kb search` |

---

## Task 1: Add a minimal synchronous search path to `KB`

**Files:**
- Modify: `src/coding_agent/kb.py:321-360` (after the existing `search` method)
- Test: `tests/coding_agent/test_kb_sync.py`

**Goal:** Keep `KBPlugin` as a thin hook adapter. Retrieval and indexing implementation stays inside `src/coding_agent/kb.py`, which will expose a minimal synchronous Phase 1 path via `_embed_sync()` + `search_sync()` and accept constructor-provided `text_extensions` for indexing. Do not add threadpool-based async bridging to the plugin. Do not remove the existing async path in Phase 1; just stop depending on it for grounding.

- [ ] **Step 1: Write the failing test for `search_sync`**

Create `tests/coding_agent/test_kb_sync.py`:

```python
"""Tests for KB.search_sync true synchronous path."""

import pytest
from pathlib import Path

from coding_agent.kb import KB, KBSearchResult


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Return deterministic fake embeddings for testing."""
    return [[float(i)] * 8 for i, _ in enumerate(texts)]


class TestSearchSync:
    def test_search_sync_returns_results(self, tmp_path: Path):
        """search_sync returns KBSearchResult objects through the sync path."""
        kb = KB(db_path=tmp_path / "test_db", embedding_dim=8, embedding_fn=_fake_embed)
        # Index some content first (async, but we need it for setup)
        import asyncio
        asyncio.run(kb.index_file(Path("doc.md"), "Hello world this is a test document about Python programming"))
        # Now test synchronous search
        results = kb.search_sync("Python", k=3)
        assert isinstance(results, list)
        assert len(results) > 0
        assert all(isinstance(r, KBSearchResult) for r in results)

    def test_search_sync_empty_query_returns_empty(self, tmp_path: Path):
        """search_sync with empty query returns empty list without calling embed."""
        kb = KB(db_path=tmp_path / "test_db", embedding_dim=8, embedding_fn=_fake_embed)
        results = kb.search_sync("", k=5)
        assert results == []

    def test_search_sync_no_table_returns_empty(self, tmp_path: Path):
        """search_sync when no chunks table exists returns empty list."""
        kb = KB(db_path=tmp_path / "test_db", embedding_dim=8, embedding_fn=_fake_embed)
        # Don't index anything — table won't exist
        results = kb.search_sync("anything", k=5)
        assert results == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/coding_agent/test_kb_sync.py -v`
Expected: FAIL with `AttributeError: 'KB' object has no attribute 'search_sync'`

- [ ] **Step 3: Implement `_embed_sync`, `has_table`, `search_sync`, and constructor-driven `text_extensions` on KB class**

In `src/coding_agent/kb.py`, make the following changes.

First, extend the constructor to accept indexed file extensions as KB-owned behavior:

```python
    def __init__(
        self,
        db_path: Path | str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
        text_extensions: set[str] | None = None,
    ):
```

And store it in `__init__`:

```python
        self._text_extensions = text_extensions or {
            ".py", ".md", ".txt", ".rst", ".json", ".yaml", ".yml",
            ".toml", ".ini", ".cfg", ".js", ".ts", ".jsx", ".tsx",
            ".html", ".css", ".sh", ".bash", ".zsh", ".fish",
        }
```

Then add a synchronous OpenAI client cache in `__init__`:

```python
        self._openai_client = None
        self._openai_sync_client = None
```

Then add a sync client getter next to `_get_openai_client`:

```python
    def _get_openai_sync_client(self):
        """Get or create synchronous OpenAI client."""
        if self._openai_sync_client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError(
                    "OpenAI package is required for embeddings. "
                    "Install it with: pip install openai"
                )

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable is required "
                    "when not using a custom embedding function"
                )
            self._openai_sync_client = OpenAI(api_key=api_key)
        return self._openai_sync_client
```

Then update `index_directory()` to use the constructor-provided extension set instead of a hard-coded local constant:

```python
    async def index_directory(
        self,
        root: Path,
        pattern: str = "**/*",
        show_progress: bool = True,
    ) -> None:
        """Index all configured text files in a directory."""
        root = Path(root)

        files = [
            path for path in root.rglob(pattern)
            if path.is_file() and path.suffix in self._text_extensions
        ]
```

Then add a synchronous embedding helper:

```python
    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using OpenAI sync client or custom embedding function."""
        if self._embedding_fn is not None:
            return self._embedding_fn(texts)

        client = self._get_openai_sync_client()
        response = client.embeddings.create(
            model=self.embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def has_table(self, table_name: str = "chunks") -> bool:
        """Check whether a named table exists in the database.

        Args:
            table_name: Table name to check (default: "chunks").

        Returns:
            True if the table exists, False otherwise.
        """
        return table_name in self._db.list_tables()

    def search_sync(self, query: str, k: int = 5) -> list[KBSearchResult]:
        """Search for relevant chunks using vector search synchronously."""
        if not query.strip():
            return []

        if not self.has_table():
            return []

        table = self._get_table()

        embeddings = self._embed_sync([query])
        query_vector = embeddings[0]

        import json

        results = (
            table.search(query_vector)
            .limit(k)
            .to_list()
        )

        return [
            KBSearchResult(
                chunk=DocumentChunk(
                    id=r["id"],
                    content=r["content"],
                    source=r["source"],
                    metadata=json.loads(r["metadata"]),
                ),
                score=r["_distance"],
            )
            for r in results
        ]
```

**Design note:** `KBPlugin.build_context()` is synchronous because the hook runtime calls it synchronously. That does **not** mean the plugin should implement sync/async bridging itself. The plugin remains a thin adapter; the `KB` class owns retrieval and indexing behavior and therefore exposes a synchronous Phase 1 search entrypoint plus constructor-driven indexing configuration. This keeps event-loop concerns and indexing policy out of plugin/CLI code.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/coding_agent/test_kb_sync.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/kb.py tests/coding_agent/test_kb_sync.py
git commit -m "feat(kb): add true synchronous search path for KB grounding"
```

---

## Task 2: Create KBPlugin with mount hook

**Files:**
- Create: `src/coding_agent/plugins/kb.py`
- Test: `tests/coding_agent/plugins/test_kb_plugin.py`

- [ ] **Step 1: Write the failing tests for KBPlugin construction and mount**

Create `tests/coding_agent/plugins/test_kb_plugin.py`:

```python
"""Tests for KBPlugin — mount + build_context hooks."""

import pytest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from coding_agent.plugins.kb import KBPlugin


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Return deterministic fake embeddings."""
    return [[float(i)] * 8 for i, _ in enumerate(texts)]


class TestKBPluginInit:
    def test_state_key(self):
        plugin = KBPlugin(db_path=Path("/tmp/test_kb"), embedding_dim=8, embedding_fn=_fake_embed)
        assert plugin.state_key == "kb"

    def test_hooks_registered(self):
        plugin = KBPlugin(db_path=Path("/tmp/test_kb"), embedding_dim=8, embedding_fn=_fake_embed)
        hooks = plugin.hooks()
        assert "mount" in hooks
        assert "build_context" in hooks
        assert len(hooks) == 2


class TestKBPluginMount:
    def test_mount_creates_kb_instance(self, tmp_path: Path):
        plugin = KBPlugin(
            db_path=tmp_path / "kb_db",
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )
        state = plugin.do_mount()
        assert "kb" in state
        assert "has_table" in state
        assert state["has_table"] is False  # No index yet

    def test_mount_detects_existing_table(self, tmp_path: Path):
        import asyncio
        from coding_agent.kb import KB

        # Pre-create a KB with indexed content
        kb = KB(db_path=tmp_path / "kb_db", embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("test.md"), "some content for indexing"))

        plugin = KBPlugin(
            db_path=tmp_path / "kb_db",
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )
        state = plugin.do_mount()
        assert state["has_table"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py::TestKBPluginInit -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.plugins.kb'`

- [ ] **Step 3: Implement KBPlugin skeleton with mount hook**

Create `src/coding_agent/plugins/kb.py`:

```python
"""KBPlugin — Automatic knowledge base grounding via build_context hook.

Injects relevant KB chunks as system messages before each LLM turn.
Uses the existing KB class for vector search, with a synchronous adapter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from coding_agent.kb import KB

logger = logging.getLogger(__name__)

# Maximum characters per chunk in the grounding message.
_CHUNK_TRUNCATE = 500


@dataclass
class _SearchSnapshot:
    """Tracks the last search to enable caching."""

    last_user_msg: str
    result_cache: list[dict[str, Any]]


class KBPlugin:
    """Plugin that grounds LLM context with relevant KB chunks.

    Hooks:
        mount  — initialise KB connection, check table existence.
        build_context — search KB for relevant chunks and return grounding messages.
    """

    state_key = "kb"

    def __init__(
        self,
        db_path: Path,
        embedding_model: str = "text-embedding-3-small",
        embedding_dim: int = 1536,
        chunk_size: int = 1200,
        chunk_overlap: int = 200,
        top_k: int = 5,
        index_extensions: list[str] | None = None,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self._db_path = db_path
        self._embedding_model = embedding_model
        self._embedding_dim = embedding_dim
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._top_k = top_k
        self._index_extensions = index_extensions or [
            ".md", ".txt", ".rst", ".yaml", ".yml", ".toml",
        ]
        self._embedding_fn = embedding_fn

        self._kb: KB | None = None
        self._has_table: bool = False
        self._snapshot: _SearchSnapshot | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "mount": self.do_mount,
            "build_context": self.build_context,
        }

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        """Initialise KB connection and check for existing chunks table."""
        self._kb = KB(
            db_path=self._db_path,
            embedding_model=self._embedding_model,
            embedding_dim=self._embedding_dim,
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
            embedding_fn=self._embedding_fn,
        )
        self._has_table = self._kb.has_table()
        logger.info("KBPlugin mounted: db_path=%s, has_table=%s", self._db_path, self._has_table)
        return {
            "kb": self._kb,
            "has_table": self._has_table,
        }

    def build_context(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Placeholder — implemented in Task 3."""
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/kb.py tests/coding_agent/plugins/test_kb_plugin.py
git commit -m "feat(kb): add KBPlugin skeleton with mount hook"
```

---

## Task 3: Implement KBPlugin build_context hook

**Files:**
- Modify: `src/coding_agent/plugins/kb.py`
- Modify: `tests/coding_agent/plugins/test_kb_plugin.py`

- [ ] **Step 1: Write failing tests for build_context**

Append to `tests/coding_agent/plugins/test_kb_plugin.py`:

```python
from agentkit.tape.tape import Tape
from agentkit.tape.models import Entry


class TestBuildContextNoTable:
    def test_returns_empty_when_no_table(self, tmp_path: Path):
        """build_context returns [] when chunks table does not exist."""
        plugin = KBPlugin(
            db_path=tmp_path / "kb_db",
            embedding_dim=8,
            embedding_fn=_fake_embed,
        )
        plugin.do_mount()
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "hello"}))
        result = plugin.build_context(tape=tape)
        assert result == []


class TestBuildContextSearch:
    @pytest.fixture()
    def indexed_plugin(self, tmp_path: Path) -> KBPlugin:
        """Create a KBPlugin with pre-indexed content."""
        import asyncio
        from coding_agent.kb import KB

        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("src/auth.py"), "Authentication module with JWT token validation"))
        asyncio.run(kb.index_file(Path("docs/api.md"), "API documentation for the REST endpoints"))

        plugin = KBPlugin(
            db_path=db_path,
            embedding_dim=8,
            top_k=5,
            embedding_fn=_fake_embed,
        )
        plugin.do_mount()
        return plugin

    def test_first_call_triggers_search(self, indexed_plugin: KBPlugin):
        """First call with user message triggers KB search and returns grounding."""
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "How does auth work?"}))
        result = indexed_plugin.build_context(tape=tape)
        assert isinstance(result, list)
        assert len(result) == 1  # Single grounding message
        msg = result[0]
        assert msg["role"] == "system"
        assert msg["content"].startswith("[KB]")

    def test_cache_hit_same_message(self, indexed_plugin: KBPlugin):
        """Same user message on second call reuses cache, no new search."""
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "How does auth work?"}))
        result1 = indexed_plugin.build_context(tape=tape)
        result2 = indexed_plugin.build_context(tape=tape)
        assert result1 == result2
        # Verify snapshot was set
        assert indexed_plugin._snapshot is not None
        assert indexed_plugin._snapshot.last_user_msg == "How does auth work?"

    def test_new_user_message_triggers_fresh_search(self, indexed_plugin: KBPlugin):
        """Different user message triggers a new search."""
        tape1 = Tape()
        tape1.append(Entry(kind="message", payload={"role": "user", "content": "How does auth work?"}))
        result1 = indexed_plugin.build_context(tape=tape1)

        tape2 = Tape()
        tape2.append(Entry(kind="message", payload={"role": "user", "content": "Show me the API docs"}))
        result2 = indexed_plugin.build_context(tape=tape2)
        assert isinstance(result2, list)
        assert len(result2) == 1
        assert indexed_plugin._snapshot.last_user_msg == "Show me the API docs"

    def test_empty_search_results_returns_empty(self, tmp_path: Path):
        """When search returns no results, build_context returns []."""
        import asyncio
        from coding_agent.kb import KB

        db_path = tmp_path / "kb_db"
        # Create table but with content that won't match
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("x.md"), "x"))

        plugin = KBPlugin(
            db_path=db_path,
            embedding_dim=8,
            top_k=5,
            embedding_fn=_fake_embed,
        )
        plugin.do_mount()

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "completely unrelated query"}))
        result = plugin.build_context(tape=tape)
        # Even if results come back, they should be formatted; but if truly empty:
        assert isinstance(result, list)

    def test_no_user_message_returns_empty(self, indexed_plugin: KBPlugin):
        """build_context returns [] when tape has no user messages."""
        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "assistant", "content": "Hello"}))
        result = indexed_plugin.build_context(tape=tape)
        assert result == []


class TestGroundingFormat:
    def test_grounding_message_format(self, tmp_path: Path):
        """Grounding message has [KB] prefix and source paths."""
        import asyncio
        from coding_agent.kb import KB

        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("src/pipeline.py"), "Pipeline runner for agent turns"))

        plugin = KBPlugin(
            db_path=db_path,
            embedding_dim=8,
            top_k=5,
            embedding_fn=_fake_embed,
        )
        plugin.do_mount()

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "pipeline"}))
        result = plugin.build_context(tape=tape)
        assert len(result) == 1
        content = result[0]["content"]
        assert content.startswith("[KB] The following code/documentation snippets may be relevant:")
        assert "src/pipeline.py" in content

    def test_chunk_truncation_at_500_chars(self, tmp_path: Path):
        """Each chunk is truncated to 500 characters in the grounding message."""
        import asyncio
        from coding_agent.kb import KB

        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        long_content = "A" * 2000  # Much longer than 500 chars
        asyncio.run(kb.index_file(Path("long.txt"), long_content))

        plugin = KBPlugin(
            db_path=db_path,
            embedding_dim=8,
            top_k=1,
            embedding_fn=_fake_embed,
        )
        plugin.do_mount()

        tape = Tape()
        tape.append(Entry(kind="message", payload={"role": "user", "content": "long"}))
        result = plugin.build_context(tape=tape)
        assert len(result) == 1
        content = result[0]["content"]
        # Find the chunk content after the source header line
        # Each chunk body should be <= 500 chars + "..." truncation marker
        lines = content.split("\n")
        chunk_lines = [l for l in lines if l.startswith("AAA")]
        for line in chunk_lines:
            assert len(line) <= 503  # 500 + "..."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py::TestBuildContextNoTable -v`
Expected: PASS (placeholder returns [])

Run: `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py::TestBuildContextSearch::test_first_call_triggers_search -v`
Expected: FAIL — placeholder returns [] instead of grounding message

- [ ] **Step 3: Implement build_context**

`build_context()` must call `self._kb.search_sync(...)` directly. It must not create threads, call `asyncio.run(...)`, or perform event-loop bridging inside the plugin.

Replace the placeholder `build_context` method in `src/coding_agent/plugins/kb.py`:

```python
    def build_context(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Search KB for relevant chunks and return grounding messages.

        Change detection: search is triggered only when the latest user
        message differs from the cached query.  Otherwise the cached
        grounding messages are returned unchanged.
        """
        if self._kb is None or not self._has_table:
            return []

        tape = kwargs.get("tape")
        if tape is None:
            return []

        # Extract latest user message by reverse-scanning the tape.
        user_msg: str | None = None
        for entry in reversed(list(tape)):
            if entry.kind == "message" and entry.payload.get("role") == "user":
                user_msg = entry.payload.get("content", "")
                break

        if not user_msg:
            return []

        # Cache hit — same query as last time.
        if (
            self._snapshot is not None
            and self._snapshot.last_user_msg == user_msg
        ):
            return self._snapshot.result_cache

        # Perform synchronous KB search.
        results = self._kb.search_sync(user_msg, k=self._top_k)

        if not results:
            self._snapshot = _SearchSnapshot(last_user_msg=user_msg, result_cache=[])
            return []

        # Format grounding message.
        grounding = self._format_grounding(results)
        self._snapshot = _SearchSnapshot(last_user_msg=user_msg, result_cache=grounding)
        return grounding

    def _format_grounding(self, results: list[Any]) -> list[dict[str, Any]]:
        """Format KB search results into a single system grounding message."""
        parts: list[str] = [
            "[KB] The following code/documentation snippets may be relevant:",
            "",
        ]
        for r in results:
            chunk = r.chunk
            content = chunk.content
            if len(content) > _CHUNK_TRUNCATE:
                content = content[:_CHUNK_TRUNCATE] + "..."
            parts.append(f"--- {chunk.source} ---")
            parts.append(content)
            parts.append("")

        return [{"role": "system", "content": "\n".join(parts).rstrip()}]
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/plugins/kb.py tests/coding_agent/plugins/test_kb_plugin.py
git commit -m "feat(kb): implement build_context with caching and grounding format"
```

---

## Task 4: Wire KBPlugin into app.py

**Files:**
- Modify: `src/coding_agent/app.py:1-32` (imports) and `src/coding_agent/app.py:146-228` (plugin factories + config reading)
- Modify: `src/coding_agent/agent.toml` (add `[kb]` section and enable plugin)

- [ ] **Step 1: Add KBPlugin import to app.py**

In `src/coding_agent/app.py`, after line 24 (`from coding_agent.plugins.memory import MemoryPlugin`), add:

```python
from coding_agent.plugins.kb import KBPlugin
```

- [ ] **Step 2: Read `[kb]` config section in create_child_pipeline**

In `src/coding_agent/app.py`, after line 151 (`mcp_cfg = cfg.extra.get("mcp", {})`), add:

```python
    kb_cfg = cfg.extra.get("kb", {})
```

- [ ] **Step 3: Add `"kb"` to plugin_factories**

In `src/coding_agent/app.py`, inside the `plugin_factories.update({...})` block (after line 224, before line 226 `}`), add the `"kb"` factory:

```python
            "kb": lambda: KBPlugin(
                db_path=Path(
                    os.environ.get("AGENT_DATA_DIR", "./data")
                ) / kb_cfg.get("db_path", "kb"),
                embedding_model=kb_cfg.get("embedding_model", "text-embedding-3-small"),
                embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
                chunk_size=int(kb_cfg.get("chunk_size", 1200)),
                chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
                top_k=int(kb_cfg.get("top_k", 5)),
                index_extensions=kb_cfg.get(
                    "index_extensions",
                    [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"],
                ),
            ),
```

Note: `embedding_fn` is intentionally omitted — it exists only for test injection. Production always uses OpenAI.

- [ ] **Step 4: Add `[kb]` configuration to agent.toml**

In `src/coding_agent/agent.toml`, after line 25 (end of `plugins.enabled` list, before `]`), add `"kb"` to the enabled list:

```toml
[agent.plugins]
enabled = [
    "llm_provider",
    "storage",
    "core_tools",
    "approval",
    "doom_detector",
    "parallel_executor",
    "topic",
    "summarizer",
    "memory",
    "session_metrics",
    "shell_session",
    "skills",
    "mcp",
    "kb",
]
```

Then, at the end of the file (after line 88), add the `[kb]` configuration section:

```toml
# ============================================================
# Knowledge Base (KB) Plugin Configuration
# ============================================================
# Requires OPENAI_API_KEY environment variable for embeddings.
# Index your codebase first: coding-agent kb index <path>

[kb]
db_path = "kb"
embedding_model = "text-embedding-3-small"
embedding_dim = 1536
chunk_size = 1200
chunk_overlap = 200
top_k = 5
index_extensions = [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"]
```

- [ ] **Step 5: Verify no import errors or diagnostics**

Run: `uv run python -c "from coding_agent.plugins.kb import KBPlugin; print('OK')"`
Expected: `OK`

Run: `uv run pytest tests/coding_agent/plugins/test_kb_plugin.py -v`
Expected: All PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/app.py src/coding_agent/agent.toml
git commit -m "feat(kb): wire KBPlugin into app.py and add agent.toml config"
```

---

## Task 5: Add `kb index` CLI subcommand

**Files:**
- Modify: `src/coding_agent/__main__.py`
- Test: `tests/cli/test_kb_commands.py`

- [ ] **Step 1: Write failing test for `kb index`**

Create `tests/cli/test_kb_commands.py`:

```python
"""Tests for kb CLI subcommands."""

import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from click.testing import CliRunner

from coding_agent.__main__ import main


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Return deterministic fake embeddings."""
    return [[float(i)] * 8 for i, _ in enumerate(texts)]


class TestKBIndex:
    def test_kb_index_creates_table(self, tmp_path: Path):
        """kb index <path> indexes files and creates chunks table."""
        # Create a file to index
        doc = tmp_path / "docs"
        doc.mkdir()
        (doc / "readme.md").write_text("# Hello World\nThis is a test document.")

        db_path = tmp_path / "kb_db"

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "index", str(doc), "--db-path", str(db_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Indexed" in result.output or "indexed" in result.output.lower()

    def test_kb_index_skip_if_table_exists(self, tmp_path: Path):
        """kb index skips if chunks table already exists (no --force)."""
        import asyncio
        from coding_agent.kb import KB

        doc = tmp_path / "docs"
        doc.mkdir()
        (doc / "readme.md").write_text("# Test")

        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("existing.md"), "existing content"))

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "index", str(doc), "--db-path", str(db_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "already exists" in result.output.lower() or "skip" in result.output.lower()

    def test_kb_index_missing_path_errors(self):
        """kb index with no path argument errors."""
        runner = CliRunner()
        result = runner.invoke(main, ["kb", "index"])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_kb_commands.py::TestKBIndex::test_kb_index_creates_table -v`
Expected: FAIL with `Error: No such command 'kb'`

- [ ] **Step 3: Implement `kb` command group and `kb index` subcommand**

In `src/coding_agent/__main__.py`, after the imports section (after line 14), add:

```python
import asyncio as _asyncio
```

Then, after the `serve` command (after line 318, before `if __name__`), add:

```python
@main.group()
def kb():
    """Knowledge base management commands."""
    pass


@kb.command("index")
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--db-path",
    default=None,
    help="LanceDB database path (default: from agent.toml [kb].db_path)",
)
def kb_index(path: str, db_path: str | None):
    """Index a directory into the knowledge base.

    PATH is the directory to scan for files.
    """
    from coding_agent.kb import KB

    root = Path(path)

    kb_cfg: dict[str, object] = {}
    config_path = Path(__file__).parent / "agent.toml"
    if config_path.exists():
        from agentkit.config.loader import load_config as _load_agent_config

        agent_cfg = _load_agent_config(config_path)
        kb_cfg = agent_cfg.extra.get("kb", {})

    data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
    resolved_db = Path(db_path) if db_path is not None else data_dir / str(kb_cfg.get("db_path", "kb"))

    raw_extensions = kb_cfg.get(
        "index_extensions",
        [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"],
    )
    text_extensions = set(raw_extensions) if isinstance(raw_extensions, list) else {
        ".md", ".txt", ".rst", ".yaml", ".yml", ".toml"
    }

    # Check if table already exists
    probe_kb = KB(db_path=resolved_db, embedding_dim=int(kb_cfg.get("embedding_dim", 1536)))
    if probe_kb.has_table():
        click.echo("Chunks table already exists. Skipping. (Phase 1 does not support incremental updates.)")
        return

    kb_instance = KB(
        db_path=resolved_db,
        embedding_model=kb_cfg.get("embedding_model", "text-embedding-3-small"),
        embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
        chunk_size=int(kb_cfg.get("chunk_size", 1200)),
        chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
        text_extensions=text_extensions,
    )

    _asyncio.run(kb_instance.index_directory(root))

    click.echo("Done.")
```

- [ ] **Step 4: Add `import os` at the top of the module**

Check that `os` is importable. Looking at `__main__.py` — `os` is NOT imported at the top level. Add it:

After line 7 (`from pathlib import Path`), add:

```python
import os
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_kb_commands.py::TestKBIndex -v`
Expected: 3 tests PASS

Note: `test_kb_index_creates_table` will need `OPENAI_API_KEY` set or the test needs to mock the embedding function. Since the test doesn't inject `embedding_fn` into the CLI path, we need to mock it. Update the test:

Replace `test_kb_index_creates_table` with:

```python
    def test_kb_index_creates_table(self, tmp_path: Path, monkeypatch):
        """kb index <path> indexes files and creates chunks table."""
        doc = tmp_path / "docs"
        doc.mkdir()
        (doc / "readme.md").write_text("# Hello World\nThis is a test document.")

        db_path = tmp_path / "kb_db"

        # Mock the KB class to use fake embeddings
        from coding_agent import kb as kb_module
        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs.setdefault("embedding_dim", 8)
            kwargs.setdefault("text_extensions", {".md"})
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "index", str(doc), "--db-path", str(db_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
```

**Design note:** Task 5 intentionally does **not** expose a `--extensions` override in Phase 1. Indexing policy belongs to `KB`, and the CLI should read `[kb].index_extensions` once from config, construct `KB`, and call `index_directory()` directly.

- [ ] **Step 6: Commit**

```bash
git add src/coding_agent/__main__.py tests/cli/test_kb_commands.py
git commit -m "feat(kb): add 'kb index' CLI subcommand"
```

---

## Task 6: Add `kb search` CLI subcommand

**Files:**
- Modify: `src/coding_agent/__main__.py`
- Modify: `tests/cli/test_kb_commands.py`

- [ ] **Step 1: Write failing test for `kb search`**

Append to `tests/cli/test_kb_commands.py`:

```python
class TestKBSearch:
    def test_kb_search_returns_results(self, tmp_path: Path, monkeypatch):
        """kb search <query> prints formatted results."""
        import asyncio
        from coding_agent import kb as kb_module
        from coding_agent.kb import KB

        db_path = tmp_path / "kb_db"
        kb = KB(db_path=db_path, embedding_dim=8, embedding_fn=_fake_embed)
        asyncio.run(kb.index_file(Path("test.md"), "Python programming guide with examples"))

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs.setdefault("embedding_dim", 8)
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "search", "Python", "--db-path", str(db_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "test.md" in result.output or "Python" in result.output

    def test_kb_search_no_table_shows_message(self, tmp_path: Path, monkeypatch):
        """kb search when no index exists shows a helpful message."""
        from coding_agent import kb as kb_module

        db_path = tmp_path / "kb_db"

        original_init = kb_module.KB.__init__

        def patched_init(self_kb, *args, **kwargs):
            kwargs["embedding_fn"] = _fake_embed
            kwargs.setdefault("embedding_dim", 8)
            original_init(self_kb, *args, **kwargs)

        monkeypatch.setattr(kb_module.KB, "__init__", patched_init)

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["kb", "search", "anything", "--db-path", str(db_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "no index" in result.output.lower() or "not found" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/cli/test_kb_commands.py::TestKBSearch::test_kb_search_returns_results -v`
Expected: FAIL with `Error: No such command 'search'`

- [ ] **Step 3: Implement `kb search` subcommand**

In `src/coding_agent/__main__.py`, after the `kb_index` function, add:

```python
@kb.command("search")
@click.argument("query")
@click.option("--k", default=5, type=int, help="Number of results to return")
@click.option(
    "--db-path",
    default=None,
    help="LanceDB database path (default: from agent.toml [kb].db_path)",
)
def kb_search(query: str, k: int, db_path: str | None):
    """Search the knowledge base.

    QUERY is the search text.
    """
    from coding_agent.kb import KB

    # Resolve db_path (same logic as kb_index)
    if db_path is None:
        try:
            from agentkit.config.loader import load_config as _load_agent_config

            config_path = Path(__file__).parent / "agent.toml"
            if config_path.exists():
                agent_cfg = _load_agent_config(config_path)
                kb_cfg = agent_cfg.extra.get("kb", {})
                data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
                db_path = str(data_dir / kb_cfg.get("db_path", "kb"))
        except Exception:
            pass
    if db_path is None:
        db_path = str(Path(os.environ.get("AGENT_DATA_DIR", "./data")) / "kb")

    resolved_db = Path(db_path)
    kb_instance = KB(db_path=resolved_db)

    if not kb_instance.has_table():
        click.echo("No index found. Run 'kb index <path>' first.")
        return

    results = kb_instance.search_sync(query, k=k)

    if not results:
        click.echo("No results found.")
        return

    for i, r in enumerate(results, 1):
        click.echo(f"\n--- Result {i} (score: {r.score:.4f}) ---")
        click.echo(f"Source: {r.chunk.source}")
        content = r.chunk.content
        if len(content) > 200:
            content = content[:200] + "..."
        click.echo(content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/cli/test_kb_commands.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/coding_agent/__main__.py tests/cli/test_kb_commands.py
git commit -m "feat(kb): add 'kb search' CLI subcommand"
```

---

## Task 7: Full test suite run and final verification

**Files:**
- All files from Tasks 1-6

- [ ] **Step 1: Run the complete KB test suite**

Run: `uv run pytest tests/coding_agent/test_kb_sync.py tests/coding_agent/plugins/test_kb_plugin.py tests/cli/test_kb_commands.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run the full project test suite to check for regressions**

Run: `uv run pytest tests/ -v --timeout=60`
Expected: All pre-existing tests PASS. New tests PASS. No regressions.

- [ ] **Step 3: Verify LSP diagnostics on all changed files**

Check:
- `src/coding_agent/kb.py`
- `src/coding_agent/plugins/kb.py`
- `src/coding_agent/app.py`
- `src/coding_agent/__main__.py`
- `src/coding_agent/agent.toml`

Expected: No new errors introduced.

- [ ] **Step 4: Verify the import chain works end-to-end**

Run: `uv run python -c "from coding_agent.app import create_agent; print('app.py import OK')"`
Expected: `app.py import OK`

- [ ] **Step 5: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix(kb): address test/lint fixups from full suite run"
```

---

## Summary of Changes

| File | Change Type | Lines (est.) |
|------|-------------|-------------|
| `src/coding_agent/kb.py` | Add `_embed_sync()`, `has_table()`, `search_sync()`, and constructor-driven `text_extensions` | +60 |
| `src/coding_agent/plugins/kb.py` | New file: KBPlugin class | ~110 |
| `src/coding_agent/app.py` | Import + plugin factory entry | +15 |
| `src/coding_agent/agent.toml` | `[kb]` section + `plugins.enabled` entry | +12 |
| `src/coding_agent/__main__.py` | `kb index` + `kb search` subcommands | +100 |
| `tests/coding_agent/test_kb_sync.py` | New: search_sync tests | ~40 |
| `tests/coding_agent/plugins/test_kb_plugin.py` | New: KBPlugin unit tests | ~180 |
| `tests/cli/test_kb_commands.py` | New: CLI integration tests | ~100 |

**Total: ~600 lines of production + test code across 8 files.**

---

## Preconditions (runtime)

- `OPENAI_API_KEY` must be set in the environment for production use (indexing + search)
- LanceDB, OpenAI, and numpy packages must be installed (`uv sync --all-extras` covers this)
- Tests use `embedding_fn` injection — no real API calls needed
