# KBPlugin Phase 1 Design Spec

**Date**: 2026-04-08
**Goal**: Validate RAG value in single-pod coding-agent by adding automatic knowledge base grounding.
**Success Metric**: Tool call round reduction >= 20% on documentation-heavy prompts.

---

## 1. Scope

Phase 1 adds a `KBPlugin` that automatically injects relevant KB chunks into LLM context via the `build_context` hook. No tool exposure, no DocIndex Protocol changes, no multi-pod considerations.

**In scope:**
- `KBPlugin` with `mount` + `build_context` hooks
- CLI subcommands: `kb index`, `kb search`
- Configuration via `[kb]` section in `agent.toml`
- Unit tests with fake embeddings

**Out of scope:**
- `kb_search` tool for LLM (deferred until grounding value is validated)
- DocIndex Protocol changes (Path B: use KB class directly)
- Incremental/real-time index updates
- Multi-pod / pgvector migration (independent Phase 2 track)

---

## 2. Architecture

### 2.1 New File

`src/coding_agent/plugins/kb.py` — KBPlugin implementation (~100 lines).

### 2.2 Modified Files

- `src/coding_agent/app.py` — Add `"kb"` to `plugin_factories` (conditional on `[kb].enabled`)
- `src/coding_agent/agent.toml` — Add `[kb]` configuration section
- `src/coding_agent/__main__.py` — Add `kb index` and `kb search` CLI subcommands

### 2.3 Unchanged

- `src/coding_agent/kb.py` — Reused as-is (Path B: no DocIndex Protocol)
- `src/agentkit/` — No framework changes

---

## 3. KBPlugin Design

### 3.1 Interface

```python
class KBPlugin:
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
    ) -> None: ...

    def hooks(self) -> dict[str, Callable]:
        return {
            "mount": self.do_mount,
            "build_context": self.build_context,
        }
```

### 3.2 mount

- Instantiate `KB(db_path, embedding_model, embedding_dim, chunk_size, chunk_overlap, embedding_fn)`
- Connect to LanceDB
- Check if chunks table exists (used by `build_context` to skip search when empty)
- Does NOT index — indexing is an explicit CLI operation

### 3.3 build_context

Change detection with two rules:

| Condition | Action |
|-----------|--------|
| No cache OR new user message content differs from last query | Search KB |
| Otherwise | Reuse cached grounding messages |

Steps when search is triggered:
1. Extract query from tape: reverse-scan for latest user message content
2. Call `KB.search(query, k=top_k)`
3. Format results into a single grounding message
4. Cache results + update snapshot

Snapshot state:

```python
@dataclass
class _SearchSnapshot:
    last_user_msg: str         # query used for last search
    result_cache: list[dict]   # cached grounding messages
```

When table does not exist or search returns empty results: return empty list (no grounding injected).

### 3.4 Grounding Message Format

All chunks in a single system message:

```
[KB] The following code/documentation snippets may be relevant:

--- src/agentkit/runtime/pipeline.py ---
Pipeline — Bub-style linear stage runner for agent turns.
Stages: resolve_session → load_state → build_context → run_model ...

--- docs/architecture.md ---
## Plugin System
All plugins implement the Plugin protocol with a state_key ...
```

Design decisions:
- **Single message** — chunks are one result set, not independent items
- **`[KB]` prefix** — aligned with MemoryPlugin's `[Memory]` prefix
- **No score** — top-k already filters for relevance; score values (distance vs similarity) are uninterpretable for LLM
- **Source path included** — enables LLM to follow up with file_read
- **Per-chunk truncation: 500 chars** — 5 chunks x 500 chars ~ 700 tokens, < 10% of typical context

---

## 4. Configuration

### 4.1 agent.toml

```toml
[kb]
enabled = false
db_path = "./data/kb"
embedding_model = "text-embedding-3-small"
embedding_dim = 1536
chunk_size = 1200
chunk_overlap = 200
top_k = 5
index_extensions = [".md", ".txt", ".rst", ".yaml", ".yml", ".toml"]
```

