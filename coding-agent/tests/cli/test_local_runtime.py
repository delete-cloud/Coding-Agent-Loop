"""Tests for local CLI runtime/session boundary behavior."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from coding_agent.cli.local_runtime import ServerBackedLocalCliSessionManager
from coding_agent.stores.local import local_sqlite_storage_config


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
        self.additional_directory_updates = []
        self.owner_acquires = []
        self.renew_owner_leases_called = 0
        self.renew_owner_leases_event = asyncio.Event()
        self.release_owned_sessions_called = False
        self.close_called = False

    async def _persist_session_async(self, session) -> None:
        self.persisted.append(session)

    async def acquire_session_owner(self, session_id: str) -> None:
        self.owner_acquires.append(session_id)

    async def update_session_additional_directories(
        self,
        session_id: str,
        additional_directories: list[str],
    ) -> None:
        self.additional_directory_updates.append((session_id, additional_directories))

    async def release_owned_sessions(self) -> None:
        self.release_owned_sessions_called = True

    async def renew_owner_leases(self) -> None:
        self.renew_owner_leases_called += 1
        self.renew_owner_leases_event.set()

    async def close(self) -> None:
        self.close_called = True


async def test_server_backed_local_cli_attach_runtime_uses_session_binding_delegate() -> (
    None
):
    manager = object.__new__(ServerBackedLocalCliSessionManager)
    delegate = FakeDelegate()
    manager._delegate = delegate
    session = FakeManagedSession()
    pipeline = object()
    ctx = SimpleNamespace(tape=SimpleNamespace(tape_id="repl-tape"))
    adapter = object()

    await manager.attach_runtime(
        session,
        pipeline=pipeline,
        pipeline_ctx=ctx,
        pipeline_adapter=adapter,
    )

    assert session.attached == (pipeline, ctx, adapter)
    assert session.tape_id == "repl-tape"
    assert delegate.persisted == [session]


async def test_server_backed_local_cli_delegates_additional_directory_updates() -> None:
    manager = object.__new__(ServerBackedLocalCliSessionManager)
    delegate = FakeDelegate()
    manager._delegate = delegate

    await manager.update_session_additional_directories(
        "session-1",
        ["/workspace/extra"],
    )

    assert delegate.additional_directory_updates == [
        ("session-1", ["/workspace/extra"])
    ]


async def test_server_backed_local_cli_delegates_owner_acquire() -> None:
    manager = object.__new__(ServerBackedLocalCliSessionManager)
    delegate = FakeDelegate()
    manager._delegate = delegate

    await manager.acquire_session_owner("session-1")

    assert delegate.owner_acquires == ["session-1"]


async def test_server_backed_local_cli_releases_owner_leases_before_close() -> None:
    manager = object.__new__(ServerBackedLocalCliSessionManager)
    delegate = FakeDelegate()
    manager._delegate = delegate
    manager._owner_renew_task = None

    await manager.close()

    assert delegate.release_owned_sessions_called is True
    assert delegate.close_called is True


async def test_server_backed_local_cli_renews_owner_leases_until_close() -> None:
    manager = object.__new__(ServerBackedLocalCliSessionManager)
    delegate = FakeDelegate()
    manager._delegate = delegate
    manager.owner_store = object()
    manager.owner_renew_interval_seconds = 0.001
    manager._owner_renew_task = None

    await manager.start_owner_lease_renewal()
    await asyncio.wait_for(delegate.renew_owner_leases_event.wait(), timeout=1.0)
    await manager.close()

    assert delegate.renew_owner_leases_called > 0
    assert manager._owner_renew_task is None
    assert delegate.release_owned_sessions_called is True
    assert delegate.close_called is True


async def test_server_backed_local_cli_default_sqlite_owner_starts_renewal(
    tmp_path,
) -> None:
    manager = ServerBackedLocalCliSessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
        owner_renew_interval_seconds=0.001,
    )

    await manager.start_owner_lease_renewal()
    try:
        assert manager.owner_store is not None
        assert manager._owner_renew_task is not None
    finally:
        await manager.close()


async def test_server_backed_local_cli_normalizes_paths_local_owner_store(
    tmp_path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    manager = ServerBackedLocalCliSessionManager(
        storage_config={
            "paths": {"local": "~/bundle/local.sqlite3"},
            "http_session_backend": "sqlite",
            "tape_backend": "sqlite",
            "checkpoint_backend": "sqlite",
            "runtime_backend": "sqlite",
        },
    )

    try:
        expected_path = home / "bundle" / "local.sqlite3"
        assert manager.owner_store._path.resolve() == expected_path.resolve()
        assert manager._delegate._local_durable_store is not None
        assert (
            manager._delegate._local_durable_store._path.resolve()
            == expected_path.resolve()
        )
        assert not Path("./~").exists()
    finally:
        await manager.close()


async def test_server_backed_local_cli_default_owner_id_is_process_unique(
    tmp_path,
) -> None:
    first = ServerBackedLocalCliSessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
    )
    second = ServerBackedLocalCliSessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
    )

    try:
        assert isinstance(first.owner_id, str)
        assert isinstance(second.owner_id, str)
        assert first.owner_id.startswith("local-cli:")
        assert second.owner_id.startswith("local-cli:")
        assert first.owner_id != second.owner_id
    finally:
        await first.close()
        await second.close()
