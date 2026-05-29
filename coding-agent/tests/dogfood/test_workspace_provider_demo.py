from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient

from coding_agent.environment import CloudCommandResult, WorkspaceProviderCapabilities
from coding_agent.observability import prometheus_metrics_text, reset_prometheus_metrics
from coding_agent.plugins.core_tools import CoreToolsPlugin
from coding_agent.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.ui import http_server
from coding_agent.server.binding_resolver import DefaultBindingResolver
from coding_agent.server.execution_binding import CloudWorkspaceBinding
from coding_agent.server.http_server import app
from coding_agent.server.session_manager import MockProvider, SessionManager
from coding_agent.server.stores.session_store import InMemorySessionStore
from coding_agent.server.stores.workspace_store import WorkspaceRecord


FORBIDDEN_RENDERED_TEXT: Sequence[str] = (
    "workspace provider dogfood task",
    "I'll help you with that request",
    "SECRET_PROMPT_MESSAGE_CONTENT_RESULT_TEXT",
    "command_output",
    "stdout",
    "stderr",
    "DOGFOOD_WORKSPACE_SECRET_SENTINEL",
)


class InMemoryRuntimeStore:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.events_by_run: dict[str, list[RuntimeEventRecord]] = {}
        self.snapshots: dict[str, RunMessageSnapshotRecord] = {}
        self.interactions: dict[str, AgentInteractionRecord] = {}

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.runs[record.run_id] = record
        return record

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [run for run in self.runs.values() if run.session_id == session_id]

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        sequence = len(self.events_by_run.get(record.run_id, [])) + 1
        event = RuntimeEventRecord(
            event_id=record.event_id,
            run_id=record.run_id,
            event_kind=record.event_kind,
            payload=record.payload,
            created_at=record.created_at,
            sequence=record.sequence or sequence,
        )
        self.events_by_run.setdefault(event.run_id, []).append(event)
        return event

    async def load_runtime_event(self, event_id: str) -> RuntimeEventRecord | None:
        for events in self.events_by_run.values():
            for event in events:
                if event.event_id == event_id:
                    return event
        return None

    async def replay_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RuntimeEventRecord]:
        events = self.events_by_run.get(run_id, [])
        return [
            event
            for event in events
            if event.sequence is not None and event.sequence > after_sequence
        ][:limit]

    async def save_message_snapshot(
        self,
        record: RunMessageSnapshotRecord,
    ) -> RunMessageSnapshotRecord:
        self.snapshots[record.snapshot_id] = record
        return record

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        return self.snapshots.get(snapshot_id)

    async def create_agent_interaction(
        self,
        record: AgentInteractionRecord,
    ) -> AgentInteractionRecord:
        self.interactions[record.interaction_id] = record
        return record

    async def list_agent_interactions(
        self,
        run_id: str,
    ) -> list[AgentInteractionRecord]:
        return [
            interaction
            for interaction in self.interactions.values()
            if interaction.run_id == run_id
        ]

    async def resolve_agent_interaction(
        self,
        interaction_id: str,
        *,
        status: str,
        response_payload: dict[str, object],
        resolved_at: datetime,
    ) -> AgentInteractionRecord:
        record = self.interactions[interaction_id]
        resolved = AgentInteractionRecord(
            interaction_id=record.interaction_id,
            run_id=record.run_id,
            interaction_kind=record.interaction_kind,
            status=status,
            request_payload=record.request_payload,
            response_payload=cast(dict, response_payload),
            metadata=record.metadata,
            created_at=record.created_at,
            resolved_at=resolved_at,
        )
        self.interactions[interaction_id] = resolved
        return resolved

    async def update_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None,
        metadata: dict[str, object],
        result: dict[str, object],
        error: str | None,
    ) -> AgentRunRecord:
        current = self.runs[run_id]
        updated = AgentRunRecord(
            run_id=current.run_id,
            session_id=current.session_id,
            tape_id=current.tape_id,
            parent_run_id=current.parent_run_id,
            agent_id=current.agent_id,
            status=status,
            started_at=current.started_at,
            ended_at=ended_at,
            metadata=cast(dict, metadata),
            result=cast(dict, result),
            error=error,
        )
        self.runs[run_id] = updated
        return updated


