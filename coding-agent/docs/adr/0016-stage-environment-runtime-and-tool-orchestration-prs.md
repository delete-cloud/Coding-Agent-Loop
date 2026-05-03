# ADR-0016: Stage environment runtime and tool orchestration PRs

**Status**: Proposed
**Date**: 2026-05-02

## Context

ADR-0014 introduced `ExecutionBinding` so a session can record where execution is bound without mixing that concern with runtime ownership. PR 86 then made HTTP session runtime metadata durable by preserving provider, model, base URL, and step limit across restart and REPL runtime replacement. The code now has durable session metadata for model execution and workspace binding, but tool execution still enters the system through local `workspace_root` assumptions in `CoreToolsPlugin`, file tools, shell tools, and session runtime creation.

The next work affects several areas that future agents may implement in parallel: local environment execution, run context, tool governance, runtime messaging, tool proxying, cloud workspace execution, and subagent orchestration. These areas depend on each other. Implementing cloud workspace execution or full subagent orchestration before the execution and runtime boundaries are stable would force remote transport, agent identity, tape labeling, approval routing, and tool policy into interfaces that are still local-path oriented.

This ADR records the PR order and scope gates before implementation begins. It is intended to let multiple agents contribute without overlapping write scopes or introducing incompatible abstractions.

## Decision

Use `agentkit` for product-agnostic protocols, runtime primitives, and
execution envelopes. Keep `coding_agent` responsible for concrete product
integrations, HTTP/UI ownership, local shell and file implementations, provider
runtime persistence, MCP configuration, skills, memory, KB, and web-search
plugins.

The ownership boundary is:

- `agentkit.environment`: environment protocols and shared types only.
- `agentkit.runtime`: per-run identity, context budget references, trace
  metadata, tool execution boundaries, and subagent derivation primitives.
- `agentkit.tools`: generic tool provider, validation, execution result, and
  policy/approval interfaces.
- `agentkit.channel` or `agentkit.runtime`: inbound runtime message protocols
  and idempotent consumption semantics.
- `coding_agent`: `LocalEnvironment`, `SessionManager`, `ExecutionBinding`,
  `BindingResolver`, HTTP owner routing, leases, REPL model switching, provider
  runtime metadata persistence, concrete file/shell/sandbox tools, MCP plugin
  implementation, and product-specific tool risk policy.

Implement the work in the following PR sequence.

1. PR 1: Add `Environment` and `LocalEnvironment`
   - Add an `agentkit.environment` protocol for file, shell, patch, glob, grep,
     and workspace identity operations.
   - Keep the protocol and common callable types in `agentkit`.
   - Add the local implementation in `coding_agent`, because it depends on
     `coding_agent.tools.file_ops`, shell behavior, sandbox policy, and current
     app wiring.
   - Extend binding resolution so `LocalExecutionBinding` can produce an environment, while `CloudWorkspaceBinding` keeps an explicit not-implemented failure path.
   - Update `CoreToolsPlugin` to depend on an environment boundary while keeping backward compatibility for existing `workspace_root` construction.
   - Keep runtime provider metadata from PR 86 in session metadata. Do not duplicate it inside the environment.

2. PR 2: Add lightweight runtime context
   - Add a small `AgentRunContext` or `RunContext` in `agentkit.runtime` for
     per-run identity and runtime references.
   - Include `session_id`, `run_id`, `agent_id`, `parent_run_id`, `environment`,
     `context_budget`, and `trace_metadata`.
   - Keep provider, model, base URL, and max steps sourced from session runtime metadata introduced by PR 86. PR 2 may snapshot or reference those fields for tracing, but it must not make `AgentRunContext` their new durable source of truth.
   - Keep `PipelineContext`, `ContextBuilder`, and `TapeView` focused on prompt assembly and pipeline state.

