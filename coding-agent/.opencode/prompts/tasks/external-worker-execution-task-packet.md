Goal:
Implement external-worker execution so o6n can own session/run control state while a local CLI worker performs agent runtime and tool execution.

Scope:
- Add external worker execution binding and request schemas.
- Add server-side durable run request, claim, heartbeat, event upload, and finalize APIs.
- Add local CLI one-shot worker path that creates/claims/runs/finalizes an external-worker session.
- Preserve existing server-local and cloud workspace behavior.

Out of scope:
- Multi-worker scheduling UI.
- Full incremental tape append and cross-process resume beyond final tape/result metadata.
- Direct database access from local CLI.

Context:
- ADRs:
  - docs/adr/0051-external-worker-execution-control-plane.md
- Relevant files:
  - src/coding_agent/server/execution_binding.py
  - src/coding_agent/server/session_manager.py
  - src/coding_agent/server/http_server.py
  - src/coding_agent/remote/client.py
  - src/coding_agent/cli/remote_commands.py

Target tests:
- uv run pytest tests/ui/test_http_server.py -k "external_worker" -v
- uv run pytest tests/cli/test_remote_client.py -k "local_run or external_worker" -v
- uv run pytest tests/coding_agent -k "pipeline_adapter" -v

Loop policy:
- Engineer implements the smallest correct change and runs the target tests.
- Reviewer reviews only the resulting diff and affected tests.
- Reviewer reports only P1/P2 findings.
- Engineer fixes only accepted P1/P2 findings and reruns the same target tests.
- Verifier reruns the exact target tests and reports pass/fail only.

Stop conditions:
- At most one review/fix/retest cycle.
- Escalate architectural redirection or scope expansion to the human.
- Ignore non-blocking optimization suggestions.