`enabled` controls plugin registration independently from `plugins.enabled` list because:
- KB requires an OpenAI API key for embedding (not universally available)
- Indexing has I/O cost, should not happen implicitly
- Other plugins are unconditionally usable; KB has preconditions

### 4.2 app.py Integration

```python
kb_cfg = cfg.extra.get("kb", {})

if kb_cfg.get("enabled", False):
    plugin_factories["kb"] = lambda: KBPlugin(
        db_path=Path(kb_cfg.get("db_path", "./data/kb")),
        embedding_model=kb_cfg.get("embedding_model", "text-embedding-3-small"),
        embedding_dim=int(kb_cfg.get("embedding_dim", 1536)),
        chunk_size=int(kb_cfg.get("chunk_size", 1200)),
        chunk_overlap=int(kb_cfg.get("chunk_overlap", 200)),
        top_k=int(kb_cfg.get("top_k", 5)),
        index_extensions=kb_cfg.get("index_extensions", [".md", ".txt"]),
        # embedding_fn is not configurable via TOML — it exists only for
        # test injection (fake embeddings). Production always uses OpenAI.
    )
```

---

## 5. CLI Subcommands

Added to `__main__.py` alongside existing `stats` / `serve` subcommands.

### 5.1 `kb index`

```
coding-agent kb index <path> [--extensions .md,.txt,.py] [--force]
```

- `<path>`: Directory to index
- `--extensions`: Override file extensions (default from config)
- `--force`: Delete existing table and rebuild
- Without `--force`: skip if table exists (print hint to use `--force`)
- Reads `[kb]` config from agent.toml for db_path, embedding params
- Shows progress bar (KB.index_directory already supports this)

### 5.2 `kb search`

```
coding-agent kb search <query> [--k 5]
```

- Debug/verification command
- Prints formatted results to stdout
- ~10 lines of implementation

---

## 6. Testing

### 6.1 Unit Tests (`tests/coding_agent/plugins/test_kb_plugin.py`)

| Test | Validates |
|------|-----------|
| `test_build_context_no_table` | Empty table returns empty list |
| `test_build_context_first_call` | First call triggers search, returns grounding messages |
| `test_build_context_cache_hit` | Same user message reuses cache, does not call embed |
| `test_build_context_new_user_msg` | New user message triggers fresh search |
| `test_build_context_empty_results` | No results returns empty list |
| `test_grounding_format` | `[KB]` prefix, source paths, chunk truncation at 500 chars |
| `test_mount_initializes_kb` | mount creates KB instance, checks table existence |

### 6.2 CLI Tests (`tests/cli/test_kb_commands.py`)

| Test | Validates |
|------|-----------|
| `test_kb_index_creates_table` | `kb index <path>` indexes files and creates table |
| `test_kb_index_force_rebuild` | `--force` drops and rebuilds table |
| `test_kb_search_returns_results` | `kb search <query>` prints formatted output |

### 6.3 Test Infrastructure

- All tests use `embedding_fn` parameter to inject fake embeddings (KB constructor already supports this)
- LanceDB uses `tmp_path` fixture (temporary directory, auto-cleaned)
- No real API calls in tests
- No E2E tests — Phase 1 validation metric (tool call reduction) requires manual testing with real LLM

---

## 7. Future Work (Not Phase 1)

- **kb_search tool**: Expose via ToolRegistry + `@tool` decorator if grounding alone proves insufficient
- **DocIndex Protocol redesign**: If RAG is validated, redesign Protocol to reflect real needs (chunking, hybrid search, typed returns)
- **Incremental index updates**: Detect file changes and update index without full rebuild
- **Query rewriting**: Use LLM or recent tool results to refine search query
- **pgvector migration**: Part of independent multi-pod Phase 2 track
