---
id: PM-0009
title: Preserve neutral bare anchor semantics
status: active
severity: medium
confidence: medium
subsystems:
- agentkit
related_commits:
- f755dbadd60098ca68d789fbacc6b59ce4e2b226
related_files:
- .gitignore
- .sisyphus/boulder.json
- README.md
- pyproject.toml
- src/agentkit/__init__.py
- src/agentkit/context/builder.py
- src/agentkit/directive/executor.py
- src/agentkit/directive/types.py
- src/agentkit/providers/models.py
- src/agentkit/runtime/__init__.py
- src/agentkit/runtime/hookspecs.py
- src/agentkit/runtime/pipeline.py
- src/agentkit/storage/__init__.py
- src/agentkit/tape/models.py
- src/agentkit/tools/decorator.py
- src/agentkit/tools/registry.py
- src/agentkit/tools/schema.py
- src/coding_agent/__main__.py
- src/coding_agent/adapter.py
- src/coding_agent/agent.toml
- src/coding_agent/cli/bash_executor.py
- src/coding_agent/cli/commands.py
- src/coding_agent/cli/input_handler.py
- src/coding_agent/cli/repl.py
- src/coding_agent/kb.py
- src/coding_agent/plugins/approval.py
- src/coding_agent/plugins/core_tools.py
- src/coding_agent/plugins/doom_detector.py
- src/coding_agent/plugins/memory.py
- src/coding_agent/plugins/metrics.py
- src/coding_agent/plugins/parallel_executor.py
- src/coding_agent/plugins/skills.py
- src/coding_agent/plugins/storage.py
- src/coding_agent/plugins/topic.py
- src/coding_agent/tools/file_ops.py
- src/coding_agent/tools/shell.py
- src/coding_agent/tools/subagent_stub.py
- src/coding_agent/ui/components.py
- src/coding_agent/ui/rate_limit.py
- src/coding_agent/ui/rich_consumer.py
- src/coding_agent/ui/stream_renderer.py
- src/coding_agent/wire/protocol.py
- tests/agentkit/context/test_builder.py
- tests/agentkit/directive/test_executor.py
- tests/agentkit/directive/test_types.py
- tests/agentkit/runtime/test_hookspecs.py
- tests/agentkit/runtime/test_pipeline.py
- tests/agentkit/runtime/test_pipeline_truncation.py
- tests/agentkit/tape/test_anchor.py
- tests/agentkit/tape/test_models.py
- tests/agentkit/tape/test_tape.py
- tests/agentkit/test_incremental_context.py
- tests/agentkit/test_tool_result_event.py
- tests/agentkit/tools/test_decorator.py
- tests/agentkit/tools/test_registry.py
- tests/agentkit/tools/test_schema.py
- tests/cli/test_commands.py
- tests/cli/test_repl.py
- tests/coding_agent/plugins/test_approval.py
- tests/coding_agent/plugins/test_core_tools.py
- tests/coding_agent/plugins/test_core_tools_parity.py
- tests/coding_agent/plugins/test_doom_detector.py
- tests/coding_agent/plugins/test_memory.py
- tests/coding_agent/plugins/test_metrics.py
- tests/coding_agent/plugins/test_parallel_executor.py
- tests/coding_agent/plugins/test_skills.py
- tests/coding_agent/plugins/test_storage.py
- tests/coding_agent/plugins/test_topic.py
- tests/coding_agent/test_adapter_tool_result.py
- tests/coding_agent/test_bootstrap.py
- tests/coding_agent/test_cli_pipeline.py
- tests/coding_agent/test_phase1_chunk_a_cleanup.py
- tests/coding_agent/test_pipeline_adapter.py
- tests/coding_agent/tools/test_shell.py
- tests/integration/test_e2e.py
- tests/integration/test_pipeline_e2e.py
- tests/tools/test_shell.py
- tests/ui/test_stream_renderer.py
- tests/ui/test_streaming_consumer.py
- tests/wire/test_protocol.py
- uv.lock
release_checks:
- Run focused tests for agentkit changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

preserve neutral bare anchor semantics

# Trigger Conditions

- Changes in agentkit paths
- Historical commit: `fix(agentkit): preserve neutral bare anchor semantics`

# Known Fix Signals

