Goal:
Replace the current `codex` provider's Chat Completions wire path with the official Codex Responses HTTP SSE path for ChatGPT-authenticated Codex traffic.

Scope:
- Add a Codex-specific Responses provider that posts to `/responses`.
- Convert existing agent messages/tools into Responses API request payloads.
- Parse Responses SSE events into `TextEvent`, `ThinkingEvent`, `ToolCallEvent`, `UsageEvent`, and `DoneEvent`.
- Wire `provider="codex"` to the new provider and update focused tests.

Out of scope:
- Responses WebSocket transport and warmup/reuse.
- Browser-cookie reuse beyond the existing ChatGPT bearer token flow.
- Changes to non-Codex OpenAI-compatible providers.

Context:
- ADRs:
  - `docs/adr/0050-codex-oauth-provider.md`
  - `docs/adr/0057-codex-provider-responses-wire-protocol.md`
- Relevant files:
  - `src/coding_agent/providers/codex_responses.py`
  - `src/coding_agent/plugins/llm_provider.py`
  - `tests/providers/test_codex_responses.py`
  - `tests/coding_agent/plugins/test_llm_provider.py`

Target tests:
- `uv run pytest tests/providers/test_codex_responses.py tests/coding_agent/plugins/test_llm_provider.py -k "codex" -v`
- `uv run ruff check src/coding_agent/providers/codex_responses.py src/coding_agent/plugins/llm_provider.py tests/providers/test_codex_responses.py tests/coding_agent/plugins/test_llm_provider.py`

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