class InMemoryWorkspaceStore:
    def __init__(self) -> None:
        self.records: dict[str, WorkspaceRecord] = {}

    async def save(self, record: WorkspaceRecord) -> None:
        self.records[record.workspace_record_id] = record

    async def list(self) -> list[WorkspaceRecord]:
        return list(self.records.values())

    async def load_by_workspace_id(self, workspace_id: str) -> WorkspaceRecord | None:
        for record in self.records.values():
            if record.workspace_id == workspace_id:
                return record
        return None

    async def load_for_session_workspace(
        self,
        session_id: str,
        workspace_id: str,
    ) -> WorkspaceRecord | None:
        for record in self.records.values():
            if record.session_id == session_id and record.workspace_id == workspace_id:
                return record
        return None

    async def update_status(
        self,
        workspace_record_id: str,
        *,
        status: str,
        cleanup_error: str | None = None,
    ) -> WorkspaceRecord | None:
        record = self.records.get(workspace_record_id)
        if record is None:
            return None
        updated = WorkspaceRecord(
            **{
                **record.__dict__,
                "status": status,
                "cleanup_error": cleanup_error,
                "updated_at": datetime.now(UTC),
            }
        )
        self.records[workspace_record_id] = updated
        return updated

    async def update_retention(
        self,
        workspace_record_id: str,
        *,
        retention_policy: str,
        expires_at: datetime | None,
        status: str,
    ) -> WorkspaceRecord | None:
        record = self.records.get(workspace_record_id)
        if record is None:
            return None
        updated = WorkspaceRecord(
            **{
                **record.__dict__,
                "retention_policy": retention_policy,
                "expires_at": expires_at,
                "status": status,
                "updated_at": datetime.now(UTC),
            }
        )
        self.records[workspace_record_id] = updated
        return updated

    async def update_result_refs(
        self,
        workspace_record_id: str,
        *,
        result_refs: dict[str, object],
    ) -> WorkspaceRecord | None:
        record = self.records.get(workspace_record_id)
        if record is None:
            return None
        updated = WorkspaceRecord(
            **{
                **record.__dict__,
                "result_refs": cast(dict, result_refs),
                "updated_at": datetime.now(UTC),
            }
        )
        self.records[workspace_record_id] = updated
        return updated


@dataclass
class RecordingCloudClient:
    workspace_url: str = "https://workspace.example.test/ws-dogfood"
    workspace_id: str = "ws-dogfood"
    default_cwd: str = "/workspace"
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def read_file(self, path: str) -> str:
        self.calls.append(("file_read", {"path": path}))
        return "sanitized fixture file"

    def write_file(self, path: str, content: str) -> None:
        self.calls.append(("file_write", {"path": path, "content_size": len(content)}))

    def replace_file(self, path: str, old: str, new: str) -> None:
        self.calls.append(
            ("file_replace", {"path": path, "old_size": len(old), "new_size": len(new)})
        )

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        self.calls.append(("glob_files", {"pattern": pattern, "directory": directory}))
        return ["src/coding_agent/__init__.py"]

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        self.calls.append(
            (
                "grep_search",
                {"pattern": pattern, "directory": directory, "include": include},
            )
        )
        return ["src/coding_agent/__init__.py:1:sanitized"]

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        self.calls.append(("file_patch", {"path": path, "patch_size": len(patch)}))
        return {"success": True, "changed": True}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        self.calls.append(
            (
                "bash_run",
                {
                    "command_label": command.split()[0],
                    "cwd": cwd,
                    "env_keys": tuple(sorted((env or {}).keys())),
                    "timeout": timeout,
                },
            )
        )
        return CloudCommandResult(stdout="sanitized", stderr="", exit_code=0)


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    reset_prometheus_metrics()


