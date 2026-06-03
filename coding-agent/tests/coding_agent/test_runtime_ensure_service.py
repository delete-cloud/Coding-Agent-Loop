from __future__ import annotations

from dataclasses import dataclass

from coding_agent.runs import RuntimeEnsureService


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
