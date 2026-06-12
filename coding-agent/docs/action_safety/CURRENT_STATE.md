# Action Safety And Workspace Execution Current State

Date: 2026-05-19
Branch: `codex/action-safety-g25-current-state`

This document records the current safe action execution, file edit, shell, approval, and workspace archive entrypoints before implementing G26-G37. It is intentionally descriptive; code and tests remain the source of truth.

## Baseline Boundaries

- `agentkit` owns generic tool registration, tool schema, pipeline hooks, and safe runtime span boundaries.
- `coding_agent` owns concrete file, patch, shell, sandbox, approval policy, local/cloud environment, and workspace archive behavior.
- Durable runtime G00-G11 and Context System G12-G24 are baseline infrastructure for this phase. Action safety may feed validation outcomes into context, but must not change durable runtime or context-system semantics unless a later goal explicitly proves that integration boundary.
- Existing postmortem routing flags `src/coding_agent/tools/file_ops.py`, `src/coding_agent/tools/shell.py`, and `src/coding_agent/plugins/core_tools.py` through PM-0001 and PM-0009. Later production changes in those files need focused tests and control-flow review.

## Existing File Tools

`src/coding_agent/environment/local.py` builds local file tools from `src/coding_agent/tools/file_ops.py`, which provides model-facing tools:

- `file_read`
- `file_write`
- `file_replace`
- `glob_files`
- `grep_search`

The tools can be built against a specific workspace root. When configured, path resolution rejects paths outside that root. `file_read` and `grep_search` can return structured results inside their structured-results scopes. Current write and replace operations apply directly; there is no typed edit plan, risk classification, preview, or dry-run contract.

`src/coding_agent/environment/cloud.py` defines a separate `CloudEnvironment` file-tool surface that delegates read, write, replace, glob, grep, and patch calls to a `CloudWorkspaceClient`. Those cloud tools do not use the local filesystem path resolver directly; provider/client behavior is responsible for remote workspace enforcement.

`src/coding_agent/tools/file_patch_tool.py` provides the local `file_patch`, a unified-diff hunk applier for existing files. It:

- parses `@@` hunks,
- searches nearby context when line numbers drift,
- writes through a temporary file and replace,
- returns a JSON string with success, changed state, bytes written, and per-hunk results.

Current patch behavior does not expose a separate pre-application patch plan, file-size limit, binary detection, symlink policy, multi-file transaction, or approval risk level.

## Existing Shell And Sandbox Tools

`src/coding_agent/environment/local.py` builds the local shell tool from `src/coding_agent/tools/shell.py`. That local tool provides `bash_run`. Despite the name, it executes a single command through `subprocess.run(..., shell=False)` after `shlex.split`.

Current safety behavior:

- rejects empty commands,
- rejects shell metacharacters such as `&&`, `||`, `|`, `;`, redirects, and backgrounding,
- supports simple `cd` and `export` session synchronization through `CoreToolsPlugin`,
- validates explicit `cwd` against the workspace root,
- rejects absolute path arguments outside the workspace in `sandbox_mode = "none"`,
- supports structured stdout/stderr/exit-code results inside its structured-results scope,
- delegates to `coding_agent.tools.sandbox` for `none`, `native`, `podman`, or `docker` sandbox modes.

`src/coding_agent/tools/sandbox.py` provides local runner implementations and guards for cwd, Docker/Podman env names, resource limits, and command path escape detection. Docker and Podman are explicit container modes; `native` resolves to macOS Seatbelt or Linux bubblewrap and `nsjail` is rejected.

`src/coding_agent/environment/cloud.py` defines a separate cloud `bash_run` wrapper. It supports `cd` and `export` session-style responses, validates cwd under the cloud workspace default cwd, and delegates the raw command string to `CloudWorkspaceClient.run_command`. It does not use the local `_parse_command`, local shell-metacharacter rejection, local sandbox modes, local absolute-path argument escape checks, or local structured shell result scope.

Current shell behavior does not have a first-class command policy object for allow/deny/approval decisions, risk scoring, known-safe validation commands, or reusable validation result summaries across both local and cloud environments.

