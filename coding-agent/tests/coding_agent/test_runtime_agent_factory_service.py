from __future__ import annotations

import types
from typing import Any

from coding_agent.runs import RuntimeAgentFactoryService


def test_runtime_agent_factory_service_uses_injected_factory() -> None:
    observed_kwargs: dict[str, Any] = {}
    pipeline = object()
    ctx = object()

    def create_agent(**kwargs: Any) -> tuple[object, object]:
        observed_kwargs.update(kwargs)
        return pipeline, ctx

    service = RuntimeAgentFactoryService(create_agent=create_agent)

    assert service.create_agent_for_session(workspace_root="/repo") == (pipeline, ctx)
    assert observed_kwargs == {"workspace_root": "/repo"}


def test_runtime_agent_factory_service_lazily_loads_default_factory(
    monkeypatch,
) -> None:
    observed_module_names: list[str] = []
    observed_kwargs: dict[str, Any] = {}
    pipeline = object()
    ctx = object()

    def create_agent(**kwargs: Any) -> tuple[object, object]:
        observed_kwargs.update(kwargs)
        return pipeline, ctx

    def import_module(name: str) -> object:
        observed_module_names.append(name)
        return types.SimpleNamespace(create_agent=create_agent)

    monkeypatch.setattr(
        "coding_agent.runs.agent_factory.importlib.import_module",
        import_module,
    )

    service = RuntimeAgentFactoryService()

    assert service.create_agent_for_session(model_override="model-1") == (
        pipeline,
        ctx,
    )
    assert observed_module_names == ["coding_agent.__main__"]
    assert observed_kwargs == {"model_override": "model-1"}
