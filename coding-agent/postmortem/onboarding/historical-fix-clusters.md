# Historical Fix Clusters

## PM-0001 Address code review issues

- Subsystem: ui
- Commits: d3356a9acb2b06bb93afaf7920595663a997fe83, 9297e22fbd22919bf41e75d7b22565c1836c62f9, 317ae3194508b6151d9850f1199120cd1e3df897, 64f6f7f08fa6e6bf471147a156deecd96d648d18, 167f4d5ab63b0470222cedf73e2240f5984b54dd
- Files: src/agentkit/context/builder.py, src/agentkit/runtime/pipeline.py, src/coding_agent/__main__.py, src/coding_agent/cli/commands.py, src/coding_agent/cli/input_handler.py, src/coding_agent/plugins/core_tools.py, src/coding_agent/providers/__init__.py, src/coding_agent/tools/file_ops.py, src/coding_agent/tools/planner.py, src/coding_agent/tools/shell.py, src/coding_agent/ui/approval_prompt.py, src/coding_agent/ui/http_server.py, src/coding_agent/ui/rich_consumer.py, src/coding_agent/ui/rich_tui.py, src/coding_agent/ui/session_manager.py, src/coding_agent/ui/session_store.py, src/coding_agent/ui/theme.py, tests/agentkit/context/test_builder.py, tests/agentkit/runtime/test_pipeline.py, tests/cli/test_commands.py, tests/cli/test_paste_folding.py, tests/cli/test_repl.py, tests/coding_agent/plugins/test_core_tools.py, tests/coding_agent/test_bootstrap.py, tests/coding_agent/tools/test_file_ops.py, tests/coding_agent/tools/test_shell.py, tests/ui/test_approval_prompt.py, tests/ui/test_http_server.py, tests/ui/test_session_manager_public_api.py, tests/ui/test_session_persistence.py, tests/ui/test_streaming_consumer.py, uv.lock

## PM-0002 Address code review P0/P1 issues

- Subsystem: runtime
- Commits: c0440ec8e54dbdd7ba0eebf048b904b77eb326b4, 2aceff41572620e3367112653fcd2aae73a71661
- Files: src/coding_agent/agents/subagent.py, src/coding_agent/core/context.py, src/coding_agent/core/loop.py, src/coding_agent/core/tape.py, src/coding_agent/providers/anthropic.py, src/coding_agent/providers/base.py, src/coding_agent/providers/openai_compat.py, src/coding_agent/tools/planner.py, src/coding_agent/tools/registry.py, src/coding_agent/tools/search.py, src/coding_agent/tools/subagent.py, src/coding_agent/ui/headless.py, tests/agents/test_subagent.py, tests/providers/test_openai_compat.py, uv.lock

## PM-0003 Guard missing directive executors

- Subsystem: adapter
- Commits: c606f664a080c5aaeddba30357611cc2e566cc48
- Files: src/coding_agent/adapter.py, tests/coding_agent/test_pipeline_adapter.py

## PM-0004 Handle mapping tool results safely

- Subsystem: adapter
- Commits: d6e2dfbc6d6584e977185d4f44b77f51f31509c0
- Files: src/coding_agent/adapter.py, tests/coding_agent/test_adapter_tool_result.py

## PM-0005 Redact user-facing tool result displays

- Subsystem: adapter
- Commits: 00d1bb6e377f4b427614e4473af40bd29c34e49f
- Files: src/coding_agent/adapter.py, tests/coding_agent/test_adapter_tool_result.py

## PM-0006 Add usage event fields and fix tool name kwarg in pipeline

- Subsystem: agentkit
- Commits: 83db1557f9ea5cb4e52851242dc0f92233e3cbaa
- Files: src/agentkit/providers/models.py, src/agentkit/runtime/pipeline.py

## PM-0007 Move Anchor into tape models to remove import cycle

- Subsystem: agentkit
- Commits: 2ff2d24f695d01dd6a2d93304186630e3b34ecc9
- Files: src/agentkit/tape/anchor.py, src/agentkit/tape/models.py

## PM-0008 Move handoff summaries ahead of recent context

- Subsystem: agentkit
- Commits: 21636c6e1c3f6929095b61b306f09a46d1952b45
- Files: src/agentkit/tape/view.py, tests/agentkit/tape/test_view.py

## PM-0009 Preserve neutral bare anchor semantics

