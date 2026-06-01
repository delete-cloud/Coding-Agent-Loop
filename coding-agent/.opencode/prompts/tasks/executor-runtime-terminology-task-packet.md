Goal:
Align the first implementation slice with the resume-first executor/runtime
architecture by removing the unmanaged local batch runtime path and adding
executor-named CLI aliases while preserving worker-named compatibility.

Scope:
- Add ADR-0054 for executor/runtime terminology and resume-first direction.
- Change local `run --goal` to execute through a managed `SessionManager`
  session instead of directly constructing `create_agent()` and
  `PipelineAdapter`.
- Add executor-named remote CLI aliases for the current local-attached executor
  loop and executor status inspection.
- Keep existing worker-named CLI/API surfaces as compatibility aliases.

Out of scope:
- `POST /sessions/{session_id}/resume`.
- Run linkage fields such as `previous_run_id` and `resume_from_event_id`.
- Process-level reconnect, local daemon, registry, lease/fencing, event spool,
  cross-machine workspace sync, and pool UI.
- Full internal symbol migration from worker to executor.

Context:
- ADRs:
  - `docs/adr/0054-executor-runtime-terminology-and-resume-first-direction.md`
  - `docs/adr/0051-external-worker-execution-control-plane.md`
  - `docs/adr/0052-external-worker-usable-control-plane.md`
  - `docs/adr/0053-advanced-external-worker-control-plane-foundations.md`
- Relevant files:
  - `src/coding_agent/cli/main.py`
  - `src/coding_agent/cli/remote_commands.py`
  - `src/coding_agent/remote/worker.py`
  - `tests/cli/test_entrypoint_contract.py`
  - `tests/cli/test_remote_client.py`

Target tests:
- `uv run pytest tests/cli/test_entrypoint_contract.py tests/cli/test_remote_client.py -k "run_command_uses_managed_session or remote_executor_alias or remote_executors_alias or default_non_interactive" -v`
- `uv run pytest tests/cli/test_entrypoint_contract.py tests/cli/test_remote_client.py -v`

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
