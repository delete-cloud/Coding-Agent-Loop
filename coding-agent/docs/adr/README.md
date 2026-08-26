# ADRs

This directory is the long-lived decision log for this repository.

Code is the source of truth. Tests are the executable proof that a decision still holds. Historical spec documents may remain in the repo as archived design context, but they are not maintained.

## When To Write An ADR

- Persistence, protocol, or data-model changes
- Cross-module boundary changes
- Decisions with multiple reasonable options and trade-offs
- Decisions that someone will likely ask "why" about later

## When Not To Write An ADR

- Straightforward bug fixes
- Local refactors
- Obvious implementation details with no real trade-off

## Lifecycle

- Name files `NNNN-short-kebab-case.md`
- Use sequential numbers in this directory
- Valid statuses are `Proposed`, `Accepted`, and `Superseded`
- Once an ADR is `Accepted`, treat its body as immutable
- If the decision changes, write a new ADR and mark the old one `Superseded`
- Do not create new spec documents for routine work; use discussion to think, ADRs to record decisions, tests to verify them

## Required Sections

- `Context`
- `Decision`
- `Alternatives Rejected`
- `Acceptance Criteria`
- `References`

## Acceptance Criteria

- Name concrete tests
- Include the command that verifies them
- If implementation is still pending, record the intended test names and verification command that will gate the work

## Template

```md
# ADR-NNNN: Short decision title

**Status**: Proposed
**Date**: YYYY-MM-DD

## Context

2-3 short paragraphs on why this decision is needed.

## Decision

State the choice directly and briefly.

## Alternatives Rejected

- Alternative A — why it was rejected
- Alternative B — why it was rejected

## Acceptance Criteria

- [ ] `test_name_one`
- [ ] `test_name_two`
- [ ] `uv run pytest path/to/tests -k "name_one or name_two" -v`

## References

- `path/to/code.py`
- `path/to/tests.py`
- Archived design context if needed
```
