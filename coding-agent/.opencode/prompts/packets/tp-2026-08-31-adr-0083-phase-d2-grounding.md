# Task Packet

packet_id: tp-2026-08-31-adr-0083-phase-d2-grounding
packet_revision: 1
role: implementer
baseline_ref: origin/main
baseline_sha: ba94868da6d5eaf745d0a502b8c948c81461e924
branch: feat/adr-0083-phase-d2-grounding

## Goal

Implement ADR-0083 Phase D2: capability-declared context hooks receive only host-provided immutable values, and semantic-memory recall becomes a stable host-owned grounding snapshot rather than a plugin-held live-store query.

## Scope

- Add an internal, host-owned context-input provider to the legacy `Pipeline` compatibility path. It may inspect `PipelineContext`; plugins cannot obtain the provider.
- Snapshot the provider output before any `build_context` hook runs. Key immutable capability inputs by plugin ID and derive a separate host-curated compatibility view for legacy consumers.
- Invoke a capability-declared `PENDING_FACT` `build_context` hook with only its plugin-specific `input`. Do not pass `PipelineContext`, `HookRuntime`, mutable `Tape`, stores, executor, mailbox/cursor, or another plugin's input, even when the callable declares those names or `**kwargs`.
- Keep undeclared hooks on the compatibility calling convention until their named Phase D slice. Do not make the whole registry deny undeclared plugins in this task.
- Add a host-owned semantic grounding provider. It owns `SafeSemanticMemoryIndex`, `MemoryReviewStore`, semantic topic store/index, recall planning, and snapshot reuse.
- Define frozen semantic grounding message/input values with stable `input_id`, `query_digest`, `hit_count`, and ordered `(role, content)` messages. The plugin input contains no `ContextPack` object or mutable mapping.
- Derive `input_id` from session identity and the currently selected source identity: the latest admitted `USER_STEER` or `SUBAGENT_MESSAGE` `message_id` when a runtime prompt is selected, otherwise the latest windowed user `Entry.id`. Runtime-prompt selection keeps precedence. A new user entry, new runtime prompt, or window/handoff that selects a different user entry creates a new snapshot; source-store changes alone do not.
- Make `SemanticMemoryPlugin` declare `PluginCapability.PENDING_FACT`. It owns no semantic index, review store, topic store/index, derived-index cache, or host provider. It only renders its frozen input to fresh message dictionaries.
- Keep the selected semantic `ContextPack` outside the plugin-input mapping and record it through host-owned stashing so run metadata behavior remains intact without plugin access to `PipelineContext.config`.
- Remove `SEMANTIC_MEMORY_GROUNDING_MARKER_KEY`. Pass only a frozen `{query_digest, hit_count}` summary through a host-curated compatibility view to the existing KB hook when that hook explicitly requests context inputs. Do not expose semantic messages, `ContextPack`, the provider, or source stores.
- Preserve semantic enable/disable behavior, recall floors, runtime-prompt precedence, topic recall, accepted-memory recall, incremental context rebuilds, maintenance APIs, and Helm/bootstrap configuration.

### Intended internal interfaces

These names may move within the allowed files, but their ownership and signatures are fixed for this task:

```python
@dataclass(frozen=True, slots=True)
class GroundingMessage:
    role: str
    content: str

@dataclass(frozen=True, slots=True)
class SemanticMemoryGroundingInput:
    input_id: str
    query_digest: str
    hit_count: int
    messages: tuple[GroundingMessage, ...]

class BuildContextInputProvider(Protocol):
    async def snapshot(self, ctx: PipelineContext) -> Mapping[str, object]: ...
```

The provider retains and records the selected `ContextPack` in host-owned state outside the returned plugin-input mapping. The mapping value for `semantic_memory` is exactly `SemanticMemoryGroundingInput`; only that value reaches `SemanticMemoryPlugin`.

### Allowed production files

- `src/agentkit/plugin/registry.py`
- `src/agentkit/runtime/pipeline.py`
- `src/coding_agent/core/app.py`
- `src/coding_agent/plugins/semantic_memory.py`
- `src/coding_agent/plugins/kb.py`
- `src/coding_agent/topics/semantic_grounding.py` (new host-provider implementation)
- `src/coding_agent/topics/context_pack.py` (only if an existing host-owned stash helper must change)

Allowed tests are the matching registry, pipeline, incremental-context, semantic-memory, KB, context-system smoke, bootstrap, memory-switch, context-pack, and session-manager runtime tests.

## Authority

