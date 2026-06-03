from __future__ import annotations

from typing import Any

from coding_agent.runs import RuntimeControlServices


async def _list_session_ids() -> list[str]:
    return ["session-1"]


async def _recoverable(session_id: str) -> bool:
    return session_id == "session-1"


def _metadata_for_session(
    session: object, *, run_id: str | None = None
) -> dict[str, Any]:
    del session, run_id
    return {"source": "test"}


def test_runtime_control_services_use_latest_runtime_store() -> None:
    store_a = object()
    store_b = object()
    current_store: object | None = store_a
    services = RuntimeControlServices(
        store=lambda: current_store,
        metadata_for_session=_metadata_for_session,
        list_session_ids=_list_session_ids,
        session_is_recoverable=_recoverable,
        owner_id=lambda: "owner-1",
        active_resume_blocking_statuses=frozenset({"running"}),
    )

    assert services.queries().store is store_a
    assert services.cancel().store is store_a

    current_store = store_b

    assert services.queries().store is store_b
    assert services.cancel().store is store_b
    assert services.attached_executor().store is store_b
    assert services.run_persistence().run_store is store_b
    assert services.run_persistence().checkpoint_store is store_b


def test_runtime_control_services_wire_metadata_and_recovery_policy() -> None:
    store = object()
    current_owner_id: str | None = "owner-1"
    services = RuntimeControlServices(
        store=lambda: store,
        metadata_for_session=_metadata_for_session,
        list_session_ids=_list_session_ids,
        session_is_recoverable=_recoverable,
        owner_id=lambda: current_owner_id,
        active_resume_blocking_statuses=frozenset({"claimed", "running"}),
    )

    assert services.queries().active_resume_blocking_statuses == frozenset(
        {"claimed", "running"}
    )
    assert services.attached_executor().metadata_for_session is _metadata_for_session
    assert services.run_persistence().metadata_for_session is _metadata_for_session
    assert services.run_recovery().list_session_ids is _list_session_ids
    assert services.run_recovery().session_is_recoverable is _recoverable
    assert services.run_recovery().owner_id == "owner-1"

    current_owner_id = "owner-2"

    assert services.run_recovery().owner_id == "owner-2"
