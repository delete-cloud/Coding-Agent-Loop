from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot


class FSCheckpointStore:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _validated_checkpoint_id(self, checkpoint_id: str) -> str:
        if checkpoint_id == "":
            raise ValueError("checkpoint_id must not be empty")

        candidate = Path(checkpoint_id)
        if candidate.is_absolute():
            raise ValueError("checkpoint_id must be a single relative path component")
        if len(candidate.parts) != 1:
            raise ValueError("checkpoint_id must be a single path component")
        if any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError("checkpoint_id must not contain path traversal components")
        if "\\" in checkpoint_id:
            raise ValueError("checkpoint_id must not contain path separators")

        return checkpoint_id

    def _meta_path(self, checkpoint_id: str) -> Path:
        safe_checkpoint_id = self._validated_checkpoint_id(checkpoint_id)
        return self._base_dir / f"{safe_checkpoint_id}.meta.json"

    def _entries_path(self, checkpoint_id: str) -> Path:
        safe_checkpoint_id = self._validated_checkpoint_id(checkpoint_id)
        return self._base_dir / f"{safe_checkpoint_id}.entries.jsonl"

    def _state_path(self, checkpoint_id: str) -> Path:
        safe_checkpoint_id = self._validated_checkpoint_id(checkpoint_id)
        return self._base_dir / f"{safe_checkpoint_id}.state.json"

    def _atomic_write_text(self, path: Path, content: str) -> None:
        fd, temp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f"{path.name}.",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
            raise

    async def save(self, snapshot: CheckpointSnapshot) -> None:
        checkpoint_id = snapshot.meta.checkpoint_id

        def _write() -> None:
            entries_path = self._entries_path(checkpoint_id)
            state_path = self._state_path(checkpoint_id)
            meta_path = self._meta_path(checkpoint_id)
            published_paths: list[Path] = []
            try:
                self._atomic_write_text(
                    entries_path,
                    "".join(
                        json.dumps(entry) + "\n" for entry in snapshot.tape_entries
                    ),
                )
                published_paths.append(entries_path)
                self._atomic_write_text(
                    state_path,
                    json.dumps(
                        {
                            "plugin_states": snapshot.plugin_states,
                            "extra": snapshot.extra,
                        }
                    ),
                )
                published_paths.append(state_path)
                self._atomic_write_text(
                    meta_path,
                    json.dumps(
                        {
                            "checkpoint_id": snapshot.meta.checkpoint_id,
                            "tape_id": snapshot.meta.tape_id,
                            "session_id": snapshot.meta.session_id,
                            "entry_count": snapshot.meta.entry_count,
                            "window_start": snapshot.meta.window_start,
                            "created_at": snapshot.meta.created_at.isoformat(),
                            "label": snapshot.meta.label,
                        }
                    ),
                )
                published_paths.append(meta_path)
            except Exception:
                for published_path in published_paths:
                    try:
                        published_path.unlink()
                    except FileNotFoundError:
                        pass
                raise

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)

    async def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        def _read() -> CheckpointSnapshot | None:
            meta_path = self._meta_path(checkpoint_id)
            if not meta_path.exists():
                return None
            meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
            state_raw = json.loads(
                self._state_path(checkpoint_id).read_text(encoding="utf-8")
            )
            entries: list[dict[str, Any]] = []
            with self._entries_path(checkpoint_id).open(encoding="utf-8") as handle:
                for line in handle:
                    stripped = line.strip()
                    if stripped:
                        entries.append(json.loads(stripped))
            meta = CheckpointMeta(
                checkpoint_id=meta_raw["checkpoint_id"],
                tape_id=meta_raw["tape_id"],
                session_id=meta_raw["session_id"],
                entry_count=meta_raw["entry_count"],
                window_start=meta_raw["window_start"],
                created_at=datetime.fromisoformat(meta_raw["created_at"]),
                label=meta_raw.get("label"),
            )
            return CheckpointSnapshot(
                meta=meta,
                tape_entries=tuple(entries),
                plugin_states=state_raw.get("plugin_states", {}),
                extra=state_raw.get("extra", {}),
            )

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _read)

    async def list_by_tape(self, tape_id: str) -> list[CheckpointMeta]:
        def _list() -> list[CheckpointMeta]:
            metas: list[CheckpointMeta] = []
            for path in self._base_dir.glob("*.meta.json"):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("tape_id") != tape_id:
                    continue
                metas.append(
                    CheckpointMeta(
                        checkpoint_id=raw["checkpoint_id"],
                        tape_id=raw["tape_id"],
                        session_id=raw.get("session_id"),
                        entry_count=raw["entry_count"],
                        window_start=raw["window_start"],
                        created_at=datetime.fromisoformat(raw["created_at"]),
                        label=raw.get("label"),
                    )
                )
            return sorted(metas, key=lambda meta: meta.created_at)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _list)

    async def delete(self, checkpoint_id: str) -> None:
        def _delete() -> None:
            for path in (
                self._meta_path(checkpoint_id),
                self._entries_path(checkpoint_id),
                self._state_path(checkpoint_id),
            ):
                if path.exists():
                    path.unlink()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _delete)
