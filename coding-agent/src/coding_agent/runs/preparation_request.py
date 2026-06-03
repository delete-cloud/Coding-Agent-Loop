from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .coordinator import RunRequest
from .target import RunTarget


class RuntimePreparationRequestSession(Protocol):
    id: str
    default_run_target: RunTarget | None


@dataclass(frozen=True)
class RuntimePreparationRequestService:
    run_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex

    def request_for_session(
        self,
        session: RuntimePreparationRequestSession,
        *,
        purpose: str = "runtime_preparation",
    ) -> RunRequest:
        if session.default_run_target is None:
            raise RuntimeError("session is missing default_run_target")
        return RunRequest(
            session_id=session.id,
            run_id=f"runtime-prepare-{self.run_id_factory()}",
            target=session.default_run_target,
            metadata={"purpose": purpose},
        )


__all__ = [
    "RuntimePreparationRequestService",
    "RuntimePreparationRequestSession",
]
