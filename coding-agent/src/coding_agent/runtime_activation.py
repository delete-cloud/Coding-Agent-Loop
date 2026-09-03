"""Immutable session runtime versions and the Phase F activation fence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal


RUNTIME_VERSION_LEGACY = "legacy"
RUNTIME_VERSION_NEW = "agentkit-1"
KNOWN_RUNTIME_VERSIONS = frozenset({RUNTIME_VERSION_LEGACY, RUNTIME_VERSION_NEW})
RuntimePath = Literal["legacy", "new"]

if TYPE_CHECKING:
    from agentkit.checkpoint.models import CheckpointSnapshot

CHECKPOINT_FORMAT_KEY = "checkpoint_format"
OPERATION_STATE_VERSION_KEY = "operation_state_version"


class UnknownRuntimeVersionError(ValueError):
    """Raised when a session carries an unknown runtime version."""


class CrossVersionWriteError(ValueError):
    """Raised when a write would change or mix session runtime versions."""


class NewRuntimeCheckpointRejectedError(RuntimeError):
    """Raised when new-runtime checkpoint capture or restore is attempted."""


class NewRuntimeSettledWriteError(ValueError):
    """Raised when a new-runtime session tries to write the legacy settled alias."""


@dataclass(frozen=True, slots=True)
class RuntimeActivationState:
    new_sessions_enabled: bool = False


def parse_runtime_version(payload: Mapping[str, object]) -> str:
    value = payload.get("runtime_version")
    if value is None:
        return RUNTIME_VERSION_LEGACY
    if not isinstance(value, str) or value not in KNOWN_RUNTIME_VERSIONS:
        raise UnknownRuntimeVersionError(f"unknown runtime_version: {value!r}")
    return value


def runtime_path_for_version(version: str) -> RuntimePath:
    if version == RUNTIME_VERSION_NEW:
        return "new"
    if version == RUNTIME_VERSION_LEGACY:
        return "legacy"
    raise UnknownRuntimeVersionError(f"unknown runtime_version: {version!r}")


def version_for_new_session(activation: RuntimeActivationState) -> str:
    if activation.new_sessions_enabled:
        return RUNTIME_VERSION_NEW
    return RUNTIME_VERSION_LEGACY


def stamp_session_payload_for_save(
    *,
    incoming: Mapping[str, object],
    stored: Mapping[str, object] | None,
    activation: RuntimeActivationState,
) -> dict[str, object]:
    """Return the payload to persist, fencing unknown and cross-version writes."""

    incoming_version = incoming.get("runtime_version")
    if incoming_version is not None:
        parsed_incoming = parse_runtime_version(incoming)
    else:
        parsed_incoming = None
    if stored is None:
        if parsed_incoming is None:
            version = version_for_new_session(activation)
        else:
            if (
                not activation.new_sessions_enabled
                and parsed_incoming == RUNTIME_VERSION_NEW
            ):
                raise CrossVersionWriteError(
                    "new-runtime sessions cannot be created while activation is off"
                )
            version = parsed_incoming
        return {**dict(incoming), "runtime_version": version}
    stored_version = parse_runtime_version(stored)
    if parsed_incoming is None:
        return {**dict(incoming), "runtime_version": stored_version}
    if parsed_incoming != stored_version:
        raise CrossVersionWriteError("session runtime_version is immutable")
    return {**dict(incoming), "runtime_version": stored_version}


def is_new_runtime_restore_point(snapshot: CheckpointSnapshot) -> bool:
    extra = snapshot.extra or {}
    return (
        extra.get(CHECKPOINT_FORMAT_KEY) == RUNTIME_VERSION_NEW
        and OPERATION_STATE_VERSION_KEY in extra
        and not snapshot.tape_entries
        and not snapshot.plugin_states
    )


def restore_point_stamp(
    extra: Mapping[str, object],
) -> Mapping[str, object] | None:
    if OPERATION_STATE_VERSION_KEY not in extra:
        return None
    stamp = extra[OPERATION_STATE_VERSION_KEY]
    if stamp is None:
        return None
    if not isinstance(stamp, Mapping):
        raise ValueError("operation_state_version must be an object or null")
    return stamp


def restore_point_cas_matches(
    stamp: Mapping[str, object],
    *,
    run_id: str,
    revision: int,
    projection_epoch: int,
    transition_id: str,
) -> bool:
    commit_ref = stamp.get("commit_ref")
    stored_transition = (
        commit_ref.get("transition_id") if isinstance(commit_ref, Mapping) else None
    )
    return (
        stamp.get("run_id") == run_id
        and stamp.get("revision") == revision
        and stamp.get("projection_epoch") == projection_epoch
        and stored_transition == transition_id
    )


def assert_checkpoint_allowed(
    payload: Mapping[str, object],
    snapshot: CheckpointSnapshot | None = None,
) -> None:
    version = parse_runtime_version(payload)
    restore_point = snapshot is not None and is_new_runtime_restore_point(snapshot)
    if version == RUNTIME_VERSION_NEW:
        if restore_point:
            return
        raise NewRuntimeCheckpointRejectedError(
            "new-runtime checkpoint capture and restore require RestorePoint format"
        )
    if restore_point:
        raise NewRuntimeCheckpointRejectedError(
            "legacy sessions cannot capture or restore agentkit-1 RestorePoints"
        )


def assert_effect_status_allowed(*, status: str, runtime_version: str) -> None:
    if not status:
        raise ValueError("status must be non-empty")
    if runtime_version not in KNOWN_RUNTIME_VERSIONS:
        raise UnknownRuntimeVersionError(
            f"unknown runtime_version: {runtime_version!r}"
        )
    if runtime_version == RUNTIME_VERSION_NEW and status == "settled":
        raise NewRuntimeSettledWriteError("new-runtime sessions cannot write settled")


ServingTurnKind = Literal["pipeline_adapter", "durable_segment_runner"]


def serving_turn_kind(payload: Mapping[str, object]) -> ServingTurnKind:
    if runtime_path_for_version(parse_runtime_version(payload)) == "new":
        return "durable_segment_runner"
    return "pipeline_adapter"


def assert_legacy_terminal_writer_allowed(payload: Mapping[str, object]) -> None:
    assert_effect_status_allowed(
        status="settled",
        runtime_version=parse_runtime_version(payload),
    )
