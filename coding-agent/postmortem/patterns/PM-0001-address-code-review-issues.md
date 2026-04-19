---
id: PM-0001
title: Address code review issues
status: active
severity: medium
confidence: medium
subsystems:
- ui
related_commits:
- d3356a9acb2b06bb93afaf7920595663a997fe83
- 9297e22fbd22919bf41e75d7b22565c1836c62f9
- 317ae3194508b6151d9850f1199120cd1e3df897
- 64f6f7f08fa6e6bf471147a156deecd96d648d18
- 167f4d5ab63b0470222cedf73e2240f5984b54dd
related_files:
- src/agentkit/context/builder.py
- src/agentkit/runtime/pipeline.py
- src/coding_agent/__main__.py
- src/coding_agent/cli/commands.py
- src/coding_agent/cli/input_handler.py
- src/coding_agent/plugins/core_tools.py
- src/coding_agent/providers/__init__.py
- src/coding_agent/tools/file_ops.py
- src/coding_agent/tools/planner.py
- src/coding_agent/tools/shell.py
- src/coding_agent/ui/approval_prompt.py
- src/coding_agent/ui/http_server.py
- src/coding_agent/ui/rich_consumer.py
- src/coding_agent/ui/rich_tui.py
- src/coding_agent/ui/session_manager.py
- src/coding_agent/ui/session_store.py
- src/coding_agent/ui/theme.py
- tests/agentkit/context/test_builder.py
- tests/agentkit/runtime/test_pipeline.py
- tests/cli/test_commands.py
- tests/cli/test_paste_folding.py
- tests/cli/test_repl.py
- tests/coding_agent/plugins/test_core_tools.py
- tests/coding_agent/test_bootstrap.py
- tests/coding_agent/tools/test_file_ops.py
- tests/coding_agent/tools/test_shell.py
- tests/ui/test_approval_prompt.py
- tests/ui/test_http_server.py
- tests/ui/test_session_manager_public_api.py
- tests/ui/test_session_persistence.py
- tests/ui/test_streaming_consumer.py
- uv.lock
release_checks:
- Run focused tests for ui changes before release.
- Review affected files for the same control-flow shape before shipping.
---

# Summary

address code review issues

# Trigger Conditions

- Changes in ui paths
- Historical commit: `fix(tui): address code review issues`

# Known Fix Signals

- `src/agentkit/context/builder.py`
- `src/agentkit/runtime/pipeline.py`
- `src/coding_agent/__main__.py`
- `src/coding_agent/cli/commands.py`
- `src/coding_agent/cli/input_handler.py`
- `src/coding_agent/plugins/core_tools.py`
- `src/coding_agent/providers/__init__.py`
- `src/coding_agent/tools/file_ops.py`
- `src/coding_agent/tools/planner.py`
- `src/coding_agent/tools/shell.py`
- `src/coding_agent/ui/approval_prompt.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/ui/rich_consumer.py`
- `src/coding_agent/ui/rich_tui.py`
- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/session_store.py`
- `src/coding_agent/ui/theme.py`
- `tests/agentkit/context/test_builder.py`
- `tests/agentkit/runtime/test_pipeline.py`
- `tests/cli/test_commands.py`
- `tests/cli/test_paste_folding.py`
- `tests/cli/test_repl.py`
- `tests/coding_agent/plugins/test_core_tools.py`
- `tests/coding_agent/test_bootstrap.py`
- `tests/coding_agent/tools/test_file_ops.py`
- `tests/coding_agent/tools/test_shell.py`
- `tests/ui/test_approval_prompt.py`
- `tests/ui/test_http_server.py`
- `tests/ui/test_session_manager_public_api.py`
- `tests/ui/test_session_persistence.py`
- `tests/ui/test_streaming_consumer.py`
- `uv.lock`

# Release Review Checklist

- Run focused tests for ui changes before release.
- Review affected files for the same control-flow shape before shipping.