## Existing Approval Policy

`src/coding_agent/approval/policy.py` provides `ApprovalPolicy`, `PolicyConfig`, and `PolicyEngine`.

Current policy is tool-name based:

- `YOLO` approves all tools,
- `INTERACTIVE` requires approval for all tools,
- `AUTO` approves only configured safe tool names,
- default safe tools are `file_read`, `repo_list`, and `git_status`.

`src/coding_agent/approval/coordinator.py` coordinates pending approval requests and session-scoped approvals. Current approval state does not classify individual file paths, patch sizes, destructive commands, cwd/env risk, or validation-only commands.

## Existing Core Tool Wiring

`src/coding_agent/plugins/core_tools.py` registers file, patch, shell, planner, web search, and subagent tools from the active environment. For `bash_run`, it injects shell-session cwd/env state and syncs successful `cd` and `export` results back into the session. The active environment may be local or cloud, so later action-safety enforcement must account for both tool implementations.

This is the main app-layer execution boundary for future action-safety enforcement. Later goals should keep generic pipeline semantics in `agentkit` and put product-specific file/command risk policy in `coding_agent`.

## Existing Workspace Archive

`src/coding_agent/workspace_archive.py` provides base64 tar.gz workspace archive create/extract helpers.

Current safety behavior:

- excludes `.git`, `__pycache__`, `.pyc`, and `.pyo`,
- rejects symlinks during archive creation,
- rejects path traversal and preserved root members during extraction,
- enforces archive byte, tar stream, and member-count limits,
- extracts to a temporary directory before clearing the target,
- preserves `.git` while reconciling deleted files.

This is close to a snapshot/restore primitive, but it is transport-oriented and base64 archive-oriented. G33 should add a local snapshot/restore MVP that is explicit about temporary workspace behavior and failure recovery.

## Existing Tests

Focused tests already cover parts of the action-safety surface:

- `tests/coding_agent/tools/test_file_ops.py` covers workspace path rejection for file read and glob.
- `tests/coding_agent/tools/test_shell.py` covers basic command output, stderr, exit code display, and shell metacharacter rejection.
- `tests/approval/test_policy.py` covers tool-name based approval policy behavior.
- `tests/coding_agent/environment/test_workspace_archive.py` covers archive traversal, symlink, size, invalid archive, too-many-members, and `.git` preservation behavior.

The tests are deterministic and use local temporary workspaces. They do not yet cover patch planning, edit risk classification, command policy, validation runner outcomes, action observability, or snapshot/restore MVP behavior.

## Gaps For G26-G37

- No ADR captures the action-safety ownership boundary, policy model, validation contract, or snapshot/restore trade-offs.
- File edits and patches apply directly instead of flowing through typed plans, previews, and risk classification.
- Safe edit policy lacks explicit file size, binary, symlink, path, and workspace-boundary checks across write/replace/patch operations.
- Command execution lacks a reusable policy model for allow, deny, approval, cwd/env, timeout, path escape, local/cloud differences, and validation-only commands.
- Validation/test execution has no structured runner contract or reusable outcome model.
- Action observability does not yet emit safe action metadata counters/spans for edit, command, validation, approval, or restore behavior.
- Workspace archive helpers exist, but there is no local workspace snapshot/restore MVP designed for action recovery.
- Approval is currently tool-name based, not action-risk based.
- There is no end-to-end smoke test proving patch, command, validation, and restore behavior together.

## Implementation Rules For Later Goals

- Keep AgentKit Core generic and avoid AgentKit pipeline rewrites.
- Keep concrete file, command, approval, and workspace policy in `coding_agent`.
- Preserve existing JSONL compatibility and durable runtime behavior.
- Use deterministic tests with fixtures, fakes, and temporary workspaces.
- Keep observability attributes free of raw prompts, content, messages, command output, file content, secrets, and text payloads.
- Do not implement schedules, desktop, bridge, proactive autonomous-agent behavior, or a full Docker sandbox in this phase.