3. PR 3: Add `Toolset` governance
   - Keep `ToolRegistry` as the registration mechanism.
   - Add a generic tool execution boundary in `agentkit.tools` or
     `agentkit.runtime` because `approve_tool_call`, `execute_tool`, and
     `execute_tools_batch` already live in the agentkit pipeline layer.
   - Move only framework-level concepts upward: tool provider protocols, schema
     validation, timeout/retry wrapping points, execution result envelopes, tape
     recording hooks, and policy/approval interfaces.
   - Scope single-tool retries to the provider hook that raised. Providers that
     return `UNHANDLED_TOOL_RESULT` must do so before I/O or state mutation.
   - Treat approval hook lookup failures, approval hook exceptions, async
     approval await failures, non-`Directive` returns, and directive executor
     exceptions as fail-closed denials. Executor failures keep the directive
     reason when one exists; other approval failures use `reason="policy"`.
   - Treat `CoreToolsPlugin` and `MCPPlugin` as `coding_agent` tool providers.
   - Keep concrete safe-tool lists, HTTP approval, TUI approval, shell/file risk
     policy, and MCP implementation details in `coding_agent`.

4. PR 4: Add runtime message bus and runtime context injection
   - Add an inbound `RuntimeMessageBus` protocol in `agentkit.channel` or
     `agentkit.runtime`.
   - Cover `interrupt`, `user_steer`, `approval_decision`,
     `subagent_message`, and `system_notice`, with consumed cursor or
     idempotency semantics.
   - Keep runtime message consumption cursor-based so applying the same cursor
     is idempotent and ownership layers can persist the cursor later.
   - Keep cursors consumer-owned: the agentkit pipeline cursor only covers
     messages agentkit interprets, while product approval stores consume
     `approval_decision` messages with their own cursor.
   - Consume runtime messages only at pipeline safe points: stage boundaries,
     between model rounds, and before tool execution.
   - Treat `interrupt` as a fail-fast turn stop at the next safe point without
     advancing the pipeline cursor for that batch.
   - Keep outbound event streams separate from inbound runtime control.
   - Keep HTTP SSE, owner routing, and UI event queues in `coding_agent`.
   - Add prompt-time runtime context injection for run id, agent id, environment summary, cwd, elapsed time, context budget, and active approvals.

5. PR 5: Add tool proxy for dynamic toolsets
   - Add stable `search_tools` and `call_tool` affordances for MCP and dynamically loaded tools.
   - Add separate `get_proxy_tools` and `execute_proxy_tool` hooks so dynamic
     providers do not compete with directly exposed core tool names.
   - Treat `search_tools` and `call_tool` as proxy affordances whose outer
     approval is skipped; the nested target tool remains governed by Toolset
     approval.
   - Keep core file, shell, planner, patch, web search, and subagent tools directly visible until there is evidence that proxying them improves behavior.
   - Route proxied execution through the same `Toolset` schema validation,
     approval, timeout, retry, and result-envelope path.
   - Document that mixed direct/proxy batches execute sequentially until a later
     partitioning PR is justified.

Cloud workspace execution and full subagent orchestration are tracked as follow-up implementation areas, not as PR 1 work.

- Cloud workspace execution must wait until PR 1 defines the environment protocol and local parity tests. Until then, cloud bindings must fail explicitly rather than partially executing through local path assumptions.
- Full subagent orchestration must wait until PR 2, PR 3, and PR 4 define run identity, environment sharing, tool governance, inbound messages, and prompt-time runtime injection. Until then, the existing subagent tool may remain as-is.
- Generic subagent foundations may move into `agentkit`: child run id, parent
  run id, child tape trace metadata, and an interface for deriving child
  context from parent context. Full coding subagent orchestration must stay in
  `coding_agent` until write leases, tool filtering, workspace policy, and
  approval behavior are stable enough to generalize.

Parallel agents may work on design review tasks for cloud workspace and subagent orchestration while PR 1 is being implemented. Those agents should produce interface requirements, risk lists, and test expectations only. They should not edit environment, runtime, toolset, or message bus implementation files until the prerequisite PRs land.

