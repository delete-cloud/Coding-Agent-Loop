# Configuration Validation Rules

These rules must be enforced when loading or updating the application configuration.

## ListenAddr

- `ListenAddr` must match the pattern `host:port` where port is in the range **1024–65535**.
- Ports below 1024 are reserved and must be rejected with: `"listen port must be >= 1024"`.
- An empty host component (e.g., `:8080`) is acceptable and means bind to all interfaces.

## DBPath

- `DBPath` must end with the `.db` file extension.
- Paths without this extension must be rejected with error: `"db_path must end with .db extension"`.
- The parent directory of `DBPath` must exist at startup; do not auto-create parent directories.

## Model Configuration

- If `Model.APIKey` is set but `Model.BaseURL` is empty, return a validation error with the exact message: `"api_key requires base_url"`.
- If `Model.BaseURL` is set, it must be a valid URL starting with `https://` or `http://`.
- `Model.APIKey` may be empty when using local models; no error should be raised in that case.

## Artifacts Directory Safety

- The configured `ArtifactsDir` **must not** be a parent directory of the repository root.
- This prevents the agent from accidentally overwriting source files with generated artifacts.
- Validation should resolve both paths to absolute form and check that `RepoRoot` does not have `ArtifactsDir` as a prefix.
- Violation must produce the error: `"artifacts_dir must not be a parent of repo_root"`.
