from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

from coding_agent.__main__ import main
from coding_agent.runs import (
    REMOTE_LOOP_OWNERSHIP_RETIRED,
    RemoteLoopOwnershipRetired,
    run_target_from_dict,
    run_target_from_legacy_session_payload,
)
from coding_agent.runs.recovery import (
    REMOTE_LOOP_RETIRED_ERROR,
    REMOTE_LOOP_RETIRED_RECOVERY_REASON,
    RuntimeRunRecoveryService,
)
from coding_agent.runs.target import (
    ExternalWorkerExecutorRef,
    ExternalWorkerWorkspaceRef,
    IsolationPolicy,
    LocalAttachedExecutorRef,
    RunTarget,
)
from coding_agent.stores.runtime_store import AgentRunRecord
from coding_agent.remote.worker import (
    AttachedExecutorConsumer,
    run_attached_executor_loop,
    run_local_attached_executor_once,
)
import coding_agent.remote.worker as remote_worker
import coding_agent.server.http_server as http_server
from coding_agent.server.http_server import app


_CODING_AGENT_ROOT = Path(__file__).resolve().parents[2]


def _attached_run(
    *,
    run_id: str,
    session_id: str,
    status: str,
    executor_ref_kind: str,
    started_at: datetime,
    ended_at: datetime | None = None,
    extra_metadata: dict[str, object] | None = None,
) -> AgentRunRecord:
    metadata: dict[str, object] = {"executor_ref_kind": executor_ref_kind}
    if extra_metadata:
        metadata.update(extra_metadata)
    return AgentRunRecord(
        run_id=run_id,
        session_id=session_id,
        tape_id="tape-1",
        parent_run_id=None,
        agent_id=None,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        metadata=metadata,
        result={},
        error=None,
    )


@pytest.fixture
async def client(isolated_http_session_manager):
    del isolated_http_session_manager
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_worker_and_executor_claim_heartbeat_events_approval_complete_return_410(
    client: AsyncClient,
) -> None:
    bodies = {
        "claim": {
            "worker_id": "worker-1",
            "executor_id": "executor-1",
            "executor_kind": "local_cli",
        },
        "heartbeat": {
            "worker_id": "worker-1",
            "executor_id": "executor-1",
            "claim_token": "token",
        },
        "events": {
            "worker_id": "worker-1",
            "executor_id": "executor-1",
            "claim_token": "token",
            "events": [
                {
                    "event_id": "event-1",
                    "event": "StreamDelta",
                    "data": {"content": "hi"},
                }
            ],
        },
        "approval": {
            "worker_id": "worker-1",
            "executor_id": "executor-1",
            "claim_token": "token",
            "request_id": "approval-1",
            "tool_name": "shell_execute",
            "arguments": {"command": "pwd"},
        },
        "complete": {
            "worker_id": "worker-1",
            "executor_id": "executor-1",
            "claim_token": "token",
            "status": "completed",
            "result": {},
        },
    }
    paths = (
        ("/worker/runs/claim", bodies["claim"]),
        ("/executor/runs/claim", bodies["claim"]),
        ("/worker/runs/run-1/heartbeat", bodies["heartbeat"]),
        ("/executor/runs/run-1/heartbeat", bodies["heartbeat"]),
        ("/worker/runs/run-1/events", bodies["events"]),
        ("/executor/runs/run-1/events", bodies["events"]),
        ("/worker/runs/run-1/approval", bodies["approval"]),
        ("/executor/runs/run-1/approval", bodies["approval"]),
        ("/worker/runs/run-1/complete", bodies["complete"]),
        ("/executor/runs/run-1/complete", bodies["complete"]),
    )
    for path, payload in paths:
        response = await client.post(path, json=payload)
        assert response.status_code == 410, path
        assert response.status_code != 404
        assert response.json()["detail"] == REMOTE_LOOP_OWNERSHIP_RETIRED


@pytest.mark.asyncio
async def test_attached_executor_consumer_loop_entrypoints_are_gone() -> None:
    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        AttachedExecutorConsumer()
    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        await run_local_attached_executor_once(
            base_url="http://example",
            headers={},
            repo_path=Path("."),
            goal="x",
            approval_policy="yolo",
            provider_name=None,
            model_name=None,
            base_url_override=None,
            max_steps=1,
            worker_id="worker-1",
        )
    with pytest.raises(RemoteLoopOwnershipRetired, match="in-process"):
        await run_attached_executor_loop(
            base_url="http://example",
            headers={},
            repo_path=Path("."),
            worker_id="worker-1",
            once=True,
            poll_interval_seconds=0.1,
        )
    for name in (
        "_claim_run",
        "_execute_claimed_run",
        "_create_attached_executor_session",
        "_heartbeat_until_complete",
    ):
        assert not hasattr(remote_worker, name)
        assert name not in dict(inspect.getmembers(remote_worker))


def test_cli_remote_local_run_and_worker_loop_refuse_remote_loop_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "remotes.json"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    monkeypatch.setenv("CODING_AGENT_REMOTES_FILE", str(config_path))
    runner = CliRunner()
    runner.invoke(
        main,
        ["remote", "add", "dev", "http://agent.example", "--token", "secret-token"],
        catch_exceptions=False,
    )
    commands = (
        [
            "remote",
            "local-run",
            "dev",
            "--repo",
            str(repo_path),
            "--goal",
            "do local work",
        ],
        [
            "remote",
            "worker",
            "dev",
            "--repo",
            str(repo_path),
            "--worker-id",
            "worker-test",
        ],
        [
            "remote",
            "executor",
            "dev",
            "--repo",
            str(repo_path),
            "--executor-id",
            "executor-test",
        ],
    )
    for args in commands:
        result = runner.invoke(main, args)
        assert result.exit_code != 0, args
        assert REMOTE_LOOP_OWNERSHIP_RETIRED in result.output


