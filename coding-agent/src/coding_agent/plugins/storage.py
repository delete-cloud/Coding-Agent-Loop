"""StoragePlugin — provides tape storage and session management."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnannotatedClassAttribute=false, reportUnusedParameter=false, reportUnusedCallResult=false

from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import time
import uuid
from inspect import isawaitable
from pathlib import Path
from typing import Any, Callable, cast

from agentkit.storage.protocols import SessionStore, TapeStore
from agentkit.storage.session import FileSessionStore
from agentkit.tape.store import ForkTapeStore


def _load_pg_types() -> tuple[Any, Any, Any, Any]:
    try:
        pg_module = importlib.import_module("agentkit.storage.pg")
    except ImportError as exc:
        raise RuntimeError(
            "PG backend is not available; add agentkit.storage.pg before using backend='pg'"
        ) from exc
    return (
        getattr(pg_module, "PGPool"),
        getattr(pg_module, "PGSessionLock"),
        getattr(pg_module, "PGSessionStore"),
        getattr(pg_module, "PGTapeStore"),
    )


class JSONLTapeStore:
    """Simple JSONL-based TapeStore implementation."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, tape_id: str) -> Path:
        return self._base_dir / f"{tape_id}.jsonl"

    async def save(self, tape_id: str, entries: list[dict[str, Any]]) -> None:
        path = self._path_for(tape_id)

        def _write() -> None:
            mode = "a" if path.exists() else "w"
            with open(path, mode) as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)

    async def load(self, tape_id: str) -> list[dict[str, Any]]:
        path = self._path_for(tape_id)
        if not path.exists():
            return []

        def _read() -> list[dict[str, Any]]:
            entries = []
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
            return entries

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _read)

    async def list_ids(self) -> list[str]:
        return [p.stem for p in self._base_dir.glob("*.jsonl")]

    async def truncate(self, tape_id: str, keep: int) -> None:
        if keep < 0:
            raise ValueError("keep must be >= 0")
        path = self._path_for(tape_id)
        if not path.exists():
            return

        def _truncate() -> None:
            kept_lines: list[str] = []
            with open(path, encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= keep:
                        break
                    kept_lines.append(line)

            fd, temp_path = tempfile.mkstemp(
                dir=path.parent,
                prefix=f"{path.name}.",
                suffix=".tmp",
                text=True,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.writelines(kept_lines)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
            except Exception:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
                raise

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _truncate)

    def append_memory_record(self, tape_id: str, record: dict[str, Any]) -> None:
        path = self._path_for(tape_id)
        entry = {
            "id": str(uuid.uuid4()),
            "kind": "memory_record",
            "payload": record,
            "timestamp": time.time(),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def load_memory_records(self, tape_id: str) -> list[dict[str, Any]]:
        path = self._path_for(tape_id)
        if not path.exists():
            return []

        records: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                if raw.get("kind") == "memory_record":
                    payload = raw.get("payload")
                    if isinstance(payload, dict):
                        records.append(payload)
        return records

    def replace_memory_records(
        self, tape_id: str, records: list[dict[str, Any]]
    ) -> None:
        path = self._path_for(tape_id)
        retained_lines: list[str] = []
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    raw = json.loads(stripped)
                    if raw.get("kind") != "memory_record":
                        retained_lines.append(stripped)

        with open(path, "w", encoding="utf-8") as f:
            for line in retained_lines:
                f.write(line + "\n")
            for record in records:
                entry = {
                    "id": str(uuid.uuid4()),
                    "kind": "memory_record",
                    "payload": record,
                    "timestamp": time.time(),
                }
                f.write(json.dumps(entry) + "\n")


class StoragePlugin:
    """Plugin providing storage backends."""

    state_key = "storage"

    def __init__(
        self,
        data_dir: Path | None,
        config: dict[str, Any] | None = None,
        backend: str | None = None,
        pg_pool: object | None = None,
    ) -> None:
        resolved_data_dir = data_dir or Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        self._data_dir = resolved_data_dir
        self._config = config or {}
        self._backend = (
            (backend or self._config.get("tape_backend", "jsonl")).strip().lower()
        )
        self._pg_pool = pg_pool
        self._fork_store: ForkTapeStore | None = None
        self._session_store: SessionStore | None = None
        self._jsonl_store: JSONLTapeStore | None = None
        self._session_lock: object | None = None

        if self._backend == "pg" and self._pg_pool is not None:
            _, PGSessionLock, _, _ = _load_pg_types()
            self._session_lock = PGSessionLock(pool=self._pg_pool)

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "provide_storage": self.provide_storage,
            "mount": self.do_mount,
            "on_shutdown": self.on_shutdown,
        }

    def provide_storage(self, **kwargs: Any) -> ForkTapeStore:
        if self._fork_store is None:
            backing = self._create_tape_store()
            self._fork_store = ForkTapeStore(backing)
        return self._fork_store

    def _get_pg_pool(self) -> object:
        PGPool, _, _, _ = _load_pg_types()
        if self._pg_pool is not None:
            return self._pg_pool

        dsn = self._config.get("dsn")
        if not isinstance(dsn, str) or not dsn.strip():
            raise RuntimeError("PG backend requires pg_pool or storage.dsn")

        self._pg_pool = PGPool(dsn=dsn)
        return self._pg_pool

    def _create_tape_store(self) -> TapeStore:
        if self._backend == "pg":
            _, PGSessionLock, _, PGTapeStore = _load_pg_types()
            pool = self._get_pg_pool()
            if self._session_lock is None:
                self._session_lock = PGSessionLock(pool=pool)
            return cast(TapeStore, PGTapeStore(pool=pool))
        return self._get_jsonl_store()

    def _get_jsonl_store(self) -> JSONLTapeStore:
        if self._jsonl_store is None:
            self._jsonl_store = JSONLTapeStore(self._data_dir / "tapes")
        return self._jsonl_store

    def load_memory_records(self, session_id: str) -> list[dict[str, Any]]:
        return self._get_jsonl_store().load_memory_records(session_id)

    def append_memory_record(self, session_id: str, record: dict[str, Any]) -> None:
        self._get_jsonl_store().append_memory_record(session_id, record)

    def replace_memory_records(
        self, session_id: str, records: list[dict[str, Any]]
    ) -> None:
        self._get_jsonl_store().replace_memory_records(session_id, records)

    def _create_session_store(self) -> SessionStore:
        backend = (
            str(
                self._config.get(
                    "session_backend", "pg" if self._backend == "pg" else "file"
                )
            )
            .strip()
            .lower()
        )
        if backend == "file":
            return FileSessionStore(self._data_dir / "sessions")
        if backend == "pg":
            _, _, PGSessionStore, _ = _load_pg_types()
            dsn = self._config.get("dsn")
            if self._backend != "pg" and (not isinstance(dsn, str) or not dsn.strip()):
                raise ValueError(
                    "storage.dsn is required when storage.session_backend='pg'"
                )
            return cast(SessionStore, PGSessionStore(pool=self._get_pg_pool()))
        raise ValueError(f"unsupported storage.session_backend: {backend}")

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        if self._session_store is None:
            self._session_store = self._create_session_store()
        return {"session_store": self._session_store, "plugin": self}

    async def on_shutdown(self, **kwargs: Any) -> None:
        pool = (
            getattr(self._session_store, "_pool", None)
            if self._session_store is not None
            else None
        )
        if pool is None:
            pool = self._pg_pool
        close = getattr(pool, "close", None)
        if callable(close):
            close_result = close()
            if isawaitable(close_result):
                await close_result

    @property
    def session_lock(self) -> object | None:
        return self._session_lock
