# PR Workflow Rules

These rules govern how the agent creates and manages pull requests.

## PR Title Format

- PR titles **must** follow the conventional commits format: `type(scope): description`.
- The `type` must be one of: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.
- The `scope` should be the primary package or module affected (e.g., `config`, `embedding`, `loop`).
- Example: `feat(embedding): add configurable timeout for API calls`.

## PR Body Structure

- The PR body **must** include at minimum two sections: `## Changes` and `## Testing`.
- `## Changes` should list what was modified and why, using bullet points.
- `## Testing` should describe how the changes were verified (tests added, manual checks, etc.).
- PRs missing either section must not be submitted; the agent should add placeholder sections if needed.

## Live Mode — No Changes Guard

- When `PRMode` is set to `"live"` but the working tree has no changed files, the agent must **skip PR creation entirely**.
- In this case, log the message: `"no changes to submit"` at INFO level and return success without error.
- Do not create an empty PR or a PR with only whitespace changes.

## Branch Naming

- Feature branches must be named `agent/<type>/<short-description>` (e.g., `agent/feat/add-timeout`).
- Branch names must be lowercase, using hyphens as word separators. No underscores or uppercase.
