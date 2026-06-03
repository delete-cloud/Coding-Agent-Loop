from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


RuntimeAgentFactory = Callable[..., tuple[object, object]]


@dataclass(frozen=True)
class RuntimeAgentFactoryService:
    create_agent: RuntimeAgentFactory | None = None

    def create_agent_for_session(self, **kwargs: Any) -> tuple[object, object]:
        factory = self.create_agent
        if factory is None:
            factory = importlib.import_module("coding_agent.__main__").create_agent
        return factory(**kwargs)


__all__ = [
    "RuntimeAgentFactory",
    "RuntimeAgentFactoryService",
]
