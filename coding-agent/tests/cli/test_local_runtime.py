"""Tests for local CLI runtime/session boundary behavior."""

from types import SimpleNamespace

from coding_agent.cli.local_runtime import ServerBackedLocalCliSessionManager


class FakeManagedSession:
    __slots__ = ("attached", "tape_id")

    def __init__(self) -> None:
        self.attached = None
        self.tape_id = None

    def attach_runtime_binding(self, *, pipeline, ctx, adapter) -> None:
        self.attached = (pipeline, ctx, adapter)


class FakeDelegate:
    def __init__(self) -> None:
        self.persisted = []

    def _persist_session(self, session) -> None:
        self.persisted.append(session)


def test_server_backed_local_cli_attach_runtime_uses_session_binding_delegate() -> None:
    manager = object.__new__(ServerBackedLocalCliSessionManager)
    delegate = FakeDelegate()
    manager._delegate = delegate
    session = FakeManagedSession()
    pipeline = object()
    ctx = SimpleNamespace(tape=SimpleNamespace(tape_id="repl-tape"))
    adapter = object()

    manager.attach_runtime(
        session,
        pipeline=pipeline,
        pipeline_ctx=ctx,
        pipeline_adapter=adapter,
    )

    assert session.attached == (pipeline, ctx, adapter)
    assert session.tape_id == "repl-tape"
    assert delegate.persisted == [session]
