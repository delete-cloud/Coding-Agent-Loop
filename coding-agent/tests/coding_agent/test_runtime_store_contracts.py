from __future__ import annotations

from typing import Any, cast

from coding_agent.stores.runtime_store import (
    JSONLRuntimeStore,
    PGRuntimeStore,
    SQLiteRuntimeStore,
)
from coding_agent.stores import (
    RuntimeCheckpointStore,
    RuntimeEventStore,
    RuntimeInteractionStore,
    RuntimeRunLifecycleStore,
    RuntimeRunRecoveryStore,
    RuntimeRunStore,
    RuntimeStore,
)


class _FakePGPool:
    pass


def test_jsonl_runtime_store_satisfies_runtime_store_contracts(tmp_path) -> None:
    store = JSONLRuntimeStore(tmp_path / "runtime")

    assert isinstance(store, RuntimeRunLifecycleStore)
    assert isinstance(store, RuntimeRunRecoveryStore)
    assert isinstance(store, RuntimeRunStore)
    assert isinstance(store, RuntimeEventStore)
    assert isinstance(store, RuntimeCheckpointStore)
    assert isinstance(store, RuntimeInteractionStore)
    assert isinstance(store, RuntimeStore)


def test_sqlite_runtime_store_satisfies_runtime_store_contracts(tmp_path) -> None:
    store = SQLiteRuntimeStore(tmp_path / "runtime.sqlite3")

    assert isinstance(store, RuntimeRunLifecycleStore)
    assert isinstance(store, RuntimeRunRecoveryStore)
    assert isinstance(store, RuntimeRunStore)
    assert isinstance(store, RuntimeEventStore)
    assert isinstance(store, RuntimeCheckpointStore)
    assert isinstance(store, RuntimeInteractionStore)
    assert isinstance(store, RuntimeStore)


def test_pg_runtime_store_satisfies_runtime_store_contracts() -> None:
    store = PGRuntimeStore(pool=cast(Any, _FakePGPool()))

    assert isinstance(store, RuntimeRunLifecycleStore)
    assert isinstance(store, RuntimeRunRecoveryStore)
    assert isinstance(store, RuntimeRunStore)
    assert isinstance(store, RuntimeEventStore)
    assert isinstance(store, RuntimeCheckpointStore)
    assert isinstance(store, RuntimeInteractionStore)
    assert isinstance(store, RuntimeStore)
