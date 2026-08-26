from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

import coding_agent.server.http_server as http_server
from coding_agent.events.connected_chat import ChatEvent
from coding_agent.runs.resume import DEFAULT_RESUME_PROMPT
from coding_agent.server.http.events import StreamControl
from coding_agent.server.http.routes import prompts as prompt_routes
from coding_agent.server.http.routes import sessions as session_routes
from coding_agent.server.http.routes import sse as sse_routes
from coding_agent.server.schemas import PromptRequest, ResumeSessionRequest
from coding_agent.server.session_manager import Session, SessionManager
from coding_agent.server.stores.session_owner_store import SQLiteSessionOwnerStore
from coding_agent.stores.local import local_sqlite_storage_config
from coding_agent.stores.runtime_store import AuthoritativeUnitOfWork, EventRecord


@pytest.fixture
async def durable_manager(tmp_path) -> AsyncIterator[SessionManager]:
    original = http_server.session_manager
    owner_store = SQLiteSessionOwnerStore(tmp_path / "local.sqlite3")
    manager = SessionManager(
        storage_config=local_sqlite_storage_config(tmp_path),
        owner_store=owner_store,
        owner_id="r3-owner",
        fencing_token=1,
    )
    http_server.session_manager = manager
    try:
        yield manager
    finally:
        http_server.session_manager = original
        await manager.close()


async def _registered_session(
    manager: SessionManager, session_id: str = "session-r3"
) -> Session:
    now = datetime.now(UTC)
    session = Session(id=session_id, created_at=now, last_activity=now)
    session.tape_id = f"tape-{session_id}"
    manager._session_cache[session.id] = session
    await manager._acquire_owner_for_session(session.id)
    store = manager._authoritative_store()
    assert store is not None
    await store.save_session(
        manager._owner_authorities[session.id], session.to_store_data()
    )
    return session


async def _append_assistant_event(
    manager: SessionManager,
    session: Session,
    *,
    event_id: str,
    run_id: str,
    text: str,
) -> None:
    store = manager._authoritative_store()
    assert store is not None
    authority = manager._owner_authorities[session.id]
    await store.commit_authoritative_uow(
        authority,
        AuthoritativeUnitOfWork(
            event=EventRecord(
                event_id=event_id,
                session_id=session.id,
                event_kind="assistant_message",
                payload={"run_id": run_id, "text": text},
                created_at=datetime.now(UTC),
            ),
            session_state=session.to_store_data(),
        ),
    )


@pytest.mark.asyncio
async def test_real_manager_follow_replays_canonical_stable_event_ids(
    durable_manager: SessionManager,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    admission = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-r3"
    )
    await _append_assistant_event(
        manager,
        session,
        event_id="event-r3-assistant",
        run_id=admission.run_id,
        text="world",
    )

    stream = await manager.follow_chat_events(session.id, cursor=None)
    first = await anext(stream)
    second = await anext(stream)
    await stream.aclose()

    assert isinstance(first, ChatEvent)
    assert isinstance(second, ChatEvent)
    assert [first.source_event_id, second.source_event_id] == [
        "session-r3:chat-command:command-r3",
        "event-r3-assistant",
    ]
    assert [first.session_seq, second.session_seq] == ["1", "2"]


@pytest.mark.asyncio
async def test_passive_reconnect_from_safe_cursor_has_no_duplicate(
    durable_manager: SessionManager,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    admission = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-reconnect"
    )

    initial = await manager.follow_chat_events(session.id, cursor=None)
    prompt_event = await anext(initial)
    await initial.aclose()
    assert isinstance(prompt_event, ChatEvent)

    await _append_assistant_event(
        manager,
        session,
        event_id="event-after-reconnect",
        run_id=admission.run_id,
        text="continued",
    )
    cursor = await manager.chat_follow_cursor(
        session.id, after_seq=prompt_event.session_seq
    )
    reconnected = await manager.follow_chat_events(session.id, cursor=cursor)
    event = await anext(reconnected)
    await reconnected.aclose()

    assert isinstance(event, ChatEvent)
    assert event.source_event_id == "event-after-reconnect"
    assert event.session_seq == "2"


