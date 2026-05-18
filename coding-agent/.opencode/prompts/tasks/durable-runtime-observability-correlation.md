Goal:
Propagate durable runtime correlation identifiers into observability spans.

Scope:
- Add safe, low-cardinality runtime correlation attributes to AgentKit pipeline
  stage and LLM generation spans.
- Include `tape_id` from the active tape.
- Forward only whitelisted `AgentRunContext.trace_metadata` correlation keys.
- Bind HTTP root runs to `turn_id` and `tape_id` trace metadata from
  `SessionManager`.
- Keep OTLP/Langfuse exporter privacy filtering intact.

Out of scope:
- Do not add raw prompt, message, content, tool result, secret, or text values
  to span attributes.
- Do not change trace id derivation.
- Do not change observability configuration defaults.
- Do not change runtime persistence or replay APIs.

Context:
- ADRs:
  - `docs/adr/0029-durable-runtime-identity.md`
  - `docs/adr/0028-observability-and-langfuse-integration.md`
- Postmortem patterns consulted:
  - `postmortem/patterns/PM-0001-address-code-review-issues.md`
  - `postmortem/patterns/PM-0006-add-usage-event-fields-and-fix-tool-name-kwarg-in-pipeline.md`
  - `postmortem/patterns/PM-0009-preserve-neutral-bare-anchor-semantics.md`
  - `postmortem/patterns/PM-0010-route-incremental-context-append-through-tapeview.md`
- Relevant files:
  - `src/agentkit/runtime/pipeline.py`
  - `src/coding_agent/ui/session_manager.py`
  - `tests/agentkit/runtime/test_pipeline.py`
  - `tests/ui/test_session_manager_runtime.py`
  - `tests/coding_agent/test_observability.py`

Target tests:
- `uv run pytest tests/agentkit/runtime/test_pipeline.py -k "span" -v`
- `uv run pytest tests/ui/test_session_manager_runtime.py -k "run_id or approval or message_snapshot" -v`
- `uv run pytest tests/coding_agent/test_observability.py -v`
- `uv run ruff check src/agentkit/runtime/pipeline.py src/coding_agent/ui/session_manager.py tests/agentkit/runtime/test_pipeline.py tests/ui/test_session_manager_runtime.py`

Stop conditions:
- Stop with a blocker if the work requires exporting raw prompt/message/result
  data or weakening the existing OTLP denylist.
- Stop with a blocker if trace metadata needs a broader public API decision.
