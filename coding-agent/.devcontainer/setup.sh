#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "${script_dir}/.."

if [[ "$(id -u)" == "0" ]]; then
  echo "development commands must run as a non-root user" >&2
  exit 1
fi

check_version() {
  local tool="$1"
  local expected="$2"
  local actual="$3"

  if [[ "${actual}" != "${expected}" ]]; then
    echo "expected ${tool} ${expected}, got ${actual}" >&2
    exit 1
  fi
}

check_version "Python" "Python 3.12.11" "$(python --version)"
check_version "Node.js" "v20.19.5" "$(node --version)"
read -r uv_name uv_number _ <<< "$(uv --version)"
check_version "uv" "uv 0.12.1" "${uv_name} ${uv_number}"
check_version "Ruff" "ruff 0.15.12" "$(ruff --version)"
check_version "pnpm" "10.23.0" "$(corepack pnpm@10.23.0 --version)"

uv sync --all-extras

corepack pnpm@10.23.0 config set --global store-dir "${HOME}/.local/share/pnpm/store"
pnpm_store_path="$(corepack pnpm@10.23.0 store path)"
mkdir -p -- "${pnpm_store_path}"
pnpm_store_path="$(cd -- "${pnpm_store_path}" && pwd -P)"
workspace_path="$(pwd -P)"
case "${pnpm_store_path}" in
  "${workspace_path}"|"${workspace_path}"/*)
    echo "pnpm store must be outside the workspace: ${pnpm_store_path}" >&2
    exit 1
    ;;
esac

CI=true corepack pnpm@10.23.0 --dir webui/app install --frozen-lockfile
