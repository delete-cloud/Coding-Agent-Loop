Goal:
Demote `coding_agent run` from product-entrypoint language to a dev/testkit
one-shot compatibility path, as required by ADR-0058.

Scope:
- Update CLI help and local error guidance so `run` is described as a
  dev/testkit one-shot compatibility path.
- Update README/current-state docs that still describe `run` as batch mode.
- Add a CLI help contract test that prevents the command from drifting back to
  first-class product wording.
- Preserve current runtime behavior, `--patch`, verification commands, storage,
  HTTP, REPL, and session semantics.

Out of scope:
- Do not remove `coding_agent run`.
- Do not hide `run` from Click help in this slice.
- Do not change `run --patch` behavior.
- Do not implement the local daemon process or daemon client here.
- Do not rename public functions if doing so would expand compatibility risk.

Context:
- ADR: `docs/adr/0058-local-daemon-control-plane-executor-architecture.md`
- Relevant files:
  - `src/coding_agent/cli/main.py`
  - `src/coding_agent/cli/__init__.py`
  - `src/coding_agent/ui/headless.py`
  - `README.md`
  - `docs/dogfood/CURRENT_STATE.md`
  - `tests/cli/test_entrypoint_contract.py`
  - `tests/coding_agent/test_cli_pipeline.py`

Target tests:
- `uv run pytest tests/cli/test_entrypoint_contract.py -v`
- `uv run pytest tests/coding_agent/test_cli_pipeline.py -k one_shot -v`
- `uv run ruff check src/coding_agent/cli/main.py src/coding_agent/cli/__init__.py src/coding_agent/ui/headless.py tests/cli/test_entrypoint_contract.py tests/coding_agent/test_cli_pipeline.py`
- `git diff --check`

Review fallback:
- If CodeRabbit is rate-limited, run a local subagent P1/P2 review before merge.