@pytest.mark.asyncio
async def test_nonterminal_attached_runs_interrupt_on_recovery_and_keep_parsers() -> (
    None
):
    from tests.coding_agent.test_runtime_run_recovery import (
        RecordingRuntimeRunRecoveryStore,
        _session_ids,
    )

    store = RecordingRuntimeRunRecoveryStore()
    recovered_at = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    started_at = datetime(2026, 8, 19, 11, 0, tzinfo=UTC)
    store.runs.extend(
        [
            _attached_run(
                run_id="run-requested",
                session_id="session-1",
                status="requested",
                executor_ref_kind="external_worker",
                started_at=started_at,
            ),
            _attached_run(
                run_id="run-claimed",
                session_id="session-1",
                status="claimed",
                executor_ref_kind="local_attached",
                started_at=started_at,
                extra_metadata={
                    "lease_expires_at": (
                        recovered_at + timedelta(seconds=30)
                    ).isoformat(),
                },
            ),
            _attached_run(
                run_id="run-completed",
                session_id="session-1",
                status="completed",
                executor_ref_kind="external_worker",
                started_at=started_at,
                ended_at=recovered_at,
            ),
            _attached_run(
                run_id="run-failed",
                session_id="session-1",
                status="failed",
                executor_ref_kind="local_attached",
                started_at=started_at,
                ended_at=recovered_at,
            ),
        ]
    )
    service = RuntimeRunRecoveryService(
        store=store,
        list_session_ids=lambda: _session_ids("session-1"),
    )
    recovered_count = await service.recover_stale_runtime_runs(
        recovered_at=recovered_at
    )

    assert recovered_count == 2
    updated_ids = {update["run_id"] for update in store.updated}
    assert updated_ids == {"run-requested", "run-claimed"}
    for update in store.updated:
        assert update["status"] == "interrupted"
        assert update["ended_at"] == recovered_at
        assert update["error"] == REMOTE_LOOP_RETIRED_ERROR
        metadata = update["metadata"]
        assert isinstance(metadata, dict)
        assert metadata["recovery_reason"] == REMOTE_LOOP_RETIRED_RECOVERY_REASON
        assert metadata["reclaimable"] is False
    remaining = {run.run_id: run for run in store.runs}
    assert remaining["run-completed"].status == "completed"
    assert remaining["run-failed"].status == "failed"

    external = run_target_from_legacy_session_payload(
        {
            "kind": "external_worker",
            "executor_kind": "local_cli",
            "worker_pool": "pool-a",
        }
    )
    attached = run_target_from_legacy_session_payload(
        {
            "kind": "local_attached",
            "executor_kind": "local_cli",
            "worker_pool": "attached",
        }
    )
    assert isinstance(external.executor, ExternalWorkerExecutorRef)
    assert isinstance(attached.executor, LocalAttachedExecutorRef)
    assert run_target_from_dict(external.to_dict()) == external
    assert run_target_from_dict(attached.to_dict()) == attached


def _attached_run_target_payload(*, kind: str) -> dict[str, object]:
    executor = (
        LocalAttachedExecutorRef(executor_kind="local_cli")
        if kind == "local_attached"
        else ExternalWorkerExecutorRef(executor_kind="local_cli")
    )
    return RunTarget(
        workspace=ExternalWorkerWorkspaceRef(
            ref={"kind": "local_path", "display_path": "/tmp/repo"}
        ),
        executor=executor,
        isolation=IsolationPolicy(kind="external_worker_policy"),
    ).to_dict()


@pytest.mark.asyncio
async def test_prompt_on_external_worker_or_local_attached_session_does_not_create_claimable_loop_run(
    client: AsyncClient,
) -> None:
    for kind, prompt in (
        ("external_worker", "run external"),
        ("local_attached", "run attached"),
    ):
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(kind=kind),
            },
        )
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]
        prompt_resp = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": prompt},
        )
        assert prompt_resp.status_code == 410
        assert prompt_resp.json()["detail"] == REMOTE_LOOP_OWNERSHIP_RETIRED
        with pytest.raises(RemoteLoopOwnershipRetired):
            await http_server.session_manager.run_agent(session_id, prompt)
        with pytest.raises(RemoteLoopOwnershipRetired):
            await http_server.session_manager.request_attached_executor_run(
                session_id,
                prompt,
            )
        claim_resp = await client.post(
            "/executor/runs/claim",
            json={
                "executor_id": "executor-1",
                "executor_kind": "local_cli",
                "session_id": session_id,
            },
        )
        assert claim_resp.status_code == 410


def test_adr_0051_0052_0053_stay_accepted_while_0076_is_proposed() -> None:
    adr_dir = _CODING_AGENT_ROOT / "docs" / "adr"
    for name in (
        "0051-external-worker-execution-control-plane.md",
        "0052-external-worker-usable-control-plane.md",
        "0053-advanced-external-worker-control-plane-foundations.md",
    ):
        text = (adr_dir / name).read_text()
        assert "**Status**: Accepted" in text
        assert "**Status**: Superseded" not in text
    harness = (adr_dir / "0076-harness-control-plane.md").read_text()
    assert "**Status**: Proposed" in harness
    assert "**Status**: Accepted" not in harness
    assert "**Status**: Superseded" not in harness