- Subsystem: agentkit
- Commits: f755dbadd60098ca68d789fbacc6b59ce4e2b226
- Files: .gitignore, .sisyphus/boulder.json, README.md, pyproject.toml, src/agentkit/__init__.py, src/agentkit/context/builder.py, src/agentkit/directive/executor.py, src/agentkit/directive/types.py, src/agentkit/providers/models.py, src/agentkit/runtime/__init__.py, src/agentkit/runtime/hookspecs.py, src/agentkit/runtime/pipeline.py, src/agentkit/storage/__init__.py, src/agentkit/tape/models.py, src/agentkit/tools/decorator.py, src/agentkit/tools/registry.py, src/agentkit/tools/schema.py, src/coding_agent/__main__.py, src/coding_agent/adapter.py, src/coding_agent/agent.toml, src/coding_agent/cli/bash_executor.py, src/coding_agent/cli/commands.py, src/coding_agent/cli/input_handler.py, src/coding_agent/cli/repl.py, src/coding_agent/kb.py, src/coding_agent/plugins/approval.py, src/coding_agent/plugins/core_tools.py, src/coding_agent/plugins/doom_detector.py, src/coding_agent/plugins/memory.py, src/coding_agent/plugins/metrics.py, src/coding_agent/plugins/parallel_executor.py, src/coding_agent/plugins/skills.py, src/coding_agent/plugins/storage.py, src/coding_agent/plugins/topic.py, src/coding_agent/tools/file_ops.py, src/coding_agent/tools/shell.py, src/coding_agent/tools/subagent_stub.py, src/coding_agent/ui/components.py, src/coding_agent/ui/rate_limit.py, src/coding_agent/ui/rich_consumer.py, src/coding_agent/ui/stream_renderer.py, src/coding_agent/wire/protocol.py, tests/agentkit/context/test_builder.py, tests/agentkit/directive/test_executor.py, tests/agentkit/directive/test_types.py, tests/agentkit/runtime/test_hookspecs.py, tests/agentkit/runtime/test_pipeline.py, tests/agentkit/runtime/test_pipeline_truncation.py, tests/agentkit/tape/test_anchor.py, tests/agentkit/tape/test_models.py, tests/agentkit/tape/test_tape.py, tests/agentkit/test_incremental_context.py, tests/agentkit/test_tool_result_event.py, tests/agentkit/tools/test_decorator.py, tests/agentkit/tools/test_registry.py, tests/agentkit/tools/test_schema.py, tests/cli/test_commands.py, tests/cli/test_repl.py, tests/coding_agent/plugins/test_approval.py, tests/coding_agent/plugins/test_core_tools.py, tests/coding_agent/plugins/test_core_tools_parity.py, tests/coding_agent/plugins/test_doom_detector.py, tests/coding_agent/plugins/test_memory.py, tests/coding_agent/plugins/test_metrics.py, tests/coding_agent/plugins/test_parallel_executor.py, tests/coding_agent/plugins/test_skills.py, tests/coding_agent/plugins/test_storage.py, tests/coding_agent/plugins/test_topic.py, tests/coding_agent/test_adapter_tool_result.py, tests/coding_agent/test_bootstrap.py, tests/coding_agent/test_cli_pipeline.py, tests/coding_agent/test_phase1_chunk_a_cleanup.py, tests/coding_agent/test_pipeline_adapter.py, tests/coding_agent/tools/test_shell.py, tests/integration/test_e2e.py, tests/integration/test_pipeline_e2e.py, tests/tools/test_shell.py, tests/ui/test_stream_renderer.py, tests/ui/test_streaming_consumer.py, tests/wire/test_protocol.py, uv.lock

## PM-0010 Route incremental context append through TapeView

- Subsystem: agentkit
- Commits: b3bdec71a57b102d5291f2106bf09c87f4ef99ad
- Files: src/agentkit/runtime/pipeline.py, tests/agentkit/test_incremental_context.py

## PM-0011 Centralize session approval coordination

- Subsystem: approval
- Commits: 07d3b9285aca39090b934059b24bd57011d8d2ef
- Files: src/coding_agent/approval/__init__.py, src/coding_agent/approval/coordinator.py, src/coding_agent/approval/store.py, tests/approval/test_coordinator.py

## PM-0012 Clean up pending requests after waits

- Subsystem: approval
- Commits: 2cde0b4a1536c154649cf14ea19b7d5d72f35410
- Files: src/coding_agent/approval/store.py, tests/approval/test_store.py

## PM-0013 Clear answered request projections

- Subsystem: approval
- Commits: feddcecfa02f95fda5ed44ffb6fc2caecf5df70d
- Files: src/coding_agent/approval/coordinator.py, tests/ui/test_http_server.py

## PM-0014 Make approval responses single-shot

- Subsystem: approval
- Commits: f2406cfe20638ed522214b386f7b8bca0149b1ad
- Files: src/coding_agent/approval/coordinator.py, src/coding_agent/approval/store.py, tests/approval/test_coordinator.py, tests/approval/test_store.py

## PM-0015 Require store-backed requests across HTTP approval flow

- Subsystem: approval
- Commits: de91c5f10b06574508c75751571fe435f7cd2006
- Files: src/coding_agent/approval/store.py, src/coding_agent/ui/http_server.py, src/coding_agent/ui/schemas.py, src/coding_agent/ui/session_manager.py, tests/approval/test_store.py, tests/integration/test_wire_http_integration.py, tests/ui/test_http_server.py, tests/ui/test_security.py, tests/ui/test_session_manager_public_api.py

## PM-0016 Align sandbox env error assertion

- Subsystem: bootstrap
- Commits: 959e1a830fad351d5db7bc80c737d2d061f1a855
- Files: tests/tools/test_shell.py

## PM-0017 Preserve shared bootstrap contracts

- Subsystem: bootstrap
- Commits: e88cbd15a2bde6c4b2e59213a3ca7e4336f30e54
- Files: src/coding_agent/__main__.py, src/coding_agent/plugins/kb.py, src/coding_agent/tools/sandbox.py, tests/tools/test_shell.py

## PM-0018 Restore child pipeline bootstrap wiring

- Subsystem: bootstrap
- Commits: eac8ba3965d46863d60945e56182dda932e5b171
- Files: src/coding_agent/__main__.py, src/coding_agent/adapter.py, src/coding_agent/plugins/core_tools.py, tests/coding_agent/test_bootstrap.py

## PM-0019 Restore child pipeline composition root

- Subsystem: bootstrap
- Commits: 85f7af02a2d7a666177c4cacae78a7be389f1e2a
- Files: src/coding_agent/app.py

## PM-0020 Checkpoint ids in fs store

- Subsystem: checkpoint
- Commits: 04252bf694892e8bce237f8e03cfff5077d41689
- Files: src/agentkit/storage/checkpoint_fs.py, tests/agentkit/checkpoint/test_service.py
