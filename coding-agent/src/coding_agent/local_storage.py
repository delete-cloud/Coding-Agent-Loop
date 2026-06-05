from __future__ import annotations

import os
from pathlib import Path
from typing import Any


LOCAL_SQLITE_FILENAME = "local.sqlite3"


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
    return local_sqlite_path(data_dir)


def with_local_sqlite_bundle_paths(
    storage_config: dict[str, Any],
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    config = dict(storage_config)
    local_path = str(local_sqlite_path_from_storage_config(config, data_dir))
    backend_path_pairs = (
        ("http_session_backend", "http_session_path"),
        ("tape_backend", "tape_path"),
        ("checkpoint_backend", "checkpoint_path"),
        ("runtime_backend", "runtime_path"),
    )
    for backend_key, path_key in backend_path_pairs:
        backend = config.get(backend_key)
        if isinstance(backend, str) and backend.strip().lower() == "sqlite":
            configured_path = config.get(path_key)
            if not (isinstance(configured_path, str) and configured_path.strip()):
                config[path_key] = local_path
    return config
