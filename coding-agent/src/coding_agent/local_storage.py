from __future__ import annotations

import os
from pathlib import Path
from typing import Any


LOCAL_SQLITE_FILENAME = "local.sqlite3"
DURABLE_STORAGE_BACKEND_PATH_KEYS = (
    ("http_session_backend", "http_session_path"),
    ("tape_backend", "tape_path"),
    ("checkpoint_backend", "checkpoint_path"),
    ("runtime_backend", "runtime_path"),
)
DURABLE_STORAGE_BACKEND_KEYS = tuple(
    backend_key for backend_key, _ in DURABLE_STORAGE_BACKEND_PATH_KEYS
)
DURABLE_STORAGE_PATH_KEYS = tuple(
    path_key for _, path_key in DURABLE_STORAGE_BACKEND_PATH_KEYS
)


def normalize_storage_path(path: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(path)))


def local_data_dir() -> Path:
    return Path(os.environ.get("AGENT_DATA_DIR", "./data"))


def local_sqlite_path(data_dir: Path | str | None = None) -> Path:
    root = Path(data_dir) if data_dir is not None else local_data_dir()
    return root / LOCAL_SQLITE_FILENAME


def local_sqlite_storage_config(
    data_dir: Path | str | None = None,
) -> dict[str, object]:
    path = str(local_sqlite_path(data_dir))
    return {
        "http_session_backend": "sqlite",
        "http_session_path": path,
        "tape_backend": "sqlite",
        "tape_path": path,
        "checkpoint_backend": "sqlite",
        "checkpoint_path": path,
        "runtime_backend": "sqlite",
        "runtime_path": path,
    }


def local_sqlite_path_from_storage_config(
    storage_config: dict[str, Any],
    data_dir: Path | str | None = None,
) -> Path:
    paths = storage_config.get("paths")
    if isinstance(paths, dict):
        local_path = paths.get("local")
        if isinstance(local_path, str) and local_path.strip():
            return Path(local_path)
    explicit_paths = [
        storage_config.get(path_key) for path_key in DURABLE_STORAGE_PATH_KEYS
    ]
    if all(isinstance(path, str) and path.strip() for path in explicit_paths):
        resolved_paths = [normalize_storage_path(str(path)) for path in explicit_paths]
        first = resolved_paths[0]
        if all(path == first for path in resolved_paths):
            return first
    return local_sqlite_path(data_dir)


def durable_storage_backend_values(
    storage_config: dict[str, Any],
) -> dict[str, str]:
    return {
        key: str(storage_config.get(key, "")).strip().lower()
        for key in DURABLE_STORAGE_BACKEND_KEYS
    }


def storage_has_any_sqlite_backend(storage_config: dict[str, Any]) -> bool:
    return any(
        value == "sqlite"
        for value in durable_storage_backend_values(storage_config).values()
    )


def storage_uses_local_sqlite_bundle(
    storage_config: dict[str, Any],
    data_dir: Path | str | None = None,
) -> bool:
    backend_values = durable_storage_backend_values(storage_config)
    if any(value != "sqlite" for value in backend_values.values()):
        return False
    local_path = normalize_storage_path(
        str(local_sqlite_path_from_storage_config(storage_config, data_dir))
    )
    return all(
        normalize_storage_path(str(storage_config.get(key, ""))) == local_path
        for key in DURABLE_STORAGE_PATH_KEYS
    )


def with_local_sqlite_bundle_paths(
    storage_config: dict[str, Any],
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    config = dict(storage_config)
    local_path = str(local_sqlite_path_from_storage_config(config, data_dir))
    for backend_key, path_key in DURABLE_STORAGE_BACKEND_PATH_KEYS:
        backend = config.get(backend_key)
        if isinstance(backend, str) and backend.strip().lower() == "sqlite":
            configured_path = config.get(path_key)
            if not (isinstance(configured_path, str) and configured_path.strip()):
                config[path_key] = local_path
    return config
