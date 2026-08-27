from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from coding_agent.events.connected_chat import RootRunAlreadySettledError
from coding_agent.runs.lifecycle import RuntimeRunLifecycle
from coding_agent.server.http import events as http_events
from coding_agent.server.http.routes import prompts as prompt_routes
from coding_agent.server.stores.session_owner_store import (
    OwnerAuthority,
    SessionOwnershipConflictError,
)
from coding_agent.stores.runtime_store import AgentRunRecord
from tests.coding_agent.test_connected_chat_admission import _load_run
from tests.coding_agent.test_harness_p2_fact_source import (
    SESSION_ID,
    SESSION_PAYLOAD,
    _open_store,
)


async def _admit(store: Any, owner: Any, command_id: str = "terminal-command") -> Any:
    return await store.admit_chat_command(
        owner,
        prompt="persist terminal",
        command_id=command_id,
        parent_run_id=None,
        session_state=SESSION_PAYLOAD,
    )


async def _assert_terminal_atomic(kind: str, tmp_path: Path) -> None:
    store, owner = await _open_store(kind, tmp_path)
    admission = await _admit(store, owner)

    settlement = await store.settle_root_run(
        owner,
        run_id=admission.run_id,
        outcome="completed",
        result="done",
        error=None,
    )

    assert settlement.event.event_id == f"{admission.run_id}:root_terminal"
    assert settlement.event.event_kind == "root_terminal"
    assert settlement.event.payload == {
        "run_id": admission.run_id,
        "outcome": "completed",
        "result": "done",
        "error": None,
    }
    run = await _load_run(store, kind, admission.run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.ended_at is not None
    assert "text" not in run.result
    session = (
        store.load_session(SESSION_ID)
        if kind == "sqlite"
        else store._harness_pool.connection.session_payloads[SESSION_ID]
    )
    assert session is not None
    assert session["turn_in_progress"] is False
    assert session["turn_status"] == "completed"
    fact = await store.load_session_fact_source(SESSION_ID)
    assert fact.session_seq == "2"
    event = await store.load_event_record(SESSION_ID, "2")
    assert event == settlement.event


@pytest.mark.asyncio
async def test_terminal_uow_is_atomic_sqlite(tmp_path: Path) -> None:
    await _assert_terminal_atomic("sqlite", tmp_path)


@pytest.mark.asyncio
async def test_terminal_uow_is_atomic_postgresql(tmp_path: Path) -> None:
    await _assert_terminal_atomic("pg", tmp_path)


@pytest.mark.asyncio
async def test_terminal_uow_recovers_after_crash(tmp_path: Path) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    admission = await _admit(store, owner, "crash-command")
    first = await store.settle_root_run(
        owner,
        run_id=admission.run_id,
        outcome="failed",
        result=None,
        error="provider failed",
    )
    retried = await store.settle_root_run(
        owner,
        run_id=admission.run_id,
        outcome="failed",
        result=None,
        error="provider failed",
    )

    assert retried.event == first.event
    assert retried.idempotent is True
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "2"


@pytest.mark.asyncio
async def test_terminal_races_write_one_root_terminal(tmp_path: Path) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    admission = await _admit(store, owner, "race-command")
    barrier = asyncio.Event()

    async def settle(outcome: str) -> object:
        await barrier.wait()
        return await store.settle_root_run(
            owner,
            run_id=admission.run_id,
            outcome=outcome,
            result="done" if outcome == "completed" else None,
            error=None,
        )

    contenders = [
        asyncio.create_task(settle("completed")),
        asyncio.create_task(settle("cancelled")),
        asyncio.create_task(settle("interrupted")),
    ]
    barrier.set()
    results = await asyncio.gather(*contenders, return_exceptions=True)

    commits = [result for result in results if not isinstance(result, Exception)]
    conflicts = [
        result for result in results if isinstance(result, RootRunAlreadySettledError)
    ]
    assert len(commits) == 1
    assert len(conflicts) == 2
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "2"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled", "interrupted"])
async def test_runtime_finalization_has_no_non_atomic_escape_hatch(
    outcome: str,
) -> None:
    class RunStore:
        updates = 0

        async def update_agent_run(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            self.updates += 1

    settlements: list[tuple[str, str, str, str | None, str | None]] = []

    async def settle(
        session_id: str,
        *,
        run_id: str,
        outcome: str,
        result: str | None,
        error: str | None,
        **_kwargs: object,
    ) -> None:
        settlements.append((session_id, run_id, outcome, result, error))

    session = type("Session", (), {"id": "session-a", "tape_id": "tape-a"})()
    store = RunStore()
    lifecycle = RuntimeRunLifecycle(
        store=store,
        metadata_for_session=lambda _session, **_kwargs: {},
        settle_root_run=settle,
    )

    await lifecycle.finish(
        session,
        run_id="run-a",
        status=outcome,
        result={"text": "done"},
        error="failed" if outcome == "failed" else None,
    )

    assert store.updates == 0
    assert settlements == [
        (
            "session-a",
            "run-a",
            outcome,
            "done",
            "failed" if outcome == "failed" else None,
        )
    ]


@pytest.mark.asyncio
async def test_eof_alone_never_settles_root(tmp_path: Path) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    admission = await _admit(store, owner, "eof-command")
    run = await _load_run(store, "sqlite", admission.run_id)

    assert isinstance(run, AgentRunRecord)
    assert run.status == "requested"
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "1"


@pytest.mark.asyncio
async def test_resume_creates_distinct_linked_run_after_durable_settlement(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    first = await _admit(store, owner, "first-command")
    await store.settle_root_run(
        owner,
        run_id=first.run_id,
        outcome="interrupted",
        result=None,
        error=None,
    )
    resumed = await store.admit_chat_command(
        owner,
        prompt="resume",
        command_id="resume-command",
        parent_run_id=first.run_id,
        session_state=SESSION_PAYLOAD,
    )

    assert resumed.run_id != first.run_id
    assert resumed.parent_run_id == first.run_id
    run = await _load_run(store, "sqlite", resumed.run_id)
    assert run is not None
    assert run.parent_run_id == first.run_id


@pytest.mark.asyncio
async def test_terminal_uow_rolls_back_all_facts_on_failure_sqlite(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    admission = await _admit(store, owner, "sqlite-rollback-command")
    connection = store._connect()
    connection.execute(
        """
        CREATE TRIGGER fail_root_terminal
        BEFORE INSERT ON session_event_records
        WHEN NEW.event_kind = 'root_terminal'
        BEGIN
            SELECT RAISE(ABORT, 'injected terminal crash');
        END
        """
    )
    connection.close()

    with pytest.raises(Exception, match="injected terminal crash"):
        await store.settle_root_run(
            owner,
            run_id=admission.run_id,
            outcome="completed",
            result="done",
            error=None,
        )

    run = await _load_run(store, "sqlite", admission.run_id)
    assert run is not None
    assert run.status == "requested"
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "1"
    session = store.load_session(SESSION_ID)
    assert session is not None
    assert session["turn_in_progress"] is True


@pytest.mark.asyncio
async def test_terminal_uow_rolls_back_all_facts_on_failure_postgresql(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("pg", tmp_path)
    admission = await _admit(store, owner, "pg-rollback-command")
    connection = store._harness_pool.connection
    connection.fail_on_agent_run_write = True

    with pytest.raises(RuntimeError, match="injected terminal crash"):
        await store.settle_root_run(
            owner,
            run_id=admission.run_id,
            outcome="failed",
            result=None,
            error="provider failed",
        )

    run = await _load_run(store, "pg", admission.run_id)
    assert run is not None
    assert run.status == "requested"
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "1"
    assert connection.session_payloads[SESSION_ID]["turn_in_progress"] is True


@pytest.mark.asyncio
async def test_terminal_uow_rejects_stale_owner_before_mutation(tmp_path: Path) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    admission = await _admit(store, owner, "stale-owner-command")
    stale_owner = OwnerAuthority(owner.session_id, owner.owner_id, owner.epoch + 1)

    with pytest.raises(SessionOwnershipConflictError):
        await store.settle_root_run(
            stale_owner,
            run_id=admission.run_id,
            outcome="interrupted",
            result=None,
            error=None,
        )

    run = await _load_run(store, "sqlite", admission.run_id)
    assert run is not None
    assert run.status == "requested"
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "1"
    await store.settle_root_run(
        owner,
        run_id=admission.run_id,
        outcome="interrupted",
        result=None,
        error=None,
    )


@pytest.mark.asyncio
async def test_pm0022_owning_disconnect_rejects_stale_owner_before_mutation(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    admission = await _admit(store, owner, "stale-disconnect-command")
    stale_owner = OwnerAuthority(owner.session_id, owner.owner_id, owner.epoch + 1)

    class Manager:
        @staticmethod
        def can_settle_root_run_authoritatively() -> bool:
            return True

        async def settle_root_run(
            self, session_id: str, *, run_id: str, outcome: str
        ) -> None:
            assert session_id == SESSION_ID
            await store.settle_root_run(
                stale_owner,
                run_id=run_id,
                outcome=outcome,
                result=None,
                error=None,
            )

    with pytest.raises(SessionOwnershipConflictError):
        await prompt_routes._settle_stream_disconnect(
            Manager(),
            session_id=SESSION_ID,
            run_id=admission.run_id,
            owns_run=True,
        )

    run = await _load_run(store, "sqlite", admission.run_id)
    assert run is not None
    assert run.status == "requested"
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "1"
    assert store.load_session(SESSION_ID)["turn_in_progress"] is True


@pytest.mark.asyncio
async def test_cancel_vs_owning_disconnect_writes_one_terminal(tmp_path: Path) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    admission = await _admit(store, owner, "cancel-disconnect-command")
    barrier = asyncio.Event()

    async def settle(outcome: str) -> object:
        await barrier.wait()
        return await store.settle_root_run(
            owner,
            run_id=admission.run_id,
            outcome=outcome,
            result=None,
            error=None,
        )

    cancel = asyncio.create_task(settle("cancelled"))
    disconnect = asyncio.create_task(settle("interrupted"))
    barrier.set()
    results = await asyncio.gather(cancel, disconnect, return_exceptions=True)

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert (
        sum(isinstance(result, RootRunAlreadySettledError) for result in results) == 1
    )
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "2"


@pytest.mark.asyncio
async def test_owning_post_disconnect_interrupts_and_shield_settles_once() -> None:
    settle_started = asyncio.Event()
    allow_settle = asyncio.Event()

    class Manager:
        calls: list[tuple[str, str]] = []

        async def settle_root_run(
            self, session_id: str, *, run_id: str, outcome: str
        ) -> None:
            self.calls.append((run_id, outcome))
            settle_started.set()
            await allow_settle.wait()

    helper = getattr(prompt_routes, "_settle_stream_disconnect", None)
    assert callable(helper), "owning stream teardown helper is required"
    manager = Manager()
    teardown = asyncio.create_task(
        helper(
            manager,
            session_id="session-a",
            run_id="run-a",
            owns_run=True,
        )
    )
    await settle_started.wait()
    teardown.cancel()
    allow_settle.set()
    with pytest.raises(asyncio.CancelledError):
        await teardown
    assert manager.calls == [("run-a", "interrupted")]


@pytest.mark.asyncio
async def test_passive_get_disconnect_never_mutates_run() -> None:
    class Manager:
        calls: list[tuple[str, str]] = []

        async def settle_root_run(
            self, session_id: str, *, run_id: str, outcome: str
        ) -> None:
            self.calls.append((run_id, outcome))

    helper = getattr(prompt_routes, "_settle_stream_disconnect", None)
    assert callable(helper), "stream teardown policy helper is required"
    manager = Manager()
    await helper(
        manager,
        session_id="session-a",
        run_id="run-a",
        owns_run=False,
    )
    assert manager.calls == []


@pytest.mark.asyncio
async def test_pm0023_passive_get_disconnect_closes_stream_without_settlement() -> None:
    stream_started = asyncio.Event()
    never_finishes = asyncio.Event()
    closed = 0
    settlements: list[tuple[str, str]] = []

    class PassiveFollowStream:
        def __aiter__(self) -> PassiveFollowStream:
            return self

        async def __anext__(self) -> object:
            stream_started.set()
            await never_finishes.wait()
            raise StopAsyncIteration

        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

    wrapped = http_events._chat_stream_sse_frames(PassiveFollowStream())
    pending = asyncio.create_task(anext(wrapped))
    await stream_started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await wrapped.aclose()

    assert closed == 1
    assert settlements == []


@pytest.mark.asyncio
async def test_pm0023_owning_disconnect_cancel_duplicate_cleanup_settles_once(
    tmp_path: Path,
) -> None:
    store, owner = await _open_store("sqlite", tmp_path)
    admission = await _admit(store, owner, "pm0023-owning-race")
    barrier = asyncio.Event()

    class Manager:
        calls: list[tuple[str, str]] = []

        @staticmethod
        def can_settle_root_run_authoritatively() -> bool:
            return True

        async def settle_root_run(
            self, session_id: str, *, run_id: str, outcome: str
        ) -> object:
            assert session_id == SESSION_ID
            self.calls.append((run_id, outcome))
            await barrier.wait()
            return await store.settle_root_run(
                owner,
                run_id=run_id,
                outcome=outcome,
                result=None,
                error=None,
            )

    async def cancel() -> object:
        await barrier.wait()
        return await store.settle_root_run(
            owner,
            run_id=admission.run_id,
            outcome="cancelled",
            result=None,
            error=None,
        )

    manager = Manager()
    contenders = [
        asyncio.create_task(cancel()),
        asyncio.create_task(
            prompt_routes._settle_stream_disconnect(
                manager,
                session_id=SESSION_ID,
                run_id=admission.run_id,
                owns_run=True,
            )
        ),
        asyncio.create_task(
            prompt_routes._settle_stream_disconnect(
                manager,
                session_id=SESSION_ID,
                run_id=admission.run_id,
                owns_run=True,
            )
        ),
    ]
    barrier.set()
    results = await asyncio.gather(*contenders, return_exceptions=True)

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert (
        sum(isinstance(result, RootRunAlreadySettledError) for result in results) == 2
    )
    assert manager.calls == [
        (admission.run_id, "interrupted"),
        (admission.run_id, "interrupted"),
    ]
    assert (await store.load_session_fact_source(SESSION_ID)).session_seq == "2"
