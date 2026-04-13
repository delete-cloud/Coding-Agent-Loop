from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agentkit.checkpoint.models import CheckpointMeta, CheckpointSnapshot
from agentkit.checkpoint.serialize import (
    extract_serializable_states,
    validate_json_safe,
)
from agentkit.runtime.pipeline import PipelineContext
from agentkit.storage.protocols import CheckpointStore
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape


class CheckpointService:
    def __init__(self, store: CheckpointStore) -> None:
        self._store = store

    async def capture(
        self,
        ctx: PipelineContext,
        label: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> CheckpointMeta:
        payload = extra or {}
        validate_json_safe(payload, name="extra")
        meta = CheckpointMeta(
            checkpoint_id=uuid4().hex,
            tape_id=ctx.tape.tape_id,
            session_id=ctx.session_id,
            entry_count=len(ctx.tape),
            window_start=ctx.tape.window_start,
            created_at=datetime.now(UTC),
            label=label,
        )
        snapshot = CheckpointSnapshot(
            meta=meta,
            tape_entries=tuple(entry.to_dict() for entry in ctx.tape.snapshot()),
            plugin_states=extract_serializable_states(ctx.plugin_states),
            extra=payload,
        )
        await self._store.save(snapshot)
        return meta

    async def restore(self, checkpoint_id: str) -> CheckpointSnapshot:
        snapshot = await self._store.load(checkpoint_id)
        if snapshot is None:
            raise KeyError(f"Checkpoint {checkpoint_id!r} not found")
        return snapshot

    async def reconstruct_tape(
        self, checkpoint_id: str, tape_id: str | None = None
    ) -> tuple[Tape, dict[str, Any], dict[str, Any]]:
        snapshot = await self.restore(checkpoint_id)
        tape = Tape(
            entries=[Entry.from_dict(entry) for entry in snapshot.tape_entries],
            tape_id=tape_id or snapshot.meta.tape_id,
            _window_start=snapshot.meta.window_start,
        )
        return tape, dict(snapshot.plugin_states), dict(snapshot.extra)

    async def list(self, tape_id: str) -> list[CheckpointMeta]:
        return await self._store.list_by_tape(tape_id)

    async def delete(self, checkpoint_id: str) -> None:
        await self._store.delete(checkpoint_id)
