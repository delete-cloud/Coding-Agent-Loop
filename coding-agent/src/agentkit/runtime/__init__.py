from __future__ import annotations

from typing import Protocol, runtime_checkable

from .hook_runtime import HookRuntime
from .hookspecs import HOOK_SPECS, HookSpec
from .pipeline import Pipeline, PipelineContext


@runtime_checkable
class Lifecycle(Protocol):
    async def on_startup(self) -> None: ...

    async def on_shutdown(self, timeout: float = 30.0) -> None: ...

    async def health_check(self) -> dict[str, object]: ...

    async def readiness_check(self) -> bool: ...


__all__ = [
    "HOOK_SPECS",
    "HookRuntime",
    "HookSpec",
    "Lifecycle",
    "Pipeline",
    "PipelineContext",
]
