"""In-memory restore semantics reference for ADR-0075 and ADR-0076."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, NoReturn


class RestoreError(RuntimeError):
    """Base error for rejected restore-store operations."""


class UnknownCheckpointError(RestoreError):
    """Raised when a restore names a checkpoint that does not exist."""


class FenceError(RestoreError):
    """Raised when session ownership does not authorize a restore."""


class CursorBindingError(RestoreError):
    """Raised when a projection cursor is used outside its binding."""

    def __init__(self, code: str, **details: object) -> None:
        super().__init__(code)
        self.code = code
        self.details = details

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, **self.details}


@dataclass
class RunRecord:
    run_id: str
    session_id: str
    started_at: str
    superseded_at: str | None = None
    superseded_by_checkpoint_id: str | None = None


@dataclass(frozen=True)
class CheckpointRecord:
    checkpoint_id: str
    session_id: str
    created_at: str | None
    lane_cuts: dict[str, int]
    effect_states: dict[int, str]


@dataclass(frozen=True)
class EventRecord:
    session_id: str
    session_seq: int
    projection_epoch: int
    kind: str


@dataclass(frozen=True)
class RestoreResult:
    transaction_count: int
    atomic_writes: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class CrossHostResolution:
    values: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return dict(self.values)


@dataclass(frozen=True)
class _ProjectionCursor:
    kind: str
    session_id: str
    projection: str
    epoch: int
    offset: int


class RestoreStore:
    """Pure in-memory model of fenced, atomic checkpoint restore semantics."""

    def __init__(
        self,
        *,
        session_id: str = "session-7",
        owner_id: str = "daemon-b",
        owner_epoch: int = 12,
        owner_lease_live: bool = True,
        retention_floor: int = 0,
    ) -> None:
        self._session_id = session_id
        self._owner_id = owner_id
        self._owner_epoch = owner_epoch
        self._owner_lease_live = owner_lease_live
        self._retention_floor = retention_floor
        self._runs: dict[str, RunRecord] = {}
        self._run_events: dict[str, list[str]] = {}
        self._checkpoints: dict[str, CheckpointRecord] = {}
        self._mailbox: dict[str, list[tuple[int, str]]] = {}
        self._lane_cuts: dict[str, int] = {}
        self._effects: dict[int, str] = {}
        self._events: dict[str, list[EventRecord]] = {}
        self._next_session_seq: dict[str, int] = {}
        self._projection_epochs: dict[str, int] = {session_id: 0}

    def add_run(
        self,
        run_id: str,
        *,
        started_at: str,
        session_id: str | None = None,
    ) -> RunRecord:
        target_session = self._target_session(session_id)
        if run_id in self._runs:
            raise ValueError(f"run already exists: {run_id}")
        record = RunRecord(run_id, target_session, started_at)
        self._runs[run_id] = record
        self._run_events[run_id] = []
        return record

    def add_event(self, run_id: str, *, event_id: str) -> None:
        if run_id not in self._runs:
            raise KeyError(run_id)
        self._run_events[run_id].append(event_id)

    def add_checkpoint(
        self,
        *,
        checkpoint_id: str,
        session_id: str | None = None,
        created_at: str | None = None,
        lane_cuts: Mapping[str, int] | None = None,
        effect_states: Mapping[int, str] | None = None,
    ) -> CheckpointRecord:
        target_session = self._target_session(session_id)
        if checkpoint_id in self._checkpoints:
            raise ValueError(f"checkpoint already exists: {checkpoint_id}")
        record = CheckpointRecord(
            checkpoint_id=checkpoint_id,
            session_id=target_session,
            created_at=created_at,
            lane_cuts=dict(lane_cuts or {}),
            effect_states=dict(effect_states or {}),
        )
        self._checkpoints[checkpoint_id] = record
        return record

    def append_mailbox(self, lane: str, *, sequence: int, payload: str) -> None:
        entries = self._mailbox.setdefault(lane, [])
        if any(existing == sequence for existing, _ in entries):
            raise ValueError(f"mailbox sequence already exists: {lane}:{sequence}")
        entries.append((sequence, payload))
        entries.sort(key=lambda entry: entry[0])

    def set_effect_state(self, *, effect_id: int, state: str) -> None:
        self._effects[effect_id] = state

    def append_event(
        self,
        session_id: str,
        *,
        projection_epoch: int,
        kind: str,
    ) -> EventRecord:
        current_epoch = self._projection_epochs.setdefault(session_id, 0)
        if projection_epoch != current_epoch:
            raise ValueError(
                f"projection epoch mismatch: expected {current_epoch}, got {projection_epoch}"
            )
        session_seq = self._next_session_seq.get(session_id, 1)
        event = EventRecord(session_id, session_seq, projection_epoch, kind)
        self._events.setdefault(session_id, []).append(event)
        self._next_session_seq[session_id] = session_seq + 1
        return event

    def restore(
        self,
        *,
        fence: Mapping[str, object],
        checkpoint_id: str,
        restored_at: str | None = None,
    ) -> RestoreResult:
        checkpoint = self._checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise UnknownCheckpointError(checkpoint_id)
        session_id = self._validate_fence(fence, checkpoint)

        runs = deepcopy(self._runs)
        lane_cuts = deepcopy(self._lane_cuts)
        effects = deepcopy(self._effects)
        events = deepcopy(self._events)
        next_session_seq = deepcopy(self._next_session_seq)
        projection_epochs = deepcopy(self._projection_epochs)
        writes: list[tuple[object, ...]] = []

        for lane, cut in sorted(checkpoint.lane_cuts.items()):
            lane_cuts[lane] = cut
            writes.append(("mailbox_lane_cut", lane, cut))
        for effect_id, state in sorted(checkpoint.effect_states.items()):
            effects[effect_id] = state
            writes.append(("effect", effect_id, state))

        superseded = [
            run
            for run in runs.values()
            if run.session_id == session_id
            and run.superseded_at is None
            and (
                checkpoint.created_at is None
                or run.started_at > checkpoint.created_at
            )
        ]
        if superseded and restored_at is None:
            raise ValueError("restored_at is required when restore supersedes runs")
        for run in sorted(superseded, key=lambda item: item.run_id):
            run.superseded_at = restored_at
            run.superseded_by_checkpoint_id = checkpoint_id
            writes.append(("run_superseded", run.run_id, checkpoint_id))

        new_epoch = projection_epochs.get(session_id, 0) + 1
        projection_epochs[session_id] = new_epoch
        writes.append(("projection_epoch", new_epoch))
        session_events = events.setdefault(session_id, [])
        has_open_turn = any(
            event.projection_epoch == new_epoch - 1 and event.kind == "turn_started"
            for event in session_events
        ) and not any(
            event.projection_epoch == new_epoch - 1
            and event.kind in {"turn_completed", "turn_failed", "turn_interrupted"}
            for event in session_events
        )
        if not has_open_turn:
            session_seq = next_session_seq.get(session_id, 1)
            session_events.append(
                EventRecord(session_id, session_seq, new_epoch, "restore_committed")
            )
            next_session_seq[session_id] = session_seq + 1

        self._runs = runs
        self._lane_cuts = lane_cuts
        self._effects = effects
        self._events = events
        self._next_session_seq = next_session_seq
        self._projection_epochs = projection_epochs
        return RestoreResult(1, tuple(writes))

    def audit_run_ids(self) -> tuple[str, ...]:
        return tuple(self._runs)

    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(
            run_id
            for run_id, run in self._runs.items()
            if run.superseded_at is None
        )

    def run(self, run_id: str) -> RunRecord:
        return self._runs[run_id]

    def event_ids(self, run_id: str) -> tuple[str, ...]:
        return tuple(self._run_events[run_id])

    def visible_mailbox_sequences(self, lane: str) -> tuple[int, ...]:
        cut = self._lane_cuts.get(lane)
        return tuple(
            sequence
            for sequence, _ in self._mailbox.get(lane, [])
            if cut is None or sequence <= cut
        )

    def effect_state(self, effect_id: int) -> str:
        return self._effects[effect_id]

    def raw_cursor(self, session_id: str, *, session_seq: int) -> str:
        return f"raw:{session_id}:{session_seq}"

    def read_raw(self, session_id: str, *, after: str | None = None) -> tuple[EventRecord, ...]:
        after_seq = 0
        if after is not None:
            prefix, cursor_session, encoded_seq = after.split(":", 2)
            if prefix != "raw" or cursor_session != session_id:
                raise CursorBindingError(
                    "cursor_session_mismatch",
                    expected_session_id=cursor_session,
                    actual_session_id=session_id,
                )
            after_seq = int(encoded_seq)
        return tuple(
            event
            for event in self._events.get(session_id, [])
            if event.session_seq > after_seq
        )

    def projection_cursor(
        self,
        *,
        kind: str,
        session_id: str,
        projection: str,
        epoch: int,
        offset: int,
    ) -> str:
        if kind not in {"delta", "settled"}:
            raise ValueError(f"unsupported projection cursor kind: {kind}")
        return f"{kind}:{session_id}:{projection}:{epoch}:{offset}"

    def read_projection(self, *, cursor: str, projection: str, epoch: int) -> int:
        binding = self._parse_projection_cursor(cursor)
        if binding.projection != projection:
            raise CursorBindingError(
                "cursor_projection_mismatch",
                expected_projection=binding.projection,
                actual_projection=projection,
            )
        if binding.epoch != epoch:
            raise CursorBindingError(
                "cursor_epoch_mismatch",
                expected_epoch=binding.epoch,
                actual_epoch=epoch,
            )
        return binding.offset

    def capture_error(
        self,
        error_type: type[RestoreError],
        operation: Callable[..., object],
        **kwargs: object,
    ) -> RestoreError:
        try:
            operation(**kwargs)
        except error_type as error:
            return error
        self._raise_missing_error(error_type)

    def resolve_cross_host_key_expired(
        self,
        *,
        session_id: str,
        expired_cursor: str,
        trusted_handoff: Mapping[str, object] | None,
    ) -> CrossHostResolution:
        prefix, cursor_session, encoded_seq = expired_cursor.split(":", 2)
        if prefix != "raw" or cursor_session != session_id:
            raise CursorBindingError(
                "cursor_session_mismatch",
                expected_session_id=cursor_session,
                actual_session_id=session_id,
            )
        int(encoded_seq)
        if trusted_handoff is None:
            return CrossHostResolution(
                {
                    "action": "replay_from_retention_floor",
                    "retention_floor": self._retention_floor,
                    "phase": "P2",
                }
            )
        host_id = trusted_handoff["host_id"]
        session_seq = trusted_handoff["session_seq"]
        if not isinstance(host_id, str) or not isinstance(session_seq, int):
            raise ValueError("trusted handoff requires string host_id and integer session_seq")
        return CrossHostResolution(
            {
                "action": "accept_trusted_handoff",
                "host_id": host_id,
                "session_seq": session_seq,
                "phase": "P2",
            }
        )

    def _target_session(self, session_id: str | None) -> str:
        return self._session_id if session_id is None else session_id

    def _validate_fence(
        self,
        fence: Mapping[str, object],
        checkpoint: CheckpointRecord,
    ) -> str:
        session_id = fence.get("session_id")
        owner_id = fence.get("owner_id")
        epoch = fence.get("epoch")
        if not isinstance(session_id, str):
            raise FenceError("fence requires a string session_id")
        if checkpoint.session_id != session_id:
            raise FenceError("checkpoint does not belong to fenced session")
        if session_id != self._session_id:
            raise FenceError("session is not owned by this store fence")
        if owner_id != self._owner_id or epoch != self._owner_epoch:
            raise FenceError("stale or foreign session owner fence")
        if not self._owner_lease_live:
            raise FenceError("session owner lease is not live")
        return session_id

    @staticmethod
    def _parse_projection_cursor(cursor: str) -> _ProjectionCursor:
        kind, session_id, projection, encoded_epoch, encoded_offset = cursor.split(":", 4)
        if kind not in {"delta", "settled"}:
            raise ValueError(f"unsupported projection cursor kind: {kind}")
        return _ProjectionCursor(
            kind, session_id, projection, int(encoded_epoch), int(encoded_offset)
        )

    @staticmethod
    def _raise_missing_error(error_type: type[RestoreError]) -> NoReturn:
        raise AssertionError(f"{error_type.__name__} was not raised")
