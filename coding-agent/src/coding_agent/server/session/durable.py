"""Durable store construction and PG/SQLite backend selection."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import (
    Any,
    cast,
)
from agentkit.errors import ConfigError
from agentkit.storage.checkpoint_fs import FSCheckpointStore
from agentkit.storage.pg import PGPool
from agentkit.storage.sqlite import (
    SQLiteCheckpointStore,
    SQLiteTapeStore,
)
from agentkit.checkpoint import CheckpointService
from agentkit.storage.protocols import (
    CheckpointStore,
    TapeStore,
)
from coding_agent.stores.local import (
    DURABLE_STORAGE_BACKEND_KEYS,
    DURABLE_STORAGE_PATH_KEYS,
    durable_storage_backend_values,
    normalize_storage_path,
)
from coding_agent.stores.durable_local import SQLiteLocalDurableStore
from coding_agent.stores.durable_pg import (
    FencedPGCheckpointStore,
    FencedPGRuntimeStore,
    FencedPGTapeStore,
    PGDurableStore,
)
from coding_agent.plugins.storage import JSONLTapeStore
from coding_agent.stores.runtime_store import (
    JSONLRuntimeStore,
    SQLiteRuntimeStore,
)
from coding_agent.stores import RuntimeStore
from coding_agent.runs import RunCoordinator
from coding_agent.server.stores.session_store import SessionStore
from coding_agent.server.stores.session_owner_store import SessionOwnerStoreProtocol
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coding_agent.runs.turn_execution import RuntimeTurnService
from coding_agent.server.session import _bindings

logger = logging.getLogger("coding_agent.server.session_manager")


def _custom_store_names(
    *,
    store: object | None,
    tape_store: object | None,
    checkpoint_store: object | None,
    checkpoint_service: object | None,
    runtime_store: object | None,
) -> list[str]:
    names: list[str] = []
    if store is not None:
        names.append("store")
    if tape_store is not None:
        names.append("tape_store")
    if checkpoint_store is not None:
        names.append("checkpoint_store")
    if checkpoint_service is not None:
        names.append("checkpoint_service")
    if runtime_store is not None:
        names.append("runtime_store")
    return names


def _load_pg_storage_types() -> tuple[Any, Any, Any]:
    try:
        pg_module = _bindings.module().importlib.import_module("agentkit.storage.pg")
    except ImportError as exc:
        raise RuntimeError(
            "PG backend is not available; ensure agentkit.storage.pg and its PostgreSQL "
            "optional dependencies are installed before using tape_backend='pg' "
            "(for example, install/include the PG extra or `asyncpg`)."
        ) from exc
    required_symbols = ("PGPool", "PGTapeStore", "PGCheckpointStore")
    missing_symbols = [
        symbol for symbol in required_symbols if not hasattr(pg_module, symbol)
    ]
    if missing_symbols:
        raise RuntimeError(
            "PG backend is missing required exports from agentkit.storage.pg: "
            f"{', '.join(missing_symbols)}. Ensure the installed PG backend package "
            "version includes the PostgreSQL storage implementation and its optional "
            "dependencies."
        )
    return (
        getattr(pg_module, "PGPool"),
        getattr(pg_module, "PGTapeStore"),
        getattr(pg_module, "PGCheckpointStore"),
    )


class DurableOps:
    def _sqlite_storage_path(self, path_key: str, default: Path) -> Path:
        path_obj = self._storage_config.get(path_key)
        if isinstance(path_obj, str) and path_obj.strip():
            return normalize_storage_path(path_obj)
        return default

    def _create_local_durable_store(
        self,
        *,
        owner_store: SessionOwnerStoreProtocol | None,
    ) -> SQLiteLocalDurableStore | None:
        if owner_store is None or not callable(
            getattr(owner_store, "acquire_authority", None)
        ):
            return None
        config = self._storage_config
        backend_values = durable_storage_backend_values(config)
        if all(value == "pg" for value in backend_values.values()):
            return None
        sqlite_backend_keys = [
            key for key, value in backend_values.items() if value == "sqlite"
        ]
        if sqlite_backend_keys and len(sqlite_backend_keys) != len(
            DURABLE_STORAGE_BACKEND_KEYS
        ):
            mismatches = ", ".join(
                f"{key}={config.get(key)!r}"
                for key, value in backend_values.items()
                if value != "sqlite"
            )
            raise ConfigError(
                "durable fencing requires all local sqlite backends when any "
                f"local sqlite backend is configured; mismatched backends: {mismatches}"
            )
        if not sqlite_backend_keys:
            return None
        # Custom stores own their path semantics; validate sqlite paths only when
        # SessionManager will create the local durable bundle itself.
        if self._custom_store_names:
            logger.warning(
                "durable fencing disabled: custom %s supplied",
                ", ".join(self._custom_store_names),
            )
            return None
        local_path = self._local_sqlite_bundle_path
        configured_paths = {
            key: normalize_storage_path(str(config.get(key, "")))
            for key in DURABLE_STORAGE_PATH_KEYS
        }
        path_mismatches = [
            f"{key}={config.get(key)!r}"
            for key, path in configured_paths.items()
            if path != local_path
        ]
        if path_mismatches:
            mismatch_text = ", ".join(path_mismatches)
            raise ConfigError(
                "durable fencing requires sqlite storage paths to share "
                f"{local_path}; mismatched paths: {mismatch_text}"
            )
        return SQLiteLocalDurableStore(local_path)

    def _configure_pg_durable_store_if_available(self) -> None:
        if self._pg_durable_store is not None:
            return
        if self._local_durable_store is not None:
            return
        if self._owner_store is None or not callable(
            getattr(self._owner_store, "acquire_authority", None)
        ):
            return
        if any(
            str(self._storage_config.get(key, "")).strip().lower() != "pg"
            for key in DURABLE_STORAGE_BACKEND_KEYS
        ):
            return
        if self._custom_store_names:
            logger.warning(
                "durable fencing disabled: custom %s supplied",
                ", ".join(self._custom_store_names),
            )
            return
        pg_pool = self._get_pg_pool()
        durable_store = PGDurableStore(pool=pg_pool)
        self._pg_durable_store = durable_store
        self._tape_store = FencedPGTapeStore(
            durable_store=durable_store,
            pool=pg_pool,
            authority_for_session=self._owner_authority_for_session,
        )
        self._checkpoint_service = CheckpointService(
            FencedPGCheckpointStore(
                durable_store=durable_store,
                pool=pg_pool,
                authority_for_session=self._owner_authority_for_session,
            )
        )
        self._runtime_store = FencedPGRuntimeStore(
            durable_store=durable_store,
            pool=pg_pool,
            authority_for_session=self._owner_authority_for_session,
            authorities=lambda: dict(self._owner_authorities),
        )

    def configure_runtime_store(
        self,
        runtime_store: RuntimeStore | None,
    ) -> None:
        self._runtime_store = runtime_store
        self._runtime_turn_service = self._build_runtime_turn_service()

    def configure_run_coordinator(self, run_coordinator: RunCoordinator) -> None:
        self._run_coordinator = run_coordinator
        self._runtime_turn_service = self._build_runtime_turn_service()

    def _build_runtime_turn_service(self) -> RuntimeTurnService:
        return self._runtime_turn_service_factory.build(self._run_coordinator)

    def _require_runtime_store(self) -> RuntimeStore:
        if self._runtime_store is None:
            raise RuntimeError("runtime store is not configured")
        return self._runtime_store

    def _get_pg_pool(self) -> PGPool:
        if self._pg_pool is not None:
            return cast(PGPool, self._pg_pool)

        pg_pool_type, _, _ = _bindings.module()._load_pg_storage_types()

        dsn_obj = self._storage_config.get("dsn")
        if not isinstance(dsn_obj, str) or not dsn_obj.strip():
            raise RuntimeError("PG storage requires storage_config['dsn']")
        dsn = dsn_obj.strip()
        self._pg_pool = pg_pool_type(dsn=dsn)
        self._owns_pg_pool = True
        return cast(PGPool, self._pg_pool)

    def _create_http_session_store(self) -> SessionStore:
        configured_backend = self._storage_config.get("http_session_backend")
        tape_backend = (
            str(self._storage_config.get("tape_backend", "jsonl")).strip().lower()
        )
        if configured_backend is None:
            legacy_backend = self._storage_config.get("session_backend")
            if (
                isinstance(legacy_backend, str)
                and legacy_backend.strip().lower() == "pg"
            ):
                configured_backend = "pg"
            elif tape_backend == "pg":
                configured_backend = "pg"

        backend = (
            configured_backend.strip().lower()
            if isinstance(configured_backend, str)
            else None
        )
        dsn = self._storage_config.get("dsn")
        session_path = self._storage_config.get("http_session_path")
        if backend == "sqlite":
            session_path = str(
                self._sqlite_storage_path(
                    "http_session_path",
                    self._local_sqlite_bundle_path,
                )
            )
        return _bindings.module().create_session_store(
            backend=backend,
            dsn=dsn if isinstance(dsn, str) else None,
            pg_pool=None,
            file_path=session_path if isinstance(session_path, str) else None,
        )

    def _create_tape_store(self, data_dir: Path) -> TapeStore:
        backend = str(self._storage_config.get("tape_backend", "jsonl")).strip().lower()
        if backend == "pg":
            _, PGTapeStore, _ = _bindings.module()._load_pg_storage_types()
            return cast(TapeStore, PGTapeStore(pool=self._get_pg_pool()))
        if backend == "sqlite":
            path = self._sqlite_storage_path(
                "tape_path", self._local_sqlite_bundle_path
            )
            return SQLiteTapeStore(path)
        return JSONLTapeStore(data_dir / "tapes")

    def _create_checkpoint_store(self, data_dir: Path) -> CheckpointStore:
        tape_backend = (
            str(self._storage_config.get("tape_backend", "jsonl")).strip().lower()
        )
        default_backend = "pg" if tape_backend == "pg" else "fs"
        backend = (
            str(self._storage_config.get("checkpoint_backend", default_backend))
            .strip()
            .lower()
        )
        if backend == "pg":
            _, _, PGCheckpointStore = _bindings.module()._load_pg_storage_types()
            return cast(CheckpointStore, PGCheckpointStore(pool=self._get_pg_pool()))
        if backend == "sqlite":
            path = self._sqlite_storage_path(
                "checkpoint_path",
                self._local_sqlite_bundle_path,
            )
            return SQLiteCheckpointStore(path)
        return FSCheckpointStore(data_dir / "checkpoints")

    def _create_runtime_store(self) -> RuntimeStore | None:
        configured_backend = self._storage_config.get("runtime_backend")
        if configured_backend is None:
            return None
        if not isinstance(configured_backend, str):
            raise ValueError("storage.runtime_backend must be a string")
        backend = configured_backend.strip().lower()
        if backend in {"", "none", "disabled"}:
            return None
        if backend == "pg":
            return _bindings.module().PGRuntimeStore(pool=self._get_pg_pool())
        if backend in {"jsonl", "fs", "file"}:
            path_obj = self._storage_config.get("runtime_path")
            root = (
                Path(path_obj)
                if isinstance(path_obj, str) and path_obj.strip()
                else Path(os.environ.get("AGENT_DATA_DIR", "./data")) / "runtime"
            )
            return JSONLRuntimeStore(root)
        if backend == "sqlite":
            path = self._sqlite_storage_path(
                "runtime_path",
                self._local_sqlite_bundle_path,
            )
            return SQLiteRuntimeStore(path)
        raise ValueError(f"unsupported storage.runtime_backend: {backend}")