@pytest.mark.asyncio
async def test_real_follow_cursor_error_is_checked_before_sse_headers(
    durable_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.core.config import settings

    monkeypatch.setattr(settings, "http_api_key", None)
    monkeypatch.setattr(
        session_routes, "_auth_context_can_access_session", lambda auth, session: True
    )
    manager = durable_manager
    http_server.session_manager = manager
    session = await _registered_session(manager)
    store = manager._authoritative_store()
    assert store is not None
    await store.snapshot_chat_events(session.id, None, 1)

    response = await session_routes.follow_chat_events(
        Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session.id}/chat-events/follow",
                "headers": [],
            }
        ),
        session.id,
        cursor="bad",
        x_api_key=None,
        authorization=None,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    assert b'"code":"cursor_malformed"' in response.body


@pytest.mark.asyncio
async def test_owning_stream_begins_at_its_admission_boundary(
    durable_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    prior = await manager.admit_chat_command(
        session.id, prompt="old", command_id="command-old"
    )
    await manager.settle_root_run(session.id, run_id=prior.run_id, outcome="completed")
    admission = await manager.admit_chat_command(
        session.id, prompt="new", command_id="command-new"
    )

    async def blocked_run_agent(
        session_id: str,
        prompt: str,
        *,
        run_id_override: str | None = None,
        resume_context: object | None = None,
    ) -> None:
        del session_id, prompt, run_id_override, resume_context
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "run_agent", blocked_run_agent)
    stream: AsyncIterator[ChatEvent | StreamControl] = manager.stream_chat_command(
        session.id, admission=admission
    )
    first = await anext(stream)
    await stream.aclose()

    assert isinstance(first, ChatEvent)
    assert first.source_event_id == "session-r3:chat-command:command-new"
    assert first.run_id == admission.run_id


@pytest.mark.asyncio
async def test_owning_stream_emits_terminal_and_exits_after_completion(
    durable_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    admission = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-complete"
    )

    async def completing_run_agent(
        session_id: str,
        prompt: str,
        *,
        run_id_override: str | None = None,
        resume_context: object | None = None,
    ) -> None:
        del prompt, resume_context
        assert run_id_override is not None
        await manager.settle_root_run(
            session_id, run_id=run_id_override, outcome="completed"
        )

    monkeypatch.setattr(manager, "run_agent", completing_run_agent)
    stream: AsyncIterator[ChatEvent | StreamControl] = manager.stream_chat_command(
        session.id, admission=admission
    )
    prompt = await asyncio.wait_for(anext(stream), timeout=1)
    terminal = await asyncio.wait_for(anext(stream), timeout=1)

    assert isinstance(prompt, ChatEvent)
    assert isinstance(terminal, ChatEvent)
    assert terminal.kind == "root_terminal"
    assert terminal.payload["outcome"] == "completed"
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)


