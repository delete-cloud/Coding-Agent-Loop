# ADR-0057: Codex Provider Uses Responses Wire Protocol

**Status**: Proposed
**Date**: 2026-06-02

## Context

The current `codex` provider reuses the OpenAI-compatible Chat Completions provider and sends requests to `https://chatgpt.com/backend-api/codex/chat/completions`. The official Codex CLI does not use that path for ChatGPT-authenticated Codex traffic.

OpenAI Codex CLI uses the Responses wire API against `https://chatgpt.com/backend-api/codex/responses`, with ChatGPT bearer auth plus `ChatGPT-Account-ID`, Codex identity headers, and SSE/WebSocket streaming semantics. Treating the ChatGPT Codex backend as Chat Completions causes Cloudflare/browser-gate failures and protocol-level mismatches.

## Decision

Replace the `codex` provider implementation with a Codex-specific Responses provider. The first implementation will support HTTP SSE against `/responses`; WebSocket prewarm/reuse remains a later enhancement.

The `openai` and `openai_compat` providers continue to use Chat Completions through `OpenAICompatProvider`. Only `provider="codex"` switches to the Codex Responses provider.

## Alternatives Rejected

- Keep `codex` on `OpenAICompatProvider` — rejected because the official Codex CLI no longer uses Chat Completions for Codex traffic.
- Add a new parallel `codex-responses` provider — rejected because the existing `codex` provider name already means ChatGPT-authenticated Codex and should have correct semantics.
- Implement WebSocket first — rejected to keep the first repair narrow; the official CLI also has HTTP Responses fallback behavior.

## Acceptance Criteria

- [ ] `test_codex_provider_uses_responses_provider`
- [ ] `test_stream_posts_to_codex_responses_with_chatgpt_headers`
- [ ] `test_stream_converts_function_call_output_item_done`
- [ ] `uv run pytest tests/providers/test_codex_responses.py tests/coding_agent/plugins/test_llm_provider.py -k "codex" -v`

## References

- `src/coding_agent/providers/codex_responses.py`
- `src/coding_agent/plugins/llm_provider.py`
- `tests/providers/test_codex_responses.py`
- `tests/coding_agent/plugins/test_llm_provider.py`
- `https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs`
- `https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/endpoint/responses.rs`
- `https://github.com/openai/codex/blob/main/codex-rs/model-provider/src/bearer_auth_provider.rs`
