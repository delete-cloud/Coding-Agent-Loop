# Embedding Configuration Best Practices

This document defines validation rules and defaults for embedding configuration.

## Chunk Size Validation

- The `chunk_size` parameter must be validated on startup: it must be **>= 100** and **<= 8192**.
- If `chunk_size` falls outside this range, return a configuration error with the message: `"chunk_size must be between 100 and 8192"`.

## Overlap Validation

- The `overlap` parameter must be strictly less than `chunk_size / 2`.
- Violating this constraint should produce the error: `"overlap must be less than half of chunk_size"`.
- Default overlap should be set to `chunk_size / 4` when not explicitly configured.

## Local Embedding Provider

- When the embedding provider is set to `"local"`, model loading **must** log the resolved model file path at INFO level.
- Log format: `"loading local embedding model" path=<absolute_path>`.
- If the model file does not exist at the configured path, fail fast with a clear error rather than falling back silently.

## Embedding API Timeout

- The timeout for embedding API calls must be configurable via `embedding.timeout` (duration string).
- Default timeout is **30 seconds**.
- Maximum allowed timeout is **120 seconds**; values above 120s must be clamped to 120s and a warning logged.
- A timeout of 0 or negative is invalid and should produce a configuration error.