- `.gitignore`
- `.sisyphus/boulder.json`
- `README.md`
- `pyproject.toml`
- `src/agentkit/__init__.py`
- `src/agentkit/context/builder.py`
- `src/agentkit/directive/executor.py`
- `src/agentkit/directive/types.py`
- `src/agentkit/providers/models.py`
- `src/agentkit/runtime/__init__.py`
- `src/agentkit/runtime/hookspecs.py`
- `src/agentkit/runtime/pipeline.py`
- `src/agentkit/storage/__init__.py`
- `src/agentkit/tape/models.py`
- `src/agentkit/tools/decorator.py`
- `src/agentkit/tools/registry.py`
- `src/agentkit/tools/schema.py`
- `src/coding_agent/__main__.py`
- `src/coding_agent/adapter.py`
- `src/coding_agent/agent.toml`
- `src/coding_agent/cli/bash_executor.py`
- `src/coding_agent/cli/commands.py`
- `src/coding_agent/cli/input_handler.py`
- `src/coding_agent/cli/repl.py`
- `src/coding_agent/kb.py`
- `src/coding_agent/plugins/approval.py`
- `src/coding_agent/plugins/core_tools.py`
- `src/coding_agent/plugins/doom_detector.py`
- `src/coding_agent/plugins/memory.py`
- `src/coding_agent/plugins/metrics.py`
- `src/coding_agent/plugins/parallel_executor.py`
- `src/coding_agent/plugins/skills.py`
- `src/coding_agent/plugins/storage.py`
- `src/coding_agent/plugins/topic.py`
- `src/coding_agent/tools/file_ops.py`
- `src/coding_agent/tools/shell.py`
- `src/coding_agent/tools/subagent_stub.py`
- `src/coding_agent/ui/components.py`
- `src/coding_agent/ui/rate_limit.py`
- `src/coding_agent/ui/rich_consumer.py`
- `src/coding_agent/ui/stream_renderer.py`
- `src/coding_agent/wire/protocol.py`
- `tests/agentkit/context/test_builder.py`
- `tests/agentkit/directive/test_executor.py`
- `tests/agentkit/directive/test_types.py`
- `tests/agentkit/runtime/test_hookspecs.py`
- `tests/agentkit/runtime/test_pipeline.py`
- `tests/agentkit/runtime/test_pipeline_truncation.py`
- `tests/agentkit/tape/test_anchor.py`
- `tests/agentkit/tape/test_models.py`
- `tests/agentkit/tape/test_tape.py`
- `tests/agentkit/test_incremental_context.py`
- `tests/agentkit/test_tool_result_event.py`
- `tests/agentkit/tools/test_decorator.py`
- `tests/agentkit/tools/test_registry.py`
- `tests/agentkit/tools/test_schema.py`
- `tests/cli/test_commands.py`
- `tests/cli/test_repl.py`
- `tests/coding_agent/plugins/test_approval.py`
- `tests/coding_agent/plugins/test_core_tools.py`
- `tests/coding_agent/plugins/test_core_tools_parity.py`
- `tests/coding_agent/plugins/test_doom_detector.py`
- `tests/coding_agent/plugins/test_memory.py`
- `tests/coding_agent/plugins/test_metrics.py`
- `tests/coding_agent/plugins/test_parallel_executor.py`
- `tests/coding_agent/plugins/test_skills.py`
- `tests/coding_agent/plugins/test_storage.py`
- `tests/coding_agent/plugins/test_topic.py`
- `tests/coding_agent/test_adapter_tool_result.py`
- `tests/coding_agent/test_bootstrap.py`
- `tests/coding_agent/test_cli_pipeline.py`
- `tests/coding_agent/test_phase1_chunk_a_cleanup.py`
- `tests/coding_agent/test_pipeline_adapter.py`
- `tests/coding_agent/tools/test_shell.py`
- `tests/integration/test_e2e.py`
- `tests/integration/test_pipeline_e2e.py`
- `tests/tools/test_shell.py`
- `tests/ui/test_stream_renderer.py`
- `tests/ui/test_streaming_consumer.py`
- `tests/wire/test_protocol.py`
- `uv.lock`

# Release Review Checklist

- Run focused tests for agentkit changes before release.
- Review affected files for the same control-flow shape before shipping.
