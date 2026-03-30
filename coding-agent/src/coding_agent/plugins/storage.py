"""StoragePlugin — provides tape storage and session management."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from agentkit.storage.session import FileSessionStore
from agentkit.tape.store import ForkTapeStore


class JSONLTapeStore:
    """Simple JSONL-based TapeStore implementation."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, tape_id: str, entries: list[dict]) -> None:
        path = self._base_dir / f"{tape_id}.jsonl"

        def _write() -> None:
            mode = "a" if path.exists() else "w"
            with open(path, mode) as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)

    async def load(self, tape_id: str) -> list[dict]:
        path = self._base_dir / f"{tape_id}.jsonl"
        if not path.exists():
            return []

        def _read() -> list[dict]:
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


class StoragePlugin:
    """Plugin providing storage backends."""

    state_key = "storage"

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._fork_store: ForkTapeStore | None = None
        self._session_store: FileSessionStore | None = None

    def hooks(self) -> dict[str, Callable[..., Any]]:
        return {
            "provide_storage": self.provide_storage,
            "mount": self.do_mount,
        }

    def provide_storage(self, **kwargs: Any) -> ForkTapeStore:
        if self._fork_store is None:
            backing = JSONLTapeStore(self._data_dir / "tapes")
            self._fork_store = ForkTapeStore(backing)
        return self._fork_store

    def do_mount(self, **kwargs: Any) -> dict[str, Any]:
        if self._session_store is None:
            self._session_store = FileSessionStore(self._data_dir / "sessions")
        return {"session_store": self._session_store}
