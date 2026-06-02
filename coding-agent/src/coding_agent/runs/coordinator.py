from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from .target import ExecutorRef, RunTarget


def _empty_metadata() -> dict[str, str]:
    return {}


def _require_non_empty(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")


def _validate_optional_non_empty(value: str | None, *, field_name: str) -> None:
    if value is not None:
        _require_non_empty(value, field_name=field_name)


def _copy_metadata(metadata: Mapping[str, str]) -> dict[str, str]:
    copied: dict[str, str] = {}
    for key, value in metadata.items():
        if not key.strip():
            raise ValueError("metadata keys must be non-empty")
        if not value.strip():
            raise ValueError(f"metadata value for {key} must be non-empty")
        copied[key] = value
    return copied


@dataclass(frozen=True)
class RunRequest:
    session_id: str
    run_id: str
    target: RunTarget
    input_summary: str | None = None
    input_ref: str | None = None
    resume_from_run_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        _require_non_empty(self.session_id, field_name="session_id")
        _require_non_empty(self.run_id, field_name="run_id")
        _validate_optional_non_empty(
            self.input_summary,
            field_name="input_summary",
        )
        _validate_optional_non_empty(self.input_ref, field_name="input_ref")
        _validate_optional_non_empty(
            self.resume_from_run_id,
            field_name="resume_from_run_id",
        )
        object.__setattr__(self, "metadata", _copy_metadata(self.metadata))


@dataclass(frozen=True)
class RunSubmission:
    session_id: str
    run_id: str
    target: RunTarget
    executor: ExecutorRef
    status: Literal["accepted"] = "accepted"
    metadata: Mapping[str, str] = field(default_factory=_empty_metadata)

    def __post_init__(self) -> None:
        _require_non_empty(self.session_id, field_name="session_id")
        _require_non_empty(self.run_id, field_name="run_id")
        object.__setattr__(self, "metadata", _copy_metadata(self.metadata))


class RunCoordinatorError(RuntimeError):
    """Base error for run coordination failures."""


class RunCoordinator(Protocol):
    async def submit_run(self, request: RunRequest) -> RunSubmission: ...


class DefaultRunCoordinator:
    async def submit_run(self, request: RunRequest) -> RunSubmission:
        return RunSubmission(
            session_id=request.session_id,
            run_id=request.run_id,
            target=request.target,
            executor=request.target.executor,
            metadata=request.metadata,
        )


__all__ = [
    "DefaultRunCoordinator",
    "RunCoordinator",
    "RunCoordinatorError",
    "RunRequest",
    "RunSubmission",
]
