# HTTP API Conventions

All HTTP endpoints in this project must adhere to the following conventions.

## Response Headers

- Every response **must** include the `X-Request-Id` header containing a unique trace identifier (UUID v4). This applies to both success and error responses.
- The `X-Request-Id` value should be generated at the start of request handling and propagated through all downstream calls for distributed tracing.

## Error Response Format

- Error responses **must** return a JSON body with both `error` (human-readable message) and `code` (machine-readable error code) fields.
- Example: `{"error": "document not found", "code": "NOT_FOUND"}`
- Returning only an `error` field without `code` is non-compliant and must be corrected.

## Health Check Endpoint

- The health check endpoint (`/healthz` or `/health`) must return the format: `{"status": "ok", "version": "<semver>"}`.
- The `version` field must be populated from the build-time version string, never hardcoded.
- If any dependency is unhealthy, return `{"status": "degraded", "version": "..."}` with HTTP 503.

## Pagination

- All list endpoints **must** support query parameters `?limit=N&offset=M` for pagination.
- `limit` defaults to 20 and must be clamped to the range [1, 100].
- `offset` defaults to 0 and must be non-negative.
- Responses must include a `total` field indicating the full count of matching resources.