@pytest.mark.asyncio
async def test_connected_resume_without_parent_writes_no_durable_state(
    durable_manager: SessionManager,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    store = manager._authoritative_store()
    assert store is not None
    before = await store.snapshot_chat_events(session.id, None, 100)
    before_runs = await manager._require_runtime_store().list_agent_runs(session.id)

    response = await prompt_routes.resume_session(
        Request(
            {
                "type": "http",
                "method": "POST",
                "path": f"/sessions/{session.id}/resume",
                "headers": [],
            }
        ),
        session.id,
        body=ResumeSessionRequest(
            command_id="command-missing-parent",
            prompt=None,
        ),
        event_format="wire",
        api_key=None,
        authorization=None,
    )

    after = await store.snapshot_chat_events(session.id, None, 100)
    after_runs = await manager._require_runtime_store().list_agent_runs(session.id)
    receipt = await store.load_receipt_slot(
        session.id, "chat-command:command-missing-parent"
    )
    assert response.status_code == 422
    assert json.loads(response.body)["error"]["code"] == "parent_run_id_required"
    assert after.events == before.events
    assert after_runs == before_runs
    assert receipt is None


@pytest.mark.asyncio
async def test_owning_resume_stream_uses_admitted_run_and_parent_context(
    durable_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    parent = await manager.admit_chat_command(
        session.id, prompt="old", command_id="command-parent"
    )
    await manager.settle_root_run(session.id, run_id=parent.run_id, outcome="failed")
    admission = await manager.admit_chat_command(
        session.id,
        prompt=DEFAULT_RESUME_PROMPT,
        command_id="command-resume",
        parent_run_id=parent.run_id,
    )

    async def fail_run_agent(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("admitted resume must not use the ordinary run path")

    async def completing_resume(
        session_id: str,
        *,
        prompt: str | None = None,
        resume_reason: str = "user_resume",
        run_id_override: str | None = None,
        previous_run_id: str | None = None,
    ) -> object:
        assert prompt == DEFAULT_RESUME_PROMPT
        assert resume_reason == "user_resume"
        assert run_id_override == admission.run_id
        assert previous_run_id == parent.run_id
        await manager.settle_root_run(
            session_id, run_id=admission.run_id, outcome="completed"
        )
        return object()

    monkeypatch.setattr(manager, "run_agent", fail_run_agent)
    monkeypatch.setattr(manager, "resume_session", completing_resume)
    stream: AsyncIterator[ChatEvent | StreamControl] = manager.stream_chat_command(
        session.id, admission=admission
    )
    prompt = await asyncio.wait_for(anext(stream), timeout=1)
    terminal = await asyncio.wait_for(anext(stream), timeout=1)

    assert isinstance(prompt, ChatEvent)
    assert prompt.run_id == admission.run_id
    assert isinstance(terminal, ChatEvent)
    assert terminal.kind == "root_terminal"
    assert terminal.run_id == admission.run_id
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)


@pytest.mark.asyncio
async def test_owning_stream_disconnect_shield_settles_interrupted(
    durable_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    admission = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-disconnect"
    )
    started = asyncio.Event()

    async def blocked_run_agent(
        session_id: str,
        prompt: str,
        *,
        run_id_override: str | None = None,
        resume_context: object | None = None,
    ) -> None:
        del session_id, prompt, run_id_override, resume_context
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "run_agent", blocked_run_agent)
    stream: AsyncIterator[ChatEvent | StreamControl] = manager.stream_chat_command(
        session.id, admission=admission
    )
    first = await anext(stream)
    assert isinstance(first, ChatEvent)
    await started.wait()
    await stream.aclose()

    store = manager._authoritative_store()
    assert store is not None
    run = await manager._require_runtime_store().load_agent_run(admission.run_id)
    assert run is not None
    assert run.status == "interrupted"
    snapshot = await store.snapshot_chat_events(session.id, None, 100)
    terminals = [event for event in snapshot.events if event.kind == "root_terminal"]
    assert len(terminals) == 1
    assert terminals[0].payload["outcome"] == "interrupted"


@pytest.mark.asyncio
async def test_active_duplicate_command_observes_without_second_executor(
    durable_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    owner = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-active-duplicate"
    )
    duplicate = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-active-duplicate"
    )
    release = asyncio.Event()
    started = asyncio.Event()
    launches = 0

    async def completing_run_agent(
        session_id: str,
        prompt: str,
        *,
        run_id_override: str | None = None,
        resume_context: object | None = None,
    ) -> None:
        nonlocal launches
        del prompt, resume_context
        launches += 1
        assert run_id_override == owner.run_id
        started.set()
        await release.wait()
        await manager.settle_root_run(
            session_id, run_id=owner.run_id, outcome="completed"
        )

    monkeypatch.setattr(manager, "run_agent", completing_run_agent)
    owner_stream = manager.stream_chat_command(session.id, admission=owner)
    duplicate_stream = manager.stream_chat_command(session.id, admission=duplicate)

    owner_admission = await asyncio.wait_for(anext(owner_stream), timeout=1)
    await asyncio.wait_for(started.wait(), timeout=1)
    duplicate_admission = await asyncio.wait_for(anext(duplicate_stream), timeout=1)
    assert owner_admission.source_event_id == duplicate_admission.source_event_id
    assert launches == 1

    release.set()
    owner_terminal = await asyncio.wait_for(anext(owner_stream), timeout=1)
    duplicate_terminal = await asyncio.wait_for(anext(duplicate_stream), timeout=1)
    assert owner_terminal.source_event_id == duplicate_terminal.source_event_id
    assert owner_terminal.kind == duplicate_terminal.kind == "root_terminal"
    with pytest.raises(StopAsyncIteration):
        await anext(owner_stream)
    with pytest.raises(StopAsyncIteration):
        await anext(duplicate_stream)

    store = manager._authoritative_store()
    assert store is not None
    snapshot = await store.snapshot_chat_events(session.id, None, 100)
    assert launches == 1
    assert len([event for event in snapshot.events if event.kind == "user_prompt"]) == 1
    assert (
        len([event for event in snapshot.events if event.kind == "root_terminal"]) == 1
    )


