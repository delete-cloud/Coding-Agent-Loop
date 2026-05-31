# ADR-0050: Codex OAuth Provider Support

**Status**: Accepted
**Date**: 2026-05-31

## Context

The coding agent currently supports API-key-based LLM providers (OpenAI, Anthropic, Copilot, Kimi, DeepSeek, StepFun). We need to add support for OpenAI's ChatGPT Codex Responses API, which requires OAuth 2.0 device authorization instead of an API key. Codex provides a distinct API surface and model capabilities that differ from the standard chat completions API.

## Decision

We will implement **Codex OAuth support** directly within the coding agent codebase under a new `src/coding_agent/oauth/` package, rather than depending on the `ya-oauth` mono-repo packages.

### Why not use `ya-oauth` / `ya-oauth-provider`?

The reference `ya-oauth` packages provide a clean OAuth client, but pulling in the entire mono-repo introduces a larger dependency surface than needed. The core OAuth flow (device code → token exchange → refresh) is straightforward and can be implemented in a self-contained module.

### Architecture

```
src/coding_agent/oauth/
├── __init__.py          # Public exports
├── types.py             # Pydantic models
├── store.py             # File-backed OAuth credential store
├── codex.py             # Codex OAuth client (device flow, token exchange, refresh)
├── auth.py              # httpx OAuthBearerAuth class
└── cli.py               # Click CLI commands (login, status, refresh, logout, doctor)
```

### OAuth Flow

1. **Device Code Request**: POST to `https://auth.openai.com/api/accounts/deviceauth/usercode`
2. **User Authorization**: User opens `https://auth.openai.com/codex/device` in browser and enters the code
3. **Token Polling**: POST to device token endpoint until user authorizes
4. **Token Exchange**: Exchange authorization code for access/refresh tokens
5. **Token Refresh**: Use refresh token to obtain new access token before it expires
6. **Storage**: Tokens stored in `~/.coding-agent/oauth/auth.json` with file locking

### Provider Integration

The Codex provider wraps `OpenAICompatProvider` with `OAuthBearerAuth`, an `httpx.Auth` subclass that:

1. Reads the stored access token
2. Attaches it as a `Bearer` authorization header
3. Automatically refreshes the token on 401 responses
4. Persists the refreshed token to the auth store

### CLI Integration

A new `coding-agent oauth` command group is added:

```bash
coding-agent oauth login codex     # Start device code flow
coding-agent oauth status          # Show login status
coding-agent oauth refresh codex   # Manually refresh tokens
coding-agent oauth logout codex    # Revoke and remove tokens
coding-agent oauth doctor          # Inspect auth file health
```

The `coding-agent --provider codex` flag selects the Codex provider for agent runs.

## Consequences

### Positive

- Self-contained OAuth implementation with minimal dependencies (httpx, pydantic)
- Reuses existing `OpenAICompatProvider` streaming infrastructure
- Consistent UX with `ya-oauth` reference implementation
- Secure file storage with proper permissions (0600 for files, 0700 for directories)

### Negative

- Duplicates some logic from `ya-oauth` (JWT parsing, device auth polling)
- Must maintain the OAuth flow if OpenAI changes their endpoints

### Risks

- OpenAI may change the Codex OAuth endpoints or flow (mitigated by keeping it self-contained and easy to update)
- Token refresh failures could interrupt long-running agent sessions (mitigated by retry logic in the provider)

## Alternatives Rejected

1. **Depend on `ya-oauth` packages** — Rejected to keep dependency surface small
2. **API key fallback** — Codex does not support API key authentication; OAuth is required
3. **External credential helper** — Rejected to keep the UX simple (single binary)

## Acceptance Criteria

- [ ] `test_oauth_store_writes_private_file_and_round_trips_record`
- [ ] `test_store_backed_token_source_refreshes_and_persists_token`
- [ ] `test_store_backed_token_source_missing_login_fails_fast`
- [ ] `test_oauth_bearer_auth_refreshes_on_unauthorized_response`
- [ ] `test_oauth_doctor_redacts_tokens`
- [ ] `test_codex_provider_uses_oauth_backed_openai_compat`
- [ ] `uv run pytest tests/coding_agent/test_codex_oauth.py tests/cli/test_oauth_commands.py tests/coding_agent/plugins/test_llm_provider.py -k "oauth or codex" -v`

## References

- `src/coding_agent/oauth/`
- `src/coding_agent/cli/oauth_commands.py`
- `src/coding_agent/plugins/llm_provider.py`
- `tests/coding_agent/test_codex_oauth.py`
- `tests/cli/test_oauth_commands.py`
- `/workspace/ya-oauth-ref/packages/ya-oauth/`
- `/workspace/ya-oauth-ref/packages/ya-oauth-provider/`
