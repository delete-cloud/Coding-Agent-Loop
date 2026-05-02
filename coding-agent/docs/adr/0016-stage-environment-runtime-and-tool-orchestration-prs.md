# ADR-0016: Stage environment runtime and tool orchestration PRs

**Status**: Proposed
**Date**: 2026-05-02

## Context

ADR-0014 introduced `ExecutionBinding` so a session can record where execution is bound without mixing that concern with runtime ownership. PR 86 then made HTTP session runtime metadata durable by preserving provider, model, base URL, and step limit across restart and REPL runtime replacement. The code now has durable session metadata for model execution and workspace binding, but tool execution still enters the system through local `workspace_root` assumptions in `CoreToolsPlugin`, file tools, shell tools, and session runtime creation.

The next work affects several areas that future agents may implement in parallel: local environment execution, run context, tool governance, runtime messaging, tool proxying, cloud workspace execution, and subagent orchestration. These areas depend on each other. Implementing cloud workspace execution or full subagent orchestration before the execution and runtime boundaries are stable would force remote transport, agent identity, tape labeling, approval routing, and tool policy into interfaces that are still local-path oriented.

This ADR records the PR order and scope gates before implementation begins. It is intended to let multiple agents contribute without overlapping write scopes or introducing incompatible abstractions.

## Decision

Implement the work in the following PR sequence.

1. PR 1: Add `Environment` and `LocalEnvironment`
   - Add an environment protocol for file, shell, patch, glob, grep, and workspace identity operations.
   - Add a local implementation that preserves current path confinement, shell, sandbox, and structured result behavior.
   - Extend binding resolution so `LocalExecutionBinding` can produce an environment, while `CloudWorkspaceBinding` keeps an explicit not-implemented failure path.
   - Update `CoreToolsPlugin` to depend on an environment boundary while keeping backward compatibility for existing `workspace_root` construction.
   - Keep runtime provider metadata from PR 86 in session metadata. Do not duplicate it inside the environment.

2. PR 2: Add lightweight runtime context
   - Add a small `AgentRunContext` or equivalent runtime object for per-run identity, environment reference, session id, agent id, context budget, and trace metadata.
   - Keep provider, model, base URL, and max steps sourced from session runtime metadata introduced by PR 86.
   - Keep `PipelineContext`, `ContextBuilder`, and `TapeView` focused on prompt assembly and pipeline state.

3. PR 3: Add `Toolset` governance
   - Keep `ToolRegistry` as the registration mechanism.
   - Add a tool execution boundary for schema validation, tool policy, approval, timeout, retry, environment injection, structured result handling, and tape recording.
   - Treat `CoreToolsPlugin` and `MCPPlugin` as tool providers.

4. PR 4: Add runtime message bus and runtime context injection
   - Add inbound runtime messages for interrupt, user steering, approval decision, subagent message, and system notice.
   - Keep outbound event streams separate from inbound runtime control.
   - Add prompt-time runtime context injection for run id, agent id, environment summary, cwd, elapsed time, context budget, and active approvals.

5. PR 5: Add tool proxy for dynamic toolsets
   - Add stable `search_tools` and `call_tool` affordances for MCP and dynamically loaded tools.
   - Keep core file, shell, planner, patch, web search, and subagent tools directly visible until there is evidence that proxying them improves behavior.
   - Route proxied execution through the same `Toolset` governance path.

Cloud workspace execution and full subagent orchestration are tracked as follow-up implementation areas, not as PR 1 work.

- Cloud workspace execution must wait until PR 1 defines the environment protocol and local parity tests. Until then, cloud bindings must fail explicitly rather than partially executing through local path assumptions.
- Full subagent orchestration must wait until PR 2, PR 3, and PR 4 define run identity, environment sharing, tool governance, inbound messages, and prompt-time runtime injection. Until then, the existing subagent tool may remain as-is.

Parallel agents may work on design review tasks for cloud workspace and subagent orchestration while PR 1 is being implemented. Those agents should produce interface requirements, risk lists, and test expectations only. They should not edit environment, runtime, toolset, or message bus implementation files until the prerequisite PRs land.

## Alternatives Rejected

- Implement cloud workspace execution immediately after ADR-0014. Rejected because the current execution path still passes local `workspace_root` into tool construction, file path resolution, shell cwd validation, and sandbox configuration.
- Implement full subagent orchestration before runtime context and message bus. Rejected because subagents need stable agent identity, parent-child run linkage, tool policy, approval routing, and inbound steering semantics.
- Combine Environment, AgentRunContext, Toolset, MessageBus, Tool Proxy, cloud execution, and subagent orchestration in one PR. Rejected because the write scope would span session management, app construction, tool registration, pipeline execution, tape semantics, HTTP events, and tests.
- Make `AgentRunContext` own provider, model, base URL, and max steps. Rejected because PR 86 already made those fields durable session runtime metadata, and duplicating them would create two sources of truth.
- Proxy all tools as soon as tool proxy exists. Rejected because direct core tools are still the clearest interface for common coding workflows, while MCP and dynamic tools are the main source of prompt schema churn.

## Acceptance Criteria

- [ ] `docs/adr/0016-stage-environment-runtime-and-tool-orchestration-prs.md` exists and records the ordered PR sequence.
- [ ] The ADR states that PR 1 implements `Environment` and `LocalEnvironment` before cloud workspace execution.
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
