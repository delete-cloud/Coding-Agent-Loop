from __future__ import annotations

from dataclasses import dataclass

import pytest

from coding_agent.runs import RuntimeBindingSnapshot, RuntimeReplacementService


@dataclass
class FakeTape:
    tape_id: str


@dataclass
class FakeContext:
    tape: FakeTape


class FakeSession:
    def __init__(self) -> None:
        self.id = "session-1"
        self.provider = object()
        self.provider_name = "old-provider"
        self.model_name = "old-model"
        self.base_url = "https://old.example.com"
        self.tape_id = "old-tape"
        self.pipeline = "old-pipeline"
        self.ctx = FakeContext(FakeTape("old-tape"))
        self.adapter = "old-adapter"

    def runtime_binding_snapshot(self) -> RuntimeBindingSnapshot:
        return RuntimeBindingSnapshot(
            pipeline=self.pipeline,
            ctx=self.ctx,
            adapter=self.adapter,
        )

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None:
        self.pipeline = pipeline
        self.ctx = ctx
        self.adapter = adapter

    def restore_runtime_binding(self, snapshot: RuntimeBindingSnapshot) -> None:
        self.pipeline = snapshot.pipeline
        self.ctx = snapshot.ctx
        self.adapter = snapshot.adapter


@pytest.mark.asyncio
async def test_runtime_replacement_service_replaces_runtime_and_closes_old() -> None:
    session = FakeSession()
    closed: list[object | None] = []
    persisted: list[tuple[str | None, str | None, object | None]] = []

    async def build_runtime(
        runtime_session: FakeSession,
        *,
        model_name: str,
        provider_name: str | None = None,
        base_url: str | None = None,
    ) -> tuple[object, FakeContext, object]:
        assert runtime_session is session
        assert model_name == "new-model"
        assert provider_name is None
        assert base_url is None
        return "new-pipeline", FakeContext(FakeTape("new-tape")), "new-adapter"

    async def persist_session(runtime_session: FakeSession) -> None:
        persisted.append(
            (
                runtime_session.model_name,
                runtime_session.tape_id,
                runtime_session.provider,
            )
        )

    async def close_adapter(adapter: object | None) -> None:
        closed.append(adapter)

    result = await RuntimeReplacementService(
        close_runtime_adapter=close_adapter,
    ).replace_runtime_config(
        session,
        model_name="new-model",
        build_runtime=build_runtime,
        persist_session=persist_session,
    )

    assert result is session
    assert session.provider is None
    assert session.model_name == "new-model"
    assert session.tape_id == "new-tape"
    assert session.pipeline == "new-pipeline"
    assert session.adapter == "new-adapter"
    assert persisted == [("new-model", "new-tape", None)]
    assert closed == ["old-adapter"]


@pytest.mark.asyncio
async def test_runtime_replacement_service_rolls_back_when_persist_fails() -> None:
    session = FakeSession()
    old_provider = session.provider
    closed: list[object | None] = []

    async def build_runtime(
        runtime_session: FakeSession,
        *,
        model_name: str,
        provider_name: str | None = None,
        base_url: str | None = None,
    ) -> tuple[object, FakeContext, object]:
        del runtime_session, model_name, provider_name, base_url
        return "new-pipeline", FakeContext(FakeTape("new-tape")), "new-adapter"

    async def persist_session(runtime_session: FakeSession) -> None:
        assert runtime_session.model_name == "new-model"
        raise RuntimeError("persist failed")

    async def close_adapter(adapter: object | None) -> None:
        closed.append(adapter)

    with pytest.raises(RuntimeError, match="persist failed"):
        await RuntimeReplacementService(
            close_runtime_adapter=close_adapter,
        ).replace_runtime_config(
            session,
            model_name="new-model",
            build_runtime=build_runtime,
            persist_session=persist_session,
        )

    assert session.provider is old_provider
    assert session.provider_name == "old-provider"
    assert session.model_name == "old-model"
    assert session.base_url == "https://old.example.com"
    assert session.tape_id == "old-tape"
    assert session.pipeline == "old-pipeline"
    assert session.adapter == "old-adapter"
    assert closed == ["new-adapter"]


@pytest.mark.asyncio
async def test_runtime_replacement_service_ignores_old_adapter_close_failure() -> None:
    session = FakeSession()

    async def build_runtime(
        runtime_session: FakeSession,
        *,
        model_name: str,
        provider_name: str | None = None,
        base_url: str | None = None,
    ) -> tuple[object, FakeContext, object]:
        del runtime_session, model_name, provider_name, base_url
        return "new-pipeline", FakeContext(FakeTape("new-tape")), "new-adapter"

    async def persist_session(runtime_session: FakeSession) -> None:
        assert runtime_session.model_name == "new-model"

    async def close_adapter(adapter: object | None) -> None:
        assert adapter == "old-adapter"
        raise RuntimeError("close failed")

    result = await RuntimeReplacementService(
        close_runtime_adapter=close_adapter,
    ).replace_runtime_config(
        session,
        model_name="new-model",
        build_runtime=build_runtime,
        persist_session=persist_session,
    )

    assert result is session
    assert session.model_name == "new-model"
    assert session.tape_id == "new-tape"
    assert session.adapter == "new-adapter"