- `docs/adr/0083-host-coordinated-durable-agentkit-runtime.md`, especially the plugin isolation and Phase D sections.
- `docs/adr/0084-stage-phase-d-capability-inputs-and-recovery-cutovers.md`, especially capability-scoped values and the Phase D2 snapshot identity rules.
- `postmortem/patterns/PM-0001-address-code-review-issues.md`
- `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
- `postmortem/patterns/PM-0010-route-incremental-context-append-through-tapeview.md`

## Non-goals

- No durable `CommandMailbox`, mailbox generation/cut, approval/subagent migration, effect `settled` removal, or rank removal.
- No coordinator recovery, unknown-effect reconciliation, takeover, or permit-quiescence work.
- No SQLite/PostgreSQL `CommitPort` or durable `EffectExecutor` implementation.
- No canonical `EventRecord` or wire cutover.
- No Phase C public runtime signature/export change and no new `EngineStepInput` variant.
- No full KB capability migration. KB remains a compatibility hook and receives only the frozen semantic summary needed for defer behavior.
- No migration of semantic maintenance services out of the legacy host session context. Capability-declared hooks must still be unable to receive that context.
- No provider/model retry or core tool execution change.

## Acceptance criteria

- A capability-declared `build_context` hook receives only its plugin-specific frozen input, even if its callable declares `ctx`, `runtime`, `tape`, or `**kwargs`.
- Classified hooks cannot reach `PipelineContext`, `HookRuntime`, mutable `Tape`, store/index instances, `Toolset`, host executor, runtime-message bus/cursor, or dispatch capability through hook arguments or their plugin input.
- Semantic grounding source stores are read once per stable `input_id`; repeated incremental/full context rebuilds reuse byte-for-byte equivalent messages.
- A store mutation with unchanged `input_id` does not change grounding. A new user entry, a new admitted runtime prompt, or a window/handoff that changes the selected user entry creates a new snapshot.
- `SemanticMemoryPlugin` owns no store/index/provider/cache and declares `PENDING_FACT`.
- Semantic context-pack run metadata remains equivalent and is recorded by host code.
- KB defer behavior receives only the frozen `{query_digest, hit_count}` summary and cannot observe semantic messages or `ContextPack`; no semantic grounding marker remains in `PipelineContext.config`.
- Disabled semantic memory constructs no provider/plugin and performs no semantic store read.
- Existing runtime-prompt precedence, recall thresholds, topic/accepted-memory results, maintenance APIs, Helm configuration, and context rendering behavior remain covered.
- Phase C public runtime signatures and exports remain unchanged.
- One bounded P1/P2 review, at most one accepted-fix pass, and one verifier retest complete.

## Target tests

- `uv run pytest tests/agentkit/plugin/test_registry.py tests/agentkit/runtime/test_pipeline.py tests/agentkit/test_incremental_context.py -q`
- `uv run pytest tests/coding_agent/plugins/test_semantic_memory.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/test_context_system_smoke.py tests/coding_agent/test_context_pack.py -q`
- `uv run pytest tests/coding_agent/test_bootstrap.py tests/coding_agent/test_memory_switch.py tests/ui/test_session_manager_runtime.py -q`
- `uv run ruff check src/agentkit/plugin/registry.py src/agentkit/runtime/pipeline.py src/coding_agent/core/app.py src/coding_agent/plugins/semantic_memory.py src/coding_agent/plugins/kb.py src/coding_agent/topics/semantic_grounding.py src/coding_agent/topics/context_pack.py tests/agentkit/plugin/test_registry.py tests/agentkit/runtime/test_pipeline.py tests/agentkit/test_incremental_context.py tests/coding_agent/plugins/test_semantic_memory.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/test_context_system_smoke.py tests/coding_agent/test_context_pack.py tests/coding_agent/test_bootstrap.py tests/coding_agent/test_memory_switch.py tests/ui/test_session_manager_runtime.py`
- `uv run ruff format --check src/agentkit/plugin/registry.py src/agentkit/runtime/pipeline.py src/coding_agent/core/app.py src/coding_agent/plugins/semantic_memory.py src/coding_agent/plugins/kb.py src/coding_agent/topics/semantic_grounding.py src/coding_agent/topics/context_pack.py tests/agentkit/plugin/test_registry.py tests/agentkit/runtime/test_pipeline.py tests/agentkit/test_incremental_context.py tests/coding_agent/plugins/test_semantic_memory.py tests/coding_agent/plugins/test_kb_plugin.py tests/coding_agent/test_context_system_smoke.py tests/coding_agent/test_context_pack.py tests/coding_agent/test_bootstrap.py tests/coding_agent/test_memory_switch.py tests/ui/test_session_manager_runtime.py`

## Loop policy

- Engineer writes failing behavioral tests before implementation and runs each focused red test.
- Engineer implements the smallest correct change and runs the exact target tests.
- Reviewer reviews only the resulting diff and affected tests, reporting P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

## Stop conditions

- Stop if implementation requires changing a frozen Phase C request, proposal, outcome, port, result signature, export, or `EngineStepInput` variant.
- Stop if a capability-declared hook cannot be isolated without receiving `PipelineContext` or `HookRuntime`.
- Stop if stable snapshot reuse requires new persistence or schema work; that belongs in a later durable-host slice.
- Stop if preserving KB defer behavior requires migrating KB execution/storage capability in this task.
- Stop if semantic maintenance endpoints require exposing host store objects to a capability-declared plugin.
- Stop after one `review -> accepted fixes -> retest` cycle.