@pytest.mark.asyncio
async def test_enabled_auth_foreign_mutations_are_hidden_without_durable_writes(
    durable_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from coding_agent.core.config import settings

    manager = durable_manager
    session = await _registered_session(manager, "session-owner-b")
    session.origin = {"owner_label": "owner:b"}
    await manager._persist_session_async(session)
    monkeypatch.setattr(settings, "http_api_key", "owner-a-token")
    store = manager._authoritative_store()
    assert store is not None
    before_events = await store.snapshot_chat_events(session.id, None, 100)
    before_runs = await manager._require_runtime_store().list_agent_runs(session.id)
    expected = {
        "error": {
            "code": "session_not_found",
            "message": "Session not found",
            "retryable": False,
        }
    }

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/sessions/{session.id}/prompt",
            "headers": [],
        }
    )
    prompt = await prompt_routes.send_prompt(
        request,
        session.id,
        body=PromptRequest(prompt="foreign", command_id="foreign-prompt"),
        prompt=None,
        event_format="wire",
        api_key=None,
        authorization="Bearer owner-a-token",
    )
    resume = await prompt_routes.resume_session(
        request,
        session.id,
        body=ResumeSessionRequest(
            prompt=None,
            command_id="foreign-resume",
            parent_run_id="foreign-parent",
        ),
        event_format="wire",
        api_key=None,
        authorization="Bearer owner-a-token",
    )
    cancel = await sse_routes.cancel_session_turn(
        request,
        session.id,
        x_api_key=None,
        authorization="Bearer owner-a-token",
    )

    for response in (prompt, resume, cancel):
        assert isinstance(response, JSONResponse)
        assert response.status_code == 404
        assert json.loads(response.body) == expected
    after_events = await store.snapshot_chat_events(session.id, None, 100)
    after_runs = await manager._require_runtime_store().list_agent_runs(session.id)
    assert after_events.events == before_events.events
    assert after_runs == before_runs
    assert (
        await store.load_receipt_slot(session.id, "chat-command:foreign-prompt") is None
    )
    assert (
        await store.load_receipt_slot(session.id, "chat-command:foreign-resume") is None
    )


@pytest.mark.asyncio
async def test_settled_duplicate_replays_without_executor_or_progress_mutation(
    durable_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = durable_manager
    session = await _registered_session(manager)
    admitted = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-settled-duplicate"
    )
    await manager.settle_root_run(
        session.id, run_id=admitted.run_id, outcome="completed"
    )
    duplicate = await manager.admit_chat_command(
        session.id, prompt="hello", command_id="command-settled-duplicate"
    )

    async def fail_run_agent(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("settled duplicate must not launch an executor")

    monkeypatch.setattr(manager, "run_agent", fail_run_agent)
    stream = manager.stream_chat_command(session.id, admission=duplicate)
    replayed_admission = await asyncio.wait_for(anext(stream), timeout=1)
    replayed_terminal = await asyncio.wait_for(anext(stream), timeout=1)

    assert replayed_admission.kind == "user_prompt"
    assert replayed_terminal.kind == "root_terminal"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert (await manager.get_session_async(session.id)).turn_in_progress is False

    store = manager._authoritative_store()
    assert store is not None
    snapshot = await store.snapshot_chat_events(session.id, None, 100)
    assert len([event for event in snapshot.events if event.kind == "user_prompt"]) == 1
    assert (
        len([event for event in snapshot.events if event.kind == "root_terminal"]) == 1
    )