@pytest.mark.asyncio
async def test_workspace_provider_dogfood_demo_path_records_sanitized_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_store = InMemoryRuntimeStore()
    workspace_store = InMemoryWorkspaceStore()
    client = RecordingCloudClient()
    binding = CloudWorkspaceBinding(
        workspace_url=client.workspace_url,
        workspace_id=client.workspace_id,
        workspace_provider="docker",
        provider_instance_id="dogfood-local",
        runtime_profile="dogfood-fixture",
    )
    manager = SessionManager(
        store=InMemorySessionStore(),
        binding_resolver=DefaultBindingResolver(
            cloud_client_factory=lambda resolved: client
        ),
        runtime_store=runtime_store,
        workspace_metadata_store=workspace_store,
    )
    monkeypatch.setattr(http_server, "session_manager", manager)
    monkeypatch.setattr(
        http_server,
        "_load_remote_retention_config",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(
        http_server,
        "_load_cloud_workspace_config",
        lambda: {
            "provider": "docker",
            "provider_instance_id": "dogfood-local",
            "workspace_root": "/workspaces",
            "workspace_host_label": "dogfood-local",
        },
    )
    monkeypatch.setattr(
        http_server,
        "_load_observability_config",
        lambda: {
            "enabled": True,
            "metrics": {
                "enabled": True,
                "endpoint_enabled": True,
                "backend": "prometheus",
            },
        },
    )
    monkeypatch.setattr(
        http_server,
        "workspace_provider_capabilities_from_config",
        lambda config: WorkspaceProviderCapabilities(
            provider=str(config["provider"]),
            available=True,
            reason="ready",
            supports_provision=True,
            supports_archive=True,
            supports_diff=True,
            supports_patch=True,
            supports_publish=False,
        ),
    )

    session_id = await manager.create_session(
        repo_path=Path.cwd(),
        origin={
            "channel": "dogfood",
            "binding_kind": "cloud",
            "workspace_source_kind": "git",
            "workspace_provider": "docker",
            "provider_instance_id": "dogfood-local",
            "workspace_root_ref": "/workspaces",
            "workspace_host_label": "dogfood-local",
            "owner_label": "owner:dogfood",
            "secret_marker": "DOGFOOD_WORKSPACE_SECRET_SENTINEL",
        },
        provider=MockProvider(),
        provider_name="mock",
        model_name="mock",
        max_steps=1,
        execution_binding=binding,
    )
    await manager.run_agent(session_id, "workspace provider dogfood task")

    session = manager.get_session(session_id)
    run_id = session.current_turn_id
    assert run_id
    run = await manager.load_runtime_run(run_id)
    assert run.session_id == session_id
    assert run.status == "completed"
    assert run.result["stop_reason"] == "no_tool_calls"

    environment = manager._resolve_environment(session)
    plugin = CoreToolsPlugin(environment=environment)
    assert "sanitized fixture file" == plugin.execute_tool(
        name="file_read",
        arguments={"path": "README.md"},
    )
    command_result = plugin.execute_tool(
        name="bash_run",
        arguments={"command": "pytest", "timeout": 5},
    )
    assert command_result == "sanitized"
    assert [name for name, _ in client.calls] == ["file_read", "bash_run"]
    assert client.calls[-1] == (
        "bash_run",
        {
            "command_label": "pytest",
            "cwd": "/workspace",
            "env_keys": (),
            "timeout": 5,
        },
    )

    workspace_record = await workspace_store.load_by_workspace_id(client.workspace_id)
    assert workspace_record is not None
    assert workspace_record.session_id == session_id
    assert workspace_record.provider == "docker"
    assert workspace_record.provider_instance_id == "dogfood-local"
    assert workspace_record.source_ref == {"runtime_profile": "dogfood-fixture"}
    await workspace_store.update_result_refs(
        workspace_record.workspace_record_id,
        result_refs={
            "branch_url": "https://example.test/branch",
            "secret_marker": "DOGFOOD_WORKSPACE_SECRET_SENTINEL",
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        pages = {
            "/console/workspaces": (
                "Workspace Provider",
                "Workspace Inventory",
                client.workspace_id,
                "dogfood-local",
            ),
            "/console/sessions": (session_id,),
            "/console/runs": (run_id, "completed"),
            f"/console/runs/{run_id}": ("Run Metadata", "Message Snapshot"),
            f"/console/observability?run_id={run_id}": (
                "Trace Correlation",
                run_id,
            ),
        }
        for path, expected_text in pages.items():
            response = await http.get(path)

            assert response.status_code == 200, path
            for text in expected_text:
                assert text in response.text, path
            for forbidden in FORBIDDEN_RENDERED_TEXT:
                assert forbidden not in response.text, path

    metrics = prometheus_metrics_text()
    assert 'route="console_workspaces"' in metrics
    assert client.workspace_id not in metrics
    assert "workspace_id" not in metrics
