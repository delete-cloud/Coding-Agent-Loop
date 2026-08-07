#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "${script_dir}/.."

.devcontainer/setup.sh
uv run pytest tests/cli -q
corepack pnpm@10.23.0 --dir webui/app test
corepack pnpm@10.23.0 --dir webui/app typecheck
corepack pnpm@10.23.0 --dir webui/app build
