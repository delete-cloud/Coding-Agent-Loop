# Context System Current State

Date: 2026-05-19
Branch: `codex/context-system-g12-current-state`

This document records the current context, retrieval, memory, observability, and evaluation entrypoints before implementing G13-G24. It is intentionally descriptive; code and tests remain the source of truth.

## Baseline Boundaries

- `agentkit` owns generic context assembly. The `build_context` hook is a collect-all hook in `src/agentkit/runtime/hookspecs.py`, and `src/agentkit/runtime/pipeline.py` calls it during `_stage_build_context` before composing messages through `src/agentkit/context/builder.py`.
- `coding_agent` owns product-specific context sources. `src/coding_agent/plugins/kb.py` and `src/coding_agent/plugins/memory.py` are application plugins that currently inject grounding through `build_context`.
- The AgentKit pipeline already has context windowing, summarization, runtime-context grounding, and incremental context rebuild support. The context-system phase must extend those hooks, not replace the pipeline.
- Durable runtime work from G00-G11 is baseline infrastructure. This phase must preserve JSONL tape compatibility, durable runtime semantics, and the existing durable runtime verification suite.

## Existing Retrieval

`src/coding_agent/kb.py` provides a LanceDB-backed `KB` with:

- `DocumentChunk(id, content, source, metadata)`
- `KBSearchResult(chunk, score)`
- async and sync vector search
- optional hybrid search
- deterministic fake embedding support for tests

Current chunk metadata contains only chunk index and total chunks. There is no first-class repo identity, file hash, line range, language, symbol, test-failure source, or source-kind model.

`src/coding_agent/plugins/kb.py` provides `KBPlugin` with:

- `mount` to create a `KB` and detect whether the `chunks` table exists
- `build_context` to search by the latest user message
- one-message caching based on latest user message text
- LLM-visible system grounding formatted as `[KB] Relevant context:`

The plugin default index extensions are documentation/config focused (`.md`, `.txt`, `.rst`, `.yaml`, `.yml`, `.toml`), while the lower-level `KB` default text extensions also include code formats such as Python, JavaScript, TypeScript, HTML, CSS, shell, JSON, and config files.

## Existing Memory

`src/agentkit/directive/types.py` defines `MemoryRecord(summary, tags, importance)` as a generic directive.

`src/coding_agent/plugins/memory.py` currently:

- records working memories from `MemoryRecord` directives
- compacts topic-end working memories into long-term memory records
- persists long-term memory records as JSONL entries of kind `memory_record`
- reloads persisted records on mount and applies importance decay
- injects memory as system messages formatted as `[Memory] <summary> (tags: ...)`

Current memory has no explicit evidence model. It stores summaries, tags, and importance only. Because memory is injected as system-role grounding, later goals must make memory visibly reference context with evidence rather than instruction authority.

## Existing Evaluation

`src/coding_agent/evaluation/adapter.py` converts a JSONL tape plus YAML golden spec into `EvaluationTestCase` objects:

- tape parsing uses JSONL entries and preserves the existing tape format
- turn extraction uses `agentkit.tape.extract.extract_turns`
- visible extraction is the default
- v1 supports single-turn tapes only
- observed tool calls and expected tools are kept separate

`src/coding_agent/evaluation/metrics.py` provides optional DeepEval metric adaptation. Tests already use fake or monkeypatched dependencies; no external judge service is required for the existing deterministic tests.

There is no manifest-driven evaluation runner for context retrieval, context-pack composition, memory evidence, or retrieval observability yet.

## Existing Observability

`agentkit` owns provider-neutral observation primitives and runtime spans. `src/agentkit/runtime/pipeline.py` already emits `runtime.stage.build_context` spans with safe correlation attributes.

`src/coding_agent/observability.py` owns product-level OTLP/Langfuse export. The exporter drops attributes whose keys include sensitive substrings:

- `content`
- `message`
- `prompt`
- `result`
- `secret`
- `text`

Current retrieval and memory code do not emit retrieval-specific spans or safe retrieval counters. Future retrieval observability must keep this same no-raw-content policy.

## Gaps For G13-G24

- Repo-aware retrieval: missing repo-aware chunk metadata, source-kind modeling, deterministic indexing expectations, and query shaping beyond latest user message vector search.
- Testing failure retrieval: no structured ingest/search path for pytest or command failure evidence.
- Context packs: no typed pack model that can combine retrieval, test failures, memory evidence, and runtime hints before rendering through `build_context`.
- Retrieval observability: no safe counters for candidate counts, selected counts, source kinds, cache hits, or retrieval latency.
- Evaluation harness: adapter exists, but no manifest/runner and no context-system golden cases.
- Memory with evidence: memory records lack evidence references, and injected memory currently appears as system-role content without explicit reference/evidence semantics.

## Implementation Rules For Later Goals

- Keep AgentKit Core generic.
- Use `build_context` hooks for context injection.
- Do not rewrite `_stage_build_context` or `ContextBuilder` unless a later goal explicitly proves a generic boundary change is required.
- Keep JSONL compatibility for tape and memory records.
- Use fake embedders, local fixtures, and deterministic tests.
- Do not add raw prompt, content, message, result, secret, or text values to trace attributes.
- Do not implement schedule, sandbox, desktop, bridge, or proactive autonomous-agent features in this phase.
