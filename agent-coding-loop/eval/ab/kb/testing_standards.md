# Testing Standards

All Go code in this project must meet these testing requirements.

## Coverage Requirements

- Every exported Go function **must** have at least one corresponding test.
- Unexported helper functions should be tested indirectly through the exported functions that call them.
- Test files must reside in the same package as the code they test (no `_test` package suffix for white-box tests).

## Test Naming Convention

- Test names must follow the pattern: `Test<FunctionName><Scenario>`.
- Example: `TestValidateConfigMissingAPIKey`, `TestParseChunkSizeUpperBound`.
- Do not use generic names like `TestConfig1` or `TestHappyPath`.

## Table-Driven Tests

- Functions with multiple distinct input/output cases **must** use table-driven tests.
- Each test case in the table must have a `name` field used in `t.Run(tc.name, ...)`.
- Prefer descriptive case names: `"negative chunk_size returns error"` over `"case3"`.

## Mocking External Dependencies

- All external dependencies (HTTP calls, database operations, file system writes) must be mocked in tests using interfaces.
- **Never** make real network calls in unit tests.
- Use constructor injection to pass mock implementations: `NewService(opts ...Option)` pattern.
- If a test accidentally makes a real HTTP call, it must be considered a bug and fixed immediately.
