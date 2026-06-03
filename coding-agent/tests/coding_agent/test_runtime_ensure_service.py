from __future__ import annotations

from dataclasses import dataclass

import pytest

from coding_agent.runs import RuntimeEnsureOrchestrationService, RuntimeEnsureService


@dataclass
class FakeTape:
    tape_id: str


@dataclass
class FakeContext:
    tape: FakeTape


class FakeSession:
    def __init__(self) -> None:
        self.id = "session-1"
        self.runtime_ctx: FakeContext | None = None
        self.runtime_adapter: object | None = None
        self.pipeline: object | None = None
        self.tape_id: str | None = "old-tape"

    def attach_runtime_binding(
        self,
        *,
        pipeline: object,
        ctx: object,
        adapter: object,
    ) -> None:
        self.pipeline = pipeline
        self.runtime_ctx = ctx
        self.runtime_adapter = adapter


async def _unexpected_build_runtime(
    session: FakeSession,
) -> tuple[object, object, object]:
    del session
    raise AssertionError("runtime should not be rebuilt")


async def _unexpected_persist_session(session: FakeSession) -> None:
    del session
    raise AssertionError("session should not be persisted")


async def test_runtime_ensure_service_returns_existing_context() -> None:
    session = FakeSession()
    ctx = FakeContext(FakeTape("existing-tape"))
    session.runtime_ctx = ctx
    session.runtime_adapter = object()

    result = await RuntimeEnsureService().ensure_runtime(
        session,
        build_runtime=_unexpected_build_runtime,
        persist_session=_unexpected_persist_session,
    )

    assert result is ctx
    assert session.tape_id == "old-tape"


async def test_runtime_ensure_service_builds_attaches_and_persists_runtime() -> None:
    session = FakeSession()
    ctx = FakeContext(FakeTape("new-tape"))
    adapter = object()
    persisted: list[tuple[str, str | None]] = []

    async def build_runtime(
        runtime_session: FakeSession,
    ) -> tuple[object, FakeContext, object]:
        assert runtime_session is session
        return "pipeline", ctx, adapter

    async def persist_session(runtime_session: FakeSession) -> None:
        persisted.append((runtime_session.id, runtime_session.tape_id))

    result = await RuntimeEnsureService().ensure_runtime(
        session,
        build_runtime=build_runtime,
        persist_session=persist_session,
    )

    assert result is ctx
    assert session.pipeline == "pipeline"
    assert session.runtime_ctx is ctx
    assert session.runtime_adapter is adapter
    assert session.tape_id == "new-tape"
    assert persisted == [("session-1", "new-tape")]


async def test_runtime_ensure_orchestration_asserts_owner_loads_and_builds() -> None:
    session = FakeSession()
    ctx = FakeContext(FakeTape("orchestrated-tape"))
    calls: list[str] = []

    async def assert_owner(session_id: str) -> None:
        calls.append(f"owner:{session_id}")

    async def load_session(session_id: str) -> FakeSession:
        calls.append(f"load:{session_id}")
        return session

    async def build_runtime(
        runtime_session: FakeSession,
    ) -> tuple[object, FakeContext, object]:
        calls.append(f"build:{runtime_session.id}")
        return "pipeline", ctx, "adapter"

    async def persist_session(runtime_session: FakeSession) -> None:
        calls.append(f"persist:{runtime_session.id}:{runtime_session.tape_id}")

    result = await RuntimeEnsureOrchestrationService(
        ensure_service=RuntimeEnsureService(),
        assert_owner=assert_owner,
        load_session=load_session,
        build_runtime=build_runtime,
        persist_session=persist_session,
    ).ensure_session_runtime("session-1")

    assert result is ctx
    assert calls == [
        "owner:session-1",
        "load:session-1",
        "build:session-1",
        "persist:session-1:orchestrated-tape",
    ]


async def test_runtime_ensure_orchestration_does_not_load_on_owner_failure() -> None:
    calls: list[str] = []

    async def assert_owner(session_id: str) -> None:
        calls.append(f"owner:{session_id}")
        raise RuntimeError("stale owner")

    async def load_session(session_id: str) -> FakeSession:
        calls.append(f"load:{session_id}")
        return FakeSession()

    with pytest.raises(RuntimeError, match="stale owner"):
        await RuntimeEnsureOrchestrationService(
            ensure_service=RuntimeEnsureService(),
            assert_owner=assert_owner,
            load_session=load_session,
            build_runtime=_unexpected_build_runtime,
            persist_session=_unexpected_persist_session,
        ).ensure_session_runtime("session-1")

    assert calls == ["owner:session-1"]