## Alternatives Rejected

- Implement cloud workspace execution immediately after ADR-0014. Rejected because the current execution path still passes local `workspace_root` into tool construction, file path resolution, shell cwd validation, and sandbox configuration.
- Implement full subagent orchestration before runtime context and message bus. Rejected because subagents need stable agent identity, parent-child run linkage, tool policy, approval routing, and inbound steering semantics.
- Combine Environment, AgentRunContext, Toolset, MessageBus, Tool Proxy, cloud execution, and subagent orchestration in one PR. Rejected because the write scope would span session management, app construction, tool registration, pipeline execution, tape semantics, HTTP events, and tests.
- Make `AgentRunContext` own provider, model, base URL, and max steps. Rejected because PR 86 already made those fields durable session runtime metadata, and duplicating them would create two sources of truth.
- Move `SessionManager`, `ExecutionBinding`, `BindingResolver`, HTTP ownership,
  leases, REPL model switching, provider runtime persistence, concrete
  file/shell/sandbox tools, MCP implementation, skills, memory, KB, or web
  search plugins into `agentkit`. Rejected because these are application and
  product integration concerns, not reusable framework contracts.
- Proxy all tools as soon as tool proxy exists. Rejected because direct core tools are still the clearest interface for common coding workflows, while MCP and dynamic tools are the main source of prompt schema churn.

## Acceptance Criteria

- [ ] `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md` exists and records the ordered PR sequence.
- [ ] The ADR states the ownership boundary between reusable `agentkit` protocols/runtime primitives and concrete `coding_agent` product integrations.
- [ ] The ADR states that PR 1 implements `Environment` and `LocalEnvironment` before cloud workspace execution.
- [ ] The ADR states that `Environment` protocol and common types belong in `agentkit`, while `LocalEnvironment` remains in `coding_agent`.
- [ ] The ADR states that PR 2 adds `AgentRunContext` or `RunContext` under `agentkit.runtime` with session id, run id, agent id, parent run id, environment, context budget, and trace metadata.
- [ ] The ADR states that Toolset governance moves only generic provider, validation, execution envelope, tape hook, timeout/retry, and policy/approval interfaces into `agentkit`.
- [ ] The ADR states that Toolset retries are scoped to the failing provider hook and that unhandled providers must not perform side effects.
- [ ] The ADR states that approval hook and directive executor failures are fail-closed denials.
- [ ] The ADR states that RuntimeMessageBus moves only inbound message protocols and idempotent consumption semantics into `agentkit`.
- [ ] The ADR states that runtime messages are consumed at pipeline safe points and `interrupt` stops the turn at the next safe point.
- [ ] The ADR states that only generic subagent identity, trace metadata, and child-context derivation primitives may move into `agentkit`.
- [ ] The ADR states that cloud workspace execution is deferred to follow-up work with explicit failure behavior for unresolved cloud bindings.
- [ ] The ADR states that full subagent orchestration is deferred until runtime context, tool governance, and runtime messaging exist.
- [ ] The ADR references PR 86 session runtime metadata and prevents duplicating provider, model, base URL, and max steps in the environment layer.
- [ ] `python3 -m py_compile` is not required because this PR changes documentation only.

## References

- `docs/adr/0014-separate-session-execution-binding-from-runtime-ownership.md`
- `docs/adr/0015-enforce-owner-routed-http-event-streams.md`
- `src/coding_agent/ui/execution_binding.py`
- `src/coding_agent/ui/binding_resolver.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/plugins/core_tools.py`
- `src/agentkit/runtime/pipeline.py`
- `src/agentkit/context/builder.py`
- `src/agentkit/tape/tape.py`
- `src/coding_agent/plugins/mcp.py`
- `https://github.com/delete-cloud/Coding-Agent-Loop/pull/86`
