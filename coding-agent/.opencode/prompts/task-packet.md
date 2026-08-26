Goal:
<GOAL>

Scope:
- <SCOPE ITEM 1>
- <SCOPE ITEM 2>
- <SCOPE ITEM 3>

Out of scope:
- <OUT OF SCOPE 1>
- <OUT OF SCOPE 2>

Context:
- ADRs:
  - <ADR PATH 1>
  - <ADR PATH 2>
- Relevant files:
  - <FILE 1>
  - <FILE 2>
  - <FILE 3>

Target tests:
- <TEST CMD 1>
- <TEST CMD 2>

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
