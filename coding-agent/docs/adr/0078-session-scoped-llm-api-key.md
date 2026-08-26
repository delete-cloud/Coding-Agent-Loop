# ADR-0078: Session-scoped LLM API key on HTTP create/runtime-config

**Status**: Proposed
**Date**: 2026-08-26

## Context

Night Console cannot start a real conversation unless the process already has the matching env var. `CreateSessionRequest` and `RuntimeConfigUpdateRequest` accept `provider` / `model` / `base_url` but not an LLM `api_key`. Session runtime construction currently passes `api_key=None` (`runtime_preparation.py`), so HTTP sessions only resolve keys from process env (`AGENT_API_KEY` and a few provider-specific vars). `ANTHROPIC_API_KEY` is not mapped, even though the default Night Console placeholder is Anthropic.

Codex already authenticates via ADR-0050 OAuth and must stay on that path.

## Decision

Accept an optional `api_key` on `POST /sessions` and `PATCH /sessions/{id}/runtime-config`.

- The key is session-scoped process memory only.
- It is never written to sqlite session metadata, tapes, logs, SSE, or HTTP responses.
- Empty / omitted key keeps today's env fallback.
- Also map `ANTHROPIC_API_KEY` in `load_config` the same way as `MOONSHOT_API_KEY` / `DEEPSEEK_API_KEY`.
- Codex (`codex` / `codex:<label>`) ignores a pasted `api_key` and keeps using `OAuthStore`.
- Provider names stay the closed `ProviderName` set plus `codex:<label>`. No free-form new provider types.

## Alternatives Rejected

- Env-only keys — rejected; the user cannot converse from the browser without restarting serve.
- Persist keys like OpenCode `auth.json` — rejected for this slice; higher leak surface.
- Vercel AI SDK / OpenRouter / models.dev as the runtime — rejected; CAL already has Python providers and Codex OAuth.
- Echoing the key back to the client — rejected; responses stay `provider_name` / `model_name` / `base_url` only.

## Acceptance Criteria

- [ ] `test_create_session_accepts_api_key_and_does_not_echo_it`
- [ ] `test_create_session_does_not_persist_api_key_in_session_record`
- [ ] `test_runtime_config_update_applies_api_key_to_next_turn_only`
- [ ] `test_omitted_api_key_falls_back_to_env`
- [ ] `test_load_config_reads_anthropic_api_key_env`
- [ ] `test_codex_provider_ignores_request_api_key`
- [ ] `uv run pytest tests/ui/test_http_server.py tests/coding_agent/core/test_config.py tests/coding_agent/server -k "api_key or anthropic_api_key or create_session" -v`

## References

- `src/coding_agent/server/schemas.py`
- `src/coding_agent/server/http/routes/sessions.py`
- `src/coding_agent/runs/runtime_preparation.py`
- `src/coding_agent/core/config.py`
- `src/coding_agent/server/session/records.py`
- `docs/adr/0050-codex-oauth-provider.md`
