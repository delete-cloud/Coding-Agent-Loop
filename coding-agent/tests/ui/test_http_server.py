"""Tests for HTTP API server."""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import shutil
import subprocess
import sys
import types
from dataclasses import replace
from types import SimpleNamespace
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient, ASGITransport
from httpx_sse import aconnect_sse
from starlette.requests import Request
from agentkit.directive.types import Approve, AskUser
from agentkit.errors import ConfigError
from agentkit.checkpoint.models import CheckpointMeta
from agentkit.providers.models import DoneEvent, TextEvent, ToolCallEvent
from agentkit.result.models import TurnResult, VerificationSummary
from agentkit.tape.extract import TurnTrace
from agentkit.tape.models import Entry
from agentkit.tape.tape import Tape
from agentkit.tools import FatalToolExecutionError

from coding_agent.adapter.types import StopReason, TurnOutcome
from coding_agent.approval import ApprovalPolicy
from coding_agent.approval.store import ApprovalStore
from coding_agent.plugins.approval import ApprovalPlugin
from coding_agent.core.config import settings
from coding_agent.environment import CloudCommandResult, CloudEnvironment
from coding_agent.stores.runtime_store import (
    AgentInteractionRecord,
    AgentRunRecord,
    RunMessageSnapshotRecord,
    RuntimeEventRecord,
)
from coding_agent.environment.workspace_provider import (
    WorkspaceArchiveManifest,
    WorkspaceBranchPublication,
    WorkspaceDiff,
    WorkspaceDiffFile,
    WorkspaceInventoryEntry,
    WorkspacePatch,
)
from coding_agent.runs import (
    CloudWorkspaceRef,
    ExternalWorkerExecutorRef,
    ExternalWorkerWorkspaceRef,
    IsolationPolicy,
    LocalAttachedExecutorRef,
    LocalDaemonExecutorRef,
    LocalPathWorkspaceRef,
    RunTarget,
    RuntimeContextBindingService,
)
from coding_agent.wire.local import LocalWire
from coding_agent.server.session_manager import Session, SessionManager
import coding_agent.server.session_manager as session_manager_module
from coding_agent.server.auth import AuthContext
from coding_agent.server.stores.workspace_store import JSONValue, WorkspaceRecord
from coding_agent.server.stores.session_owner_store import SessionOwnerRecord
from coding_agent.server.stores.session_owner_store import SessionOwnershipConflictError
from coding_agent.server.stores.session_owner_store import (
    SessionOwnershipConflictReason,
)
from coding_agent.topics.memory import (
    MemoryReviewStore,
    ReviewedMemoryRecord,
    TopicDerivedMemoryCandidate,
)
from coding_agent.topics.store import TopicAnchorRecord, TopicRecord
from coding_agent.topics.semantic_backends import (
    FAKE_SEMANTIC_INDEX_SCHEMA,
    FakeSemanticMemoryBackend,
)
from coding_agent.topics.semantic_index import SafeSemanticMemoryIndex, SemanticDocId
from coding_agent.topics.semantic_sync import (
    SemanticMemoryReviewSyncService,
    SemanticSyncReport,
    SemanticMemorySyncer,
)
from coding_agent.server.http_server import (
    SESSION_IDLE_TIMEOUT_MINUTES,
    _build_session_manager,
    _renew_owner_leases,
    _cleanup_event_queue_on_disconnect,
    _broadcast_event,
    get_events,
    get_session_display_events,
    _session_to_dict,
    stream_wire_messages,
    _wire_message_to_event,
    app,
    session_manager,
    wait_for_approval,
)
import coding_agent.server.http_server as http_server
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    ThinkingDelta,
    StreamDelta,
    ToolCallDelta,
    ToolResultDelta,
    TurnStatusDelta,
    TurnEnd,
)
from coding_agent.stores.local import local_sqlite_storage_config


def _local_run_target(path: Path | str) -> RunTarget:
    return RunTarget(
        workspace=LocalPathWorkspaceRef(path=str(path)),
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(kind="default_local_sandbox"),
    )


def _unsafe_local_run_target_payload(path: Path | str) -> dict[str, object]:
    payload = _local_run_target(path).to_dict()
    payload["isolation"] = {
        "kind": "dev_unsafe_disabled",
        "network": "unrestricted",
        "filesystem": "unrestricted",
        "secrets": "unrestricted",
    }
    return payload


def _cloud_run_target(workspace: CloudWorkspaceRef) -> RunTarget:
    return RunTarget(
        workspace=workspace,
        executor=LocalDaemonExecutorRef(),
        isolation=IsolationPolicy(
            kind="provider_sandbox",
            network="provider_managed",
            filesystem="provider_managed",
            secrets="provider_managed",
        ),
    )


def _cloud_run_target_payload(
    *,
    workspace_url: str = "https://workspace.example.com",
    workspace_id: str = "ws-123",
    workspace_provider: str | None = None,
    provider_instance_id: str | None = None,
) -> dict[str, object]:
    return _cloud_run_target(
        CloudWorkspaceRef(
            workspace_url=workspace_url,
            workspace_id=workspace_id,
            workspace_provider=workspace_provider,
            provider_instance_id=provider_instance_id,
        )
    ).to_dict()


def _attached_run_target_payload(
    *,
    kind: str = "external_worker",
    display_path: str = "/tmp/repo",
) -> dict[str, object]:
    executor = (
        LocalAttachedExecutorRef(executor_kind="local_cli")
        if kind == "local_attached"
        else ExternalWorkerExecutorRef(executor_kind="local_cli")
    )
    return RunTarget(
        workspace=ExternalWorkerWorkspaceRef(
            ref={"kind": "local_path", "display_path": display_path}
        ),
        executor=executor,
        isolation=IsolationPolicy(kind="external_worker_policy"),
    ).to_dict()


def _external_worker_run_target() -> RunTarget:
    return RunTarget(
        workspace=ExternalWorkerWorkspaceRef(),
        executor=ExternalWorkerExecutorRef(executor_kind="local_cli"),
        isolation=IsolationPolicy(kind="external_worker_policy"),
    )


def _test_runtime_profile_config(image: str = "python:3.11-slim") -> dict[str, object]:
    return {
        "default_runtime_profile": "universal",
        "image_allowlist": [image],
        "runtime_profiles": {
            "universal": {
                "provider": "docker",
                "image": image,
            }
        },
    }


def test_console_topic_store_uses_session_manager_selected_topic_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_store = object()

    class FakeSessionManager:
        def selected_topic_store(self) -> object:
            return selected_store

    monkeypatch.setattr(http_server, "session_manager", FakeSessionManager())

    assert http_server._console_topic_store() is selected_store


@pytest.fixture(autouse=True)
async def clear_sessions(isolated_http_session_manager: SessionManager):
    """Provide each test with an isolated unfenced session manager."""
    del isolated_http_session_manager
    yield


def register_session(
    session_id: str,
    **overrides,
) -> Session:
    session = Session(
        id=session_id,
        created_at=overrides.pop("created_at", datetime.now()),
        last_activity=overrides.pop("last_activity", datetime.now()),
        **overrides,
    )
    session_manager.register_session(session)
    return session


def _review_candidate(
    candidate_id: str,
    *,
    title: str = "Auth convention",
    summary: str = "JWT middleware convention",
    session_id: str | None = "memory-review-session",
    tape_id: str | None = "memory-review-tape",
    profile: str | None = "local",
) -> TopicDerivedMemoryCandidate:
    provenance: dict[str, object] = {
        "topic_id": "topic-auth",
        "topic_status": "finalized",
        "topic_kind": "coding",
        "source_entry_ranges": [
            {"topic_id": "topic-auth", "start_seq": 2, "end_seq": 9}
        ],
    }
    if session_id is not None:
        provenance["session_id"] = session_id
    if tape_id is not None:
        provenance["tape_id"] = tape_id
    if profile is not None:
        provenance["profile"] = profile
    return TopicDerivedMemoryCandidate(
        kind="fact",
        title=title,
        summary=summary,
        scope="topic:topic-auth",
        tags=("auth", "jwt"),
        confidence=0.8,
        provenance=provenance,
        candidate_id=candidate_id,
    )


def _reviewed_memory_doc_id(record: ReviewedMemoryRecord) -> str:
    return str(SemanticDocId.for_reviewed_memory(record))


def _semantic_review_runtime_config() -> tuple[
    dict[str, object],
    MemoryReviewStore,
    FakeSemanticMemoryBackend,
    SemanticMemorySyncer,
]:
    review_store = MemoryReviewStore()
    backend = FakeSemanticMemoryBackend()
    index = SafeSemanticMemoryIndex(backend)
    syncer = SemanticMemorySyncer(
        index=index,
        backend=backend,
        schema=FAKE_SEMANTIC_INDEX_SCHEMA,
    )
    service = SemanticMemoryReviewSyncService(
        review_store=review_store,
        syncer=syncer,
    )
    return (
        {
            "memory_review_store": review_store,
            "semantic_memory_backend": backend,
            "semantic_memory_index": index,
            "semantic_memory_syncer": syncer,
            "semantic_memory_review_sync_service": service,
        },
        review_store,
        backend,
        syncer,
    )


def _direct_review_runtime_config() -> tuple[dict[str, object], MemoryReviewStore]:
    review_store = MemoryReviewStore()
    return {"memory_review_store": review_store}, review_store


def _runtime_ctx(
    config: dict[str, object],
    *,
    tape: Tape | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        config=config,
        tape=tape if tape is not None else Tape(tape_id="memory-review-tape"),
    )


class _MemoryMaintenanceTopicStore:
    def __init__(
        self,
        *,
        create_exc: Exception | None = None,
        finalize_exc: Exception | None = None,
    ) -> None:
        self.topics: dict[str, TopicRecord] = {}
        self.anchors: list[TopicAnchorRecord] = []
        self.deleted: list[str] = []
        self._create_exc = create_exc
        self._finalize_exc = finalize_exc

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        if self._create_exc is not None:
            raise self._create_exc
        self.topics[record.topic_id] = record
        return record

    async def finalize_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int,
        finalized_at: datetime,
        metadata: dict[str, object],
    ) -> TopicRecord:
        if self._finalize_exc is not None:
            raise self._finalize_exc
        topic = self.topics[topic_id]
        finalized = replace(
            topic,
            status="finalized",
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=cast(dict[str, object], metadata),
        )
        self.topics[topic_id] = finalized
        return finalized

    async def abort_topic(
        self,
        topic_id: str,
        *,
        summary: str | None,
        topic_finalized_seq: int | None,
        finalized_at: datetime,
        metadata: dict[str, object],
    ) -> TopicRecord:
        topic = self.topics[topic_id]
        aborted = replace(
            topic,
            status="aborted",
            summary=summary,
            topic_finalized_seq=topic_finalized_seq,
            finalized_at=finalized_at,
            metadata=cast(dict[str, object], metadata),
        )
        self.topics[topic_id] = aborted
        return aborted

    async def record_topic_anchor(
        self,
        record: TopicAnchorRecord,
    ) -> TopicAnchorRecord:
        self.anchors.append(record)
        return record

    async def delete_topic(self, topic_id: str) -> None:
        self.deleted.append(topic_id)
        self.topics.pop(topic_id, None)
        self.anchors = [
            anchor for anchor in self.anchors if anchor.topic_id != topic_id
        ]


class _MemoryMaintenanceTapeStore:
    def __init__(self, *, save_exc: Exception | None = None) -> None:
        self.saved: list[tuple[str, list[dict[str, object]]]] = []
        self.truncated: list[tuple[str, int]] = []
        self._save_exc = save_exc

    async def save(self, tape_id: str, entries: list[dict[str, object]]) -> None:
        if self._save_exc is not None:
            raise self._save_exc
        self.saved.append((tape_id, entries))

    async def load(self, tape_id: str) -> list[dict[str, object]]:
        del tape_id
        return []

    async def list_ids(self) -> list[str]:
        return []

    async def truncate(self, tape_id: str, keep: int) -> None:
        self.truncated.append((tape_id, keep))


class _RecordingTopicSyncer:
    def __init__(self) -> None:
        self.synced: list[TopicRecord] = []

    async def sync_topic(self, topic: TopicRecord) -> object:
        self.synced.append(topic)
        return object()


class _FailingTopicSyncer(_RecordingTopicSyncer):
    async def sync_topic(self, topic: TopicRecord) -> object:
        self.synced.append(topic)
        raise RuntimeError("semantic sync failed")


def _install_memory_review_runtime(
    monkeypatch: pytest.MonkeyPatch,
    session_id: str,
    ctx: SimpleNamespace,
) -> list[str]:
    register_session(session_id)
    ensure_calls: list[str] = []

    async def fake_ensure_session_runtime(candidate_session_id: str) -> object:
        ensure_calls.append(candidate_session_id)
        return ctx

    monkeypatch.setattr(
        session_manager,
        "ensure_session_runtime",
        fake_ensure_session_runtime,
    )
    return ensure_calls


class _FailingReviewedMemorySyncer:
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("semantic backend unavailable")

    async def sync_reviewed_memory(
        self,
        record: ReviewedMemoryRecord,
    ) -> object:
        del record
        raise self._exc


class TestSemanticMemoryMaintenance:
    async def test_semantic_status_requires_admin(
        self,
        client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)
        register_session("semantic-admin-session")

        response = await client.get(
            "/sessions/semantic-admin-session/memory/semantic/status",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Admin token required"

    async def test_semantic_status_returns_counts_without_mutating_backend(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, syncer = _semantic_review_runtime_config()
        accepted_candidate = _review_candidate(
            "memory-status-accepted",
            session_id="semantic-status-session",
        )
        rejected_candidate = _review_candidate(
            "memory-status-rejected",
            session_id="semantic-status-session",
        )
        review_store.add_candidate(accepted_candidate)
        review_store.add_candidate(rejected_candidate)
        accepted = review_store.accept_candidate_for_session(
            "semantic-status-session",
            "memory-status-accepted",
            reason="Useful",
        )
        review_store.reject_candidate_for_session(
            "semantic-status-session",
            "memory-status-rejected",
            reason="Too narrow",
        )
        await syncer.sync_reviewed_memory(accepted)
        register_session("semantic-status-session")

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-status-session"
            return _runtime_ctx(config)

        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )
        monkeypatch.setattr(session_manager, "selected_topic_store", lambda: None)
        before_ids = await backend.list_ids()

        response = await client.get(
            "/sessions/semantic-status-session/memory/semantic/status"
        )

        assert response.status_code == 200
        assert response.json() == {
            "document_count": 1,
            "reviewed_memory_count": 2,
            "accepted_reviewed_memory_count": 1,
            "topic_store_available": False,
        }
        assert await backend.list_ids() == before_ids

    async def test_semantic_status_disabled_returns_409(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-disabled-session")

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-disabled-session"
            return _runtime_ctx({})

        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.get(
            "/sessions/semantic-disabled-session/memory/semantic/status"
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "semantic memory is disabled"

    async def test_semantic_status_unknown_session_returns_404(
        self,
        client: AsyncClient,
    ) -> None:
        response = await client.get(
            "/sessions/missing-semantic-session/memory/semantic/status"
        )

        assert response.status_code == 404
        assert (
            response.json()["detail"] == "Session not found: missing-semantic-session"
        )

    async def test_semantic_rebuild_requires_admin(
        self,
        client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)
        register_session("semantic-rebuild-admin-session")

        response = await client.post(
            "/sessions/semantic-rebuild-admin-session/memory/semantic/rebuild",
            headers={"Authorization": "Bearer user-token-a"},
            json={"batch_size": 10, "allow_rebuild": True, "confirm_global": True},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Admin token required"

    async def test_semantic_rebuild_returns_report(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-rebuild-session")
        rebuild_calls: list[tuple[str, int, bool]] = []

        async def fake_rebuild_semantic_memory(
            session_id: str,
            *,
            batch_size: int,
            allow_rebuild: bool,
        ) -> SemanticSyncReport:
            rebuild_calls.append((session_id, batch_size, allow_rebuild))
            return SemanticSyncReport(
                topic_count=2,
                reviewed_memory_count=1,
                indexed_count=3,
                skipped_count=0,
                deleted_count=4,
                indexed_ids=("topic-summary:topic-a:1-9", "memory:memory-a"),
                deleted_ids=("topic-summary:old-topic:1-2",),
            )

        async def fail_direct_maintainer_call(session_id: str) -> object:
            del session_id
            raise AssertionError("HTTP must call SessionManager rebuild facade")

        monkeypatch.setattr(
            session_manager,
            "rebuild_semantic_memory",
            fake_rebuild_semantic_memory,
        )
        monkeypatch.setattr(
            session_manager,
            "semantic_memory_maintainer",
            fail_direct_maintainer_call,
        )

        response = await client.post(
            "/sessions/semantic-rebuild-session/memory/semantic/rebuild",
            json={"batch_size": 25, "allow_rebuild": False, "confirm_global": True},
        )

        assert response.status_code == 200
        assert rebuild_calls == [("semantic-rebuild-session", 25, False)]
        assert response.json() == {
            "topic_count": 2,
            "scope": "global",
            "reviewed_memory_count": 1,
            "indexed_count": 3,
            "skipped_count": 0,
            "deleted_count": 4,
            "indexed_ids": ["topic-summary:topic-a:1-9", "memory:memory-a"],
            "deleted_ids": ["topic-summary:old-topic:1-2"],
        }

    async def test_semantic_rebuild_active_turn_returns_409_without_clearing_backend(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = register_session("semantic-active-session")
        session.turn_in_progress = True
        body_called = False

        class FailingMaintainer:
            async def rebuild(
                self,
                *,
                batch_size: int,
                allow_rebuild: bool,
            ) -> SemanticSyncReport:
                nonlocal body_called
                del batch_size, allow_rebuild
                body_called = True
                raise AssertionError("destructive rebuild body was reached")

        async def fake_maintainer(session_id: str) -> FailingMaintainer:
            assert session_id == "semantic-active-session"
            return FailingMaintainer()

        monkeypatch.setattr(
            session_manager, "semantic_memory_maintainer", fake_maintainer
        )

        response = await client.post(
            "/sessions/semantic-active-session/memory/semantic/rebuild",
            json={"batch_size": 10, "allow_rebuild": True, "confirm_global": True},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Turn already in progress"
        assert body_called is False

    async def test_semantic_rebuild_missing_topic_store_returns_409_without_clearing_backend(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, syncer = _semantic_review_runtime_config()
        candidate = _review_candidate(
            "memory-rebuild-existing",
            session_id="semantic-missing-topic-store-session",
        )
        review_store.add_candidate(candidate)
        accepted = review_store.accept_candidate_for_session(
            "semantic-missing-topic-store-session",
            "memory-rebuild-existing",
            reason="Useful",
        )
        await syncer.sync_reviewed_memory(accepted)
        register_session("semantic-missing-topic-store-session")

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-missing-topic-store-session"
            return _runtime_ctx(config)

        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )
        monkeypatch.setattr(session_manager, "selected_topic_store", lambda: None)
        before_ids = await backend.list_ids()

        response = await client.post(
            "/sessions/semantic-missing-topic-store-session/memory/semantic/rebuild",
            json={"batch_size": 10, "allow_rebuild": True, "confirm_global": True},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "topic_store is required for semantic memory rebuild"
        )
        assert await backend.list_ids() == before_ids

    async def test_semantic_rebuild_owner_conflict_maps_to_http_conflict(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-owner-conflict-session")

        async def fake_rebuild_semantic_memory(
            session_id: str,
            *,
            batch_size: int,
            allow_rebuild: bool,
        ) -> SemanticSyncReport:
            del session_id, batch_size, allow_rebuild
            raise SessionOwnershipConflictError(
                "stale owner or fencing token rejected",
                reason=SessionOwnershipConflictReason.STALE_OWNER,
            )

        monkeypatch.setattr(
            session_manager,
            "rebuild_semantic_memory",
            fake_rebuild_semantic_memory,
        )

        response = await client.post(
            "/sessions/semantic-owner-conflict-session/memory/semantic/rebuild",
            json={"batch_size": 10, "allow_rebuild": True, "confirm_global": True},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "stale owner or fencing token rejected"

    async def test_semantic_rebuild_request_validates_batch_size_and_explicit_confirmations(
        self,
        client: AsyncClient,
    ) -> None:
        register_session("semantic-schema-session")

        zero = await client.post(
            "/sessions/semantic-schema-session/memory/semantic/rebuild",
            json={"batch_size": 0, "allow_rebuild": True, "confirm_global": True},
        )
        too_large = await client.post(
            "/sessions/semantic-schema-session/memory/semantic/rebuild",
            json={"batch_size": 1001, "allow_rebuild": True, "confirm_global": True},
        )
        missing_allow_rebuild = await client.post(
            "/sessions/semantic-schema-session/memory/semantic/rebuild",
            json={"batch_size": 10},
        )
        missing_confirm_global = await client.post(
            "/sessions/semantic-schema-session/memory/semantic/rebuild",
            json={"batch_size": 10, "allow_rebuild": True},
        )
        false_confirm_global = await client.post(
            "/sessions/semantic-schema-session/memory/semantic/rebuild",
            json={
                "batch_size": 10,
                "allow_rebuild": True,
                "confirm_global": False,
            },
        )

        assert zero.status_code == 422
        assert too_large.status_code == 422
        assert missing_allow_rebuild.status_code == 422
        assert missing_confirm_global.status_code == 422
        assert false_confirm_global.status_code == 422

    async def test_semantic_dogfood_topic_requires_admin(
        self,
        client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)
        register_session("semantic-dogfood-admin-session")

        response = await client.post(
            "/sessions/semantic-dogfood-admin-session/memory/semantic/dogfood-topic",
            headers={"Authorization": "Bearer user-token-a"},
            json={"title": "Dogfood topic", "summary": "Seed durable anchors"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Admin token required"

    async def test_semantic_dogfood_topic_active_turn_returns_409_without_writes(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = register_session("semantic-dogfood-active-session")
        session.turn_in_progress = True
        topic_store = _MemoryMaintenanceTopicStore()
        tape_store = _MemoryMaintenanceTapeStore()

        async def fail_ensure_session_runtime(session_id: str) -> object:
            del session_id
            raise AssertionError("dogfood topic body was reached")

        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fail_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-active-session/memory/semantic/dogfood-topic",
            json={"title": "Dogfood topic", "summary": "Seed durable anchors"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "Turn already in progress"
        assert topic_store.topics == {}
        assert topic_store.anchors == []
        assert tape_store.saved == []

    async def test_semantic_dogfood_topic_creates_topic_candidate_and_delta_tape(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-dogfood-session")
        review_store = MemoryReviewStore()
        syncer = _RecordingTopicSyncer()
        topic_store = _MemoryMaintenanceTopicStore()
        tape_store = _MemoryMaintenanceTapeStore()
        tape = Tape(tape_id="dogfood-tape")
        tape.append(Entry(kind="message", payload={"content": "base entry"}))
        ctx = _runtime_ctx(
            {
                "memory_review_store": review_store,
                "memory": {"effective_write_enabled": True},
                "semantic_memory_syncer": syncer,
            },
            tape=tape,
        )

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-dogfood-session"
            return ctx

        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-session/memory/semantic/dogfood-topic",
            json={
                "title": "Dogfood semantic memory",
                "summary": "Seed one durable finalized topic for memory dogfood.",
            },
        )

        assert response.status_code == 200
        stored_records = review_store.list_memories(status="candidate")
        assert len(stored_records) == 1
        stored = stored_records[0]
        assert response.json() == {
            "topic_id": stored.candidate.provenance["topic_id"],
            "candidate_id": stored.candidate.candidate_id,
            "warnings": [],
        }
        assert stored.candidate.title == "Dogfood semantic memory"
        assert (
            stored.candidate.summary
            == "Seed one durable finalized topic for memory dogfood."
        )
        assert stored.candidate.provenance["session_id"] == "semantic-dogfood-session"
        assert stored.candidate.provenance["tape_id"] == "dogfood-tape"
        assert [anchor.anchor_type for anchor in topic_store.anchors] == [
            "topic_initial",
            "topic_finalized",
        ]
        assert len(syncer.synced) == 1
        assert syncer.synced[0].status == "finalized"
        assert len(tape_store.saved) == 1
        saved_tape_id, saved_entries = tape_store.saved[0]
        assert saved_tape_id == "dogfood-tape"
        assert len(saved_entries) == 2
        assert [entry["kind"] for entry in saved_entries] == ["anchor", "anchor"]
        assert all(entry["payload"]["label"] != "base entry" for entry in saved_entries)
        assert ctx.tape.tape_id == "dogfood-tape"
        assert len(ctx.tape) == 3

    async def test_semantic_dogfood_topic_tape_commit_failure_leaves_no_derived_writes(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-dogfood-commit-failure-session")
        review_store = MemoryReviewStore()
        syncer = _RecordingTopicSyncer()
        topic_store = _MemoryMaintenanceTopicStore()
        tape_store = _MemoryMaintenanceTapeStore(
            save_exc=RuntimeError("tape save failed")
        )
        tape = Tape(tape_id="dogfood-commit-failure-tape")
        tape.append(Entry(kind="message", payload={"content": "base entry"}))
        ctx = _runtime_ctx(
            {
                "memory_review_store": review_store,
                "memory": {"effective_write_enabled": True},
                "semantic_memory_syncer": syncer,
            },
            tape=tape,
        )

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-dogfood-commit-failure-session"
            return ctx

        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-commit-failure-session/memory/semantic/dogfood-topic",
            json={
                "title": "Dogfood semantic memory",
                "summary": "Seed one durable finalized topic for memory dogfood.",
            },
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "tape save failed"
        assert topic_store.topics == {}
        assert topic_store.anchors == []
        assert review_store.list_memories(status="candidate") == ()
        assert syncer.synced == []
        assert tape_store.truncated == []
        assert ctx.tape is tape
        assert len(ctx.tape) == 1

    @pytest.mark.parametrize(
        ("memory_write_enabled", "candidate_writes_enabled"),
        [(False, True), (True, False)],
    )
    async def test_semantic_dogfood_topic_disabled_candidate_writes_returns_no_candidate(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        memory_write_enabled: bool,
        candidate_writes_enabled: bool,
    ) -> None:
        register_session("semantic-dogfood-no-candidate-session")
        review_store = MemoryReviewStore(
            candidate_writes_enabled=candidate_writes_enabled
        )
        syncer = _RecordingTopicSyncer()
        topic_store = _MemoryMaintenanceTopicStore()
        tape_store = _MemoryMaintenanceTapeStore()
        tape = Tape(tape_id="dogfood-no-candidate-tape")
        tape.append(Entry(kind="message", payload={"content": "base entry"}))
        ctx = _runtime_ctx(
            {
                "memory_review_store": review_store,
                "memory": {"effective_write_enabled": memory_write_enabled},
                "semantic_memory_syncer": syncer,
            },
            tape=tape,
        )

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-dogfood-no-candidate-session"
            return ctx

        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-no-candidate-session/memory/semantic/dogfood-topic",
            json={
                "title": "Dogfood semantic memory",
                "summary": "Seed one durable finalized topic for memory dogfood.",
            },
        )

        assert response.status_code == 200
        assert response.json()["candidate_id"] is None
        assert response.json()["warnings"] == []
        assert len(topic_store.topics) == 1
        assert topic_store.anchors != []
        assert review_store.list_memories(status="candidate") == ()
        assert len(syncer.synced) == 1
        assert len(tape_store.saved) == 1
        assert ctx.tape.tape_id == "dogfood-no-candidate-tape"
        assert len(ctx.tape) == 3

    async def test_semantic_dogfood_topic_topic_store_failure_truncates_committed_tape(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-dogfood-topic-failure-session")
        review_store = MemoryReviewStore()
        syncer = _RecordingTopicSyncer()
        topic_store = _MemoryMaintenanceTopicStore(
            create_exc=RuntimeError("topic store failed")
        )
        tape_store = _MemoryMaintenanceTapeStore()
        tape = Tape(tape_id="dogfood-topic-failure-tape")
        tape.append(Entry(kind="message", payload={"content": "base entry"}))
        ctx = _runtime_ctx(
            {
                "memory_review_store": review_store,
                "memory": {"effective_write_enabled": True},
                "semantic_memory_syncer": syncer,
            },
            tape=tape,
        )

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-dogfood-topic-failure-session"
            return ctx

        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-topic-failure-session/memory/semantic/dogfood-topic",
            json={
                "title": "Dogfood semantic memory",
                "summary": "Seed one durable finalized topic for memory dogfood.",
            },
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "topic store failed"
        assert topic_store.topics == {}
        assert topic_store.anchors == []
        assert review_store.list_memories(status="candidate") == ()
        assert syncer.synced == []
        assert len(tape_store.saved) == 1
        assert tape_store.truncated == [("dogfood-topic-failure-tape", 1)]
        assert ctx.tape is tape
        assert len(ctx.tape) == 1

    async def test_semantic_dogfood_topic_final_write_failure_cleans_topic_before_truncating_tape(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-dogfood-final-write-failure-session")
        review_store = MemoryReviewStore()
        syncer = _RecordingTopicSyncer()
        topic_store = _MemoryMaintenanceTopicStore(
            finalize_exc=RuntimeError("topic finalize failed")
        )
        tape_store = _MemoryMaintenanceTapeStore()
        tape = Tape(tape_id="dogfood-final-write-failure-tape")
        tape.append(Entry(kind="message", payload={"content": "base entry"}))
        ctx = _runtime_ctx(
            {
                "memory_review_store": review_store,
                "memory": {"effective_write_enabled": True},
                "semantic_memory_syncer": syncer,
            },
            tape=tape,
        )

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-dogfood-final-write-failure-session"
            return ctx

        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-final-write-failure-session/memory/semantic/dogfood-topic",
            json={
                "title": "Dogfood semantic memory",
                "summary": "Seed one durable finalized topic for memory dogfood.",
            },
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "topic finalize failed"
        assert len(topic_store.deleted) == 1
        assert topic_store.topics == {}
        assert topic_store.anchors == []
        assert review_store.list_memories(status="candidate") == ()
        assert syncer.synced == []
        assert len(tape_store.saved) == 1
        assert tape_store.truncated == [("dogfood-final-write-failure-tape", 1)]
        assert ctx.tape is tape
        assert len(ctx.tape) == 1

    async def test_semantic_dogfood_topic_review_failure_warns_without_core_rollback(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-dogfood-review-failure-session")
        review_store = MemoryReviewStore()
        syncer = _RecordingTopicSyncer()
        topic_store = _MemoryMaintenanceTopicStore()
        tape_store = _MemoryMaintenanceTapeStore()
        tape = Tape(tape_id="dogfood-review-failure-tape")
        tape.append(Entry(kind="message", payload={"content": "base entry"}))
        ctx = _runtime_ctx(
            {
                "memory_review_store": review_store,
                "memory": {"effective_write_enabled": True},
                "semantic_memory_syncer": syncer,
            },
            tape=tape,
        )

        def fail_add_candidate(candidate: TopicDerivedMemoryCandidate) -> object:
            del candidate
            raise RuntimeError("review store failed")

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-dogfood-review-failure-session"
            return ctx

        monkeypatch.setattr(review_store, "add_candidate", fail_add_candidate)
        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-review-failure-session/memory/semantic/dogfood-topic",
            json={
                "title": "Dogfood semantic memory",
                "summary": "Seed one durable finalized topic for memory dogfood.",
            },
        )

        assert response.status_code == 200
        assert response.json()["candidate_id"] is None
        assert response.json()["warnings"] == [
            "memory review candidate write failed: review store failed"
        ]
        assert len(topic_store.topics) == 1
        assert topic_store.deleted == []
        assert topic_store.anchors != []
        assert review_store.list_memories(status="candidate") == ()
        assert len(syncer.synced) == 1
        assert tape_store.truncated == []
        assert ctx.tape.tape_id == "dogfood-review-failure-tape"
        assert len(ctx.tape) == 3

    async def test_semantic_dogfood_topic_review_proposal_failure_warns_without_core_rollback(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-dogfood-proposal-failure-session")
        review_store = MemoryReviewStore()
        syncer = _RecordingTopicSyncer()
        topic_store = _MemoryMaintenanceTopicStore()
        tape_store = _MemoryMaintenanceTapeStore()
        tape = Tape(tape_id="dogfood-proposal-failure-tape")
        tape.append(Entry(kind="message", payload={"content": "base entry"}))
        ctx = _runtime_ctx(
            {
                "memory_review_store": review_store,
                "memory": {"effective_write_enabled": True},
                "semantic_memory_syncer": syncer,
            },
            tape=tape,
        )

        def fail_proposal(record: TopicRecord) -> TopicDerivedMemoryCandidate | None:
            del record
            raise RuntimeError("proposal failed")

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-dogfood-proposal-failure-session"
            return ctx

        monkeypatch.setattr(
            session_manager_module,
            "propose_memory_candidate_from_topic",
            fail_proposal,
        )
        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-proposal-failure-session/memory/semantic/dogfood-topic",
            json={
                "title": "Dogfood semantic memory",
                "summary": "Seed one durable finalized topic for memory dogfood.",
            },
        )

        assert response.status_code == 200
        assert response.json()["candidate_id"] is None
        assert response.json()["warnings"] == [
            "memory review candidate write failed: proposal failed"
        ]
        assert len(topic_store.topics) == 1
        assert topic_store.deleted == []
        assert topic_store.anchors != []
        assert review_store.list_memories(status="candidate") == ()
        assert len(syncer.synced) == 1
        assert tape_store.truncated == []
        assert ctx.tape.tape_id == "dogfood-proposal-failure-tape"
        assert len(ctx.tape) == 3

    async def test_semantic_dogfood_topic_semantic_sync_failure_warns_without_core_rollback(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        register_session("semantic-dogfood-sync-failure-session")
        review_store = MemoryReviewStore()
        syncer = _FailingTopicSyncer()
        topic_store = _MemoryMaintenanceTopicStore()
        tape_store = _MemoryMaintenanceTapeStore()
        tape = Tape(tape_id="dogfood-sync-failure-tape")
        tape.append(Entry(kind="message", payload={"content": "base entry"}))
        ctx = _runtime_ctx(
            {
                "memory_review_store": review_store,
                "memory": {"effective_write_enabled": True},
                "semantic_memory_syncer": syncer,
            },
            tape=tape,
        )

        async def fake_ensure_session_runtime(session_id: str) -> object:
            assert session_id == "semantic-dogfood-sync-failure-session"
            return ctx

        monkeypatch.setattr(
            session_manager, "selected_topic_store", lambda: topic_store
        )
        monkeypatch.setattr(session_manager, "_tape_store", tape_store)
        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fake_ensure_session_runtime,
        )

        response = await client.post(
            "/sessions/semantic-dogfood-sync-failure-session/memory/semantic/dogfood-topic",
            json={
                "title": "Dogfood semantic memory",
                "summary": "Seed one durable finalized topic for memory dogfood.",
            },
        )

        assert response.status_code == 200
        assert response.json()["candidate_id"] is not None
        assert response.json()["warnings"] == [
            "semantic topic sync failed: semantic sync failed"
        ]
        assert len(topic_store.topics) == 1
        assert topic_store.deleted == []
        assert topic_store.anchors != []
        assert len(review_store.list_memories(status="candidate")) == 1
        assert len(syncer.synced) == 1
        assert tape_store.truncated == []
        assert ctx.tape.tape_id == "dogfood-sync-failure-tape"
        assert len(ctx.tape) == 3


def test_http_server_uses_canonical_rate_limiter_module():
    assert app.state.limiter is http_server.limiter


def _minimal_agent_toml(extra: str = "") -> str:
    return (
        "[agent]\n"
        'name = "test-agent"\n'
        'model = "test-model"\n'
        'provider = "openai"\n'
        f"{extra}"
    )


def _safe_production_cloud_workspace_config() -> dict[str, object]:
    return {
        "enabled": True,
        "provider": "docker",
        "workspace_root": "/srv/coding-agent/workspaces",
        "default_runtime_profile": "universal",
        "image_allowlist": ["coding-agent-runtime:2026-05-10"],
        "runtime_profiles": {
            "universal": {
                "provider": "docker",
                "image": "coding-agent-runtime:2026-05-10",
                "cpus": "2",
                "memory": "4g",
            }
        },
        "exec_user": "1000:1000",
        "max_active_workspaces": 8,
        "max_workspace_age_seconds": 86400,
        "gc_interval_seconds": 300,
        "network": "none",
        "cpus": "2",
        "memory": "4g",
        "pids_limit": 512,
    }


def _write_auth_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        _minimal_agent_toml(
            """
[server]
bearer_token = "user-token-a"
admin_bearer_token = "admin-token"
"""
        ),
        encoding="utf-8",
    )
    return config_path


def test_http_server_loads_config_from_explicit_server_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        _minimal_agent_toml(
            """
[server]
production = false

[cloud_workspace]
enabled = true
provider = "docker"
workspace_root = "/srv/coding-agent/workspaces"
"""
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(config_path))

    assert http_server._server_config_path() == config_path
    assert http_server._load_server_config() == {"production": False}
    assert http_server._load_cloud_workspace_config()["workspace_root"] == (
        "/srv/coding-agent/workspaces"
    )


def test_http_server_loads_runtime_profiles_into_cloud_workspace_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        _minimal_agent_toml(
            """
[cloud_workspace]
enabled = true
provider = "docker"
workspace_root = "/srv/coding-agent/workspaces"

[runtime_profiles.universal]
provider = "docker"
image = "registry.example/universal:2026-05-11"
cpus = "2"
memory = "4g"
"""
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(config_path))

    assert http_server._load_cloud_workspace_config()["runtime_profiles"] == {
        "universal": {
            "provider": "docker",
            "image": "registry.example/universal:2026-05-11",
            "cpus": "2",
            "memory": "4g",
        }
    }


def test_http_server_loads_remote_phases_into_cloud_workspace_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        _minimal_agent_toml(
            """
[cloud_workspace]
enabled = true
provider = "docker"
workspace_root = "/srv/coding-agent/workspaces"

[remote_phases.setup]
enabled = true
network = "bridge"
timeout_seconds = 600
commands = ["uv sync"]
secret_env_allowlist = ["PIP_INDEX_URL"]
allow_request_commands = false

[remote_phases.agent]
network = "none"
timeout_seconds = 3600
secret_env_allowlist = []
"""
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(config_path))

    assert http_server._load_cloud_workspace_config()["remote_phases"] == {
        "setup": {
            "enabled": True,
            "network": "bridge",
            "timeout_seconds": 600,
            "commands": ["uv sync"],
            "secret_env_allowlist": ["PIP_INDEX_URL"],
            "allow_request_commands": False,
        },
        "agent": {
            "network": "none",
            "timeout_seconds": 3600,
            "secret_env_allowlist": [],
        },
    }


def test_http_server_loads_remote_retention_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "server.toml"
    config_path.write_text(
        _minimal_agent_toml(
            """
[remote_retention]
enabled = true
default_policy = "ttl"
default_ttl_seconds = 86400
allow_user_pin = false
"""
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(config_path))

    assert http_server._load_remote_retention_config() == {
        "enabled": True,
        "default_policy": "ttl",
        "default_ttl_seconds": 86400,
        "allow_user_pin": False,
    }


def test_http_server_explicit_server_config_missing_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_config_path = tmp_path / "missing.toml"
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(missing_config_path))

    with pytest.raises(ConfigError, match="config file not found"):
        _ = http_server._load_server_config()


def test_production_config_accepts_safe_docker_workspace_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_AGENT_BEARER_TOKEN", "secret-token")

    http_server._validate_production_config(
        {
            "production": True,
            "bearer_token_env": "CODING_AGENT_BEARER_TOKEN",
        },
        _safe_production_cloud_workspace_config(),
    )


def test_production_config_accepts_durable_remote_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_AGENT_BEARER_TOKEN", "secret-token")
    cloud_workspace_config = _safe_production_cloud_workspace_config()
    cloud_workspace_config["provider_instance_id"] = "docker-host-a"

    http_server._validate_production_config(
        {
            "production": True,
            "bearer_token_env": "CODING_AGENT_BEARER_TOKEN",
        },
        cloud_workspace_config,
        storage_config={"http_session_backend": "pg", "dsn": "postgresql://example"},
        remote_retention_config={
            "enabled": True,
            "default_policy": "ttl",
            "default_ttl_seconds": 86400,
            "allow_user_pin": False,
        },
    )


def test_production_config_accepts_server_configured_setup_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODING_AGENT_BEARER_TOKEN", "secret-token")
    cloud_workspace_config = _safe_production_cloud_workspace_config()
    cloud_workspace_config["remote_phases"] = {
        "setup": {
            "enabled": True,
            "network": "bridge",
            "timeout_seconds": 600,
            "commands": ["uv sync"],
            "secret_env_allowlist": [],
            "allow_request_commands": False,
        },
        "agent": {"network": "none"},
    }

    http_server._validate_production_config(
        {
            "production": True,
            "bearer_token_env": "CODING_AGENT_BEARER_TOKEN",
        },
        cloud_workspace_config,
    )


@pytest.mark.parametrize(
    (
        "cloud_workspace_overrides",
        "storage_config",
        "remote_retention_config",
        "message",
    ),
    [
        (
            {},
            {"http_session_backend": "pg", "dsn": "postgresql://example"},
            {
                "enabled": True,
                "default_policy": "delete_on_close",
                "allow_user_pin": False,
            },
            "cloud_workspace.provider_instance_id",
        ),
        (
            {"provider_instance_id": "docker-host-a"},
            {"http_session_backend": "memory"},
            {
                "enabled": True,
                "default_policy": "delete_on_close",
                "allow_user_pin": False,
            },
            "remote_retention.enabled=true requires PostgreSQL HTTP session storage",
        ),
        (
            {"provider_instance_id": "docker-host-a"},
            {"http_session_backend": "pg", "dsn": "postgresql://example"},
            {
                "enabled": True,
                "default_policy": "ttl",
                "default_ttl_seconds": 0,
                "allow_user_pin": False,
            },
            "remote_retention.default_ttl_seconds",
        ),
        (
            {"provider_instance_id": "docker-host-a"},
            {"http_session_backend": "pg", "dsn": "postgresql://example"},
            {
                "enabled": True,
                "default_policy": "ttl",
                "default_ttl_seconds": -3600,
                "allow_user_pin": False,
            },
            "remote_retention.default_ttl_seconds",
        ),
        (
            {"provider_instance_id": "docker-host-a"},
            {"http_session_backend": "pg", "dsn": "postgresql://example"},
            {
                "enabled": True,
                "default_policy": "forever",
                "allow_user_pin": False,
            },
            "remote_retention.default_policy",
        ),
        (
            {"provider_instance_id": "docker-host-a"},
            {"http_session_backend": "pg", "dsn": "postgresql://example"},
            {
                "enabled": True,
                "default_policy": "delete_on_close",
                "allow_user_pin": "no",
            },
            "remote_retention.allow_user_pin",
        ),
    ],
)
def test_production_config_rejects_unsafe_remote_retention_config(
    monkeypatch: pytest.MonkeyPatch,
    cloud_workspace_overrides: dict[str, object],
    storage_config: dict[str, object],
    remote_retention_config: dict[str, object],
    message: str,
) -> None:
    monkeypatch.setenv("CODING_AGENT_BEARER_TOKEN", "secret-token")
    cloud_workspace_config = _safe_production_cloud_workspace_config()
    cloud_workspace_config.update(cloud_workspace_overrides)

    with pytest.raises(ValueError, match=re.escape(message)):
        http_server._validate_production_config(
            {
                "production": True,
                "bearer_token_env": "CODING_AGENT_BEARER_TOKEN",
            },
            cloud_workspace_config,
            storage_config=storage_config,
            remote_retention_config=remote_retention_config,
        )


@pytest.mark.parametrize(
    ("server_config", "cloud_workspace_overrides", "message"),
    [
        (
            {"production": True},
            {},
            "server.bearer_token_env",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"enabled": False},
            "cloud_workspace.enabled=true",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"image_allowlist": []},
            "cloud_workspace.image_allowlist",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"default_runtime_profile": ""},
            "cloud_workspace.default_runtime_profile",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"runtime_profiles": {}},
            "runtime_profiles must be explicitly configured",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"default_runtime_profile": "missing"},
            "cloud_workspace.default_runtime_profile must refer to a configured runtime profile",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"exec_user": "0:1000"},
            "cloud_workspace.exec_user must not be root",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"max_active_workspaces": 0},
            "cloud_workspace.max_active_workspaces",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"max_active_workspaces": True},
            "cloud_workspace.max_active_workspaces",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"max_workspace_age_seconds": 0},
            "cloud_workspace.max_workspace_age_seconds",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"gc_interval_seconds": 0},
            "cloud_workspace.gc_interval_seconds",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"cpus": ""},
            "cloud_workspace.cpus",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"memory": ""},
            "cloud_workspace.memory",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"pids_limit": 0},
            "cloud_workspace.pids_limit",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {"network": "bridge"},
            'cloud_workspace.network must be "none"',
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {
                "remote_phases": {
                    "setup": {
                        "enabled": True,
                        "network": "host",
                        "timeout_seconds": 600,
                    },
                    "agent": {"network": "none"},
                }
            },
            'remote_phases.setup.network must be "none" or "bridge"',
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {
                "remote_phases": {
                    "setup": {
                        "enabled": True,
                        "network": "bridge",
                        "timeout_seconds": 0,
                    },
                    "agent": {"network": "none"},
                }
            },
            "remote_phases.setup.timeout_seconds",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {
                "remote_phases": {
                    "setup": {
                        "enabled": True,
                        "network": "bridge",
                        "timeout_seconds": 600,
                        "commands": [],
                        "allow_request_commands": False,
                    },
                    "agent": {"network": "none"},
                }
            },
            "remote_phases.setup.enabled=true requires non-empty server-configured commands",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {
                "remote_phases": {
                    "setup": {
                        "enabled": True,
                        "network": "bridge",
                        "timeout_seconds": 600,
                        "commands": ["uv sync"],
                        "allow_request_commands": True,
                    },
                    "agent": {"network": "none"},
                }
            },
            "remote_phases.setup.allow_request_commands=true requires request-provided setup command execution support",
        ),
        (
            {"production": True, "bearer_token": "secret-token"},
            {
                "remote_phases": {
                    "setup": {
                        "enabled": False,
                        "network": "bridge",
                        "timeout_seconds": 600,
                        "commands": ["uv sync"],
                    },
                    "agent": {"network": "bridge"},
                }
            },
            'remote_phases.agent.network must be "none"',
        ),
    ],
)
def test_production_config_rejects_unsafe_remote_workspace_config(
    server_config: dict[str, object],
    cloud_workspace_overrides: dict[str, object],
    message: str,
) -> None:
    cloud_workspace_config = _safe_production_cloud_workspace_config()
    cloud_workspace_config.update(cloud_workspace_overrides)

    with pytest.raises(ValueError, match=re.escape(message)):
        http_server._validate_production_config(
            server_config,
            cloud_workspace_config,
        )


@pytest.mark.asyncio
async def test_http_create_session_rejects_request_setup_commands_until_execution_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud_workspace_config = _safe_production_cloud_workspace_config()
    cloud_workspace_config["remote_phases"] = {
        "setup": {
            "enabled": True,
            "network": "bridge",
            "timeout_seconds": 600,
            "allow_request_commands": True,
        },
        "agent": {"network": "none"},
    }
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: cloud_workspace_config,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/sessions",
            json={
                "workspace_source": {
                    "kind": "docker",
                    "setup_commands": ["uv sync"],
                }
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == ("setup phase execution is not implemented yet")


@pytest.mark.asyncio
async def test_http_create_session_rejects_request_setup_commands_when_policy_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud_workspace_config = _safe_production_cloud_workspace_config()
    cloud_workspace_config["remote_phases"] = {
        "setup": {
            "enabled": True,
            "network": "bridge",
            "timeout_seconds": 600,
            "commands": ["uv sync"],
            "allow_request_commands": False,
        },
        "agent": {"network": "none"},
    }
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: cloud_workspace_config,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/sessions",
            json={
                "workspace_source": {
                    "kind": "docker",
                    "setup_commands": ["uv sync"],
                }
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "workspace_source.setup_commands requires "
        "remote_phases.setup.allow_request_commands=true"
    )


@pytest.mark.asyncio
async def test_http_create_session_rejects_invalid_request_setup_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud_workspace_config = _safe_production_cloud_workspace_config()
    cloud_workspace_config["remote_phases"] = {
        "setup": {
            "enabled": True,
            "network": "bridge",
            "timeout_seconds": 600,
            "allow_request_commands": True,
        },
        "agent": {"network": "none"},
    }
    monkeypatch.setattr(
        "coding_agent.server.http_server._load_cloud_workspace_config",
        lambda: cloud_workspace_config,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/sessions",
            json={
                "workspace_source": {
                    "kind": "docker",
                    "setup_commands": [""],
                }
            },
        )

    assert response.status_code == 422
    assert "setup_commands" in response.text


def test_development_mode_warning_logs_when_production_is_not_enabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    http_server._log_development_mode_warning({"production": False})

    assert "not safe for team production use" in caplog.text


def add_store_backed_approval_request(
    session: Session,
    session_id: str,
    request_id: str,
) -> None:
    tool_call = ToolCallDelta(
        session_id=session_id,
        tool_name="bash",
        arguments={"command": "ls"},
        call_id=f"call-{request_id}",
    )
    approval_req = ApprovalRequest(
        session_id=session_id,
        request_id=request_id,
        tool_call=tool_call,
        timeout_seconds=120,
    )
    session.approval_store.add_request(approval_req)


class FakeCloudClient:
    workspace_id = "ws-configured"
    workspace_url = "https://workspace.example.com"
    default_cwd = "/workspace"

    def read_file(self, path: str) -> str:
        return path

    def write_file(self, path: str, content: str) -> None:
        del path, content

    def replace_file(self, path: str, old: str, new: str) -> None:
        del path, old, new

    def glob_files(self, pattern: str, directory: str) -> list[str]:
        del pattern, directory
        return []

    def grep_search(self, pattern: str, directory: str, include: str) -> list[str]:
        del pattern, directory, include
        return []

    def apply_patch(self, path: str, patch: str) -> dict[str, object]:
        del patch
        return {"success": True, "path": path, "changed": False}

    def run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        timeout: int,
    ) -> CloudCommandResult:
        del command, cwd, env, timeout
        return CloudCommandResult(stdout="", stderr="", exit_code=0)


@pytest.fixture
async def client():
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestSessionCreation:
    """Tests for session creation endpoint."""

    async def test_create_session(self, client):
        """Test creating a new session."""
        response = await client.post("/sessions", json={})
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36  # UUID format

    async def test_create_session_stores_in_memory(self, client):
        """Test that created session is stored in memory."""
        response = await client.post("/sessions", json={})
        data = response.json()
        session_id = data["session_id"]
        assert session_manager.has_session(session_id)
        assert session_manager.get_session(session_id).id == session_id

    async def test_healthz_reports_store_backed_session_count(self, client):
        response = await client.post("/sessions", json={})
        session_id = response.json()["session_id"]

        health = await client.get("/healthz")

        assert health.status_code == 200
        assert health.json()["sessions"] == 1
        assert session_manager.has_session(session_id)

    async def test_healthz_uses_count_sessions_async(self, client, monkeypatch):
        async def fake_count_sessions_async() -> int:
            return 7

        def fail_list_sessions_async():
            raise AssertionError("healthz should not call list_sessions_async")

        monkeypatch.setattr(
            session_manager,
            "count_sessions_async",
            fake_count_sessions_async,
        )
        monkeypatch.setattr(
            session_manager,
            "list_sessions_async",
            fail_list_sessions_async,
        )

        health = await client.get("/healthz")

        assert health.status_code == 200
        assert health.json()["sessions"] == 7

    async def test_metrics_endpoint_returns_404_when_disabled(self, client):
        response = await client.get("/metrics")

        assert response.status_code == 404

    async def test_metrics_endpoint_returns_prometheus_text_when_enabled(
        self,
        client,
        monkeypatch,
    ):
        monkeypatch.setattr(
            http_server,
            "_load_observability_config",
            lambda: {
                "enabled": True,
                "metrics": {"enabled": True, "endpoint_enabled": True},
            },
        )

        health = await client.get("/healthz")
        metrics = await client.get("/metrics")

        assert health.status_code == 200
        assert metrics.status_code == 200
        assert metrics.headers["content-type"].startswith("text/plain")
        text = metrics.text
        assert "coding_agent_http_requests_total" in text
        assert 'method="GET"' in text
        assert 'route="healthz"' in text
        assert 'status_code="200"' in text
        assert "coding_agent_http_request_duration_ms_count" in text

    async def test_metrics_endpoint_exposition_has_no_forbidden_labels_or_raw_text(
        self,
        client,
        monkeypatch,
    ):
        monkeypatch.setattr(
            http_server,
            "_load_observability_config",
            lambda: {
                "enabled": True,
                "metrics": {"enabled": True, "endpoint_enabled": True},
            },
        )

        await client.get("/sessions/not-a-secret-session-id")
        metrics = await client.get("/metrics")

        assert metrics.status_code == 200
        for forbidden in (
            "run_id",
            "session_id",
            "trace_id",
            "event_id",
            "interaction_id",
            "tool_call_id",
            "file_path",
            "prompt",
            "message",
            "content",
            "command_output",
            "secret",
            "not-a-secret-session-id",
        ):
            assert forbidden not in metrics.text

    async def test_metrics_config_failure_does_not_break_http_request(
        self,
        client,
        monkeypatch,
    ):
        def fail_config():
            raise RuntimeError("metrics config failed")

        monkeypatch.setattr(http_server, "_load_observability_config", fail_config)

        response = await client.get("/healthz")

        assert response.status_code == 200

    async def test_readyz_reports_dependencies_ready(self, client):
        ready = await client.get("/readyz")

        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "checks": {"session_store": "ok", "rate_limiter": "ok"},
        }

    async def test_readyz_returns_503_when_session_store_unhealthy(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(session_manager._store, "check_health", lambda: False)

        ready = await client.get("/readyz")

        assert ready.status_code == 503
        assert ready.json() == {
            "status": "not_ready",
            "checks": {"session_store": "error", "rate_limiter": "ok"},
        }

    async def test_readyz_reports_configured_cloud_workspace_provider_when_ready(
        self, client, monkeypatch
    ):
        seen_configs: list[dict[str, object]] = []
        to_thread_calls: list[tuple[Callable[..., bool], tuple[object, ...]]] = []

        def fake_readiness(config: dict[str, object]) -> bool:
            seen_configs.append(dict(config))
            return True

        async def fake_to_thread(func: Callable[..., bool], *args: object) -> bool:
            to_thread_calls.append((func, args))
            return func(*args)

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/srv/coding-agent/workspaces",
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.cloud_workspace_ready_from_config",
            fake_readiness,
            raising=False,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.asyncio.to_thread",
            fake_to_thread,
        )

        ready = await client.get("/readyz")

        assert ready.status_code == 200
        assert ready.json() == {
            "status": "ready",
            "checks": {
                "session_store": "ok",
                "rate_limiter": "ok",
                "cloud_workspace": "ok",
            },
        }
        assert seen_configs == [
            {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/srv/coding-agent/workspaces",
            }
        ]
        assert to_thread_calls == [
            (
                fake_readiness,
                (
                    {
                        "enabled": True,
                        "provider": "docker",
                        "workspace_root": "/srv/coding-agent/workspaces",
                    },
                ),
            )
        ]

    async def test_readyz_returns_503_when_cloud_workspace_provider_unhealthy(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/srv/coding-agent/workspaces",
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.cloud_workspace_ready_from_config",
            lambda config: False,
            raising=False,
        )

        ready = await client.get("/readyz")

        assert ready.status_code == 503
        assert ready.json() == {
            "status": "not_ready",
            "checks": {
                "session_store": "ok",
                "rate_limiter": "ok",
                "cloud_workspace": "error",
            },
        }

    async def test_readyz_returns_503_when_enabled_cloud_workspace_config_is_invalid(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"enabled": True},
        )

        ready = await client.get("/readyz")

        assert ready.status_code == 503
        assert ready.json() == {
            "status": "not_ready",
            "checks": {
                "session_store": "ok",
                "rate_limiter": "ok",
                "cloud_workspace": "error",
            },
        }

    async def test_create_session_uses_real_provider_by_default(self, client):
        response = await client.post("/sessions", json={})
        session_id = response.json()["session_id"]

        session = session_manager.get_session(session_id)

        assert session.provider is None

    async def test_create_session_stores_local_run_target_by_default(self, client):
        response = await client.post("/sessions", json={})

        session = session_manager.get_session(response.json()["session_id"])

        assert isinstance(session.default_run_target.workspace, LocalPathWorkspaceRef)
        assert session.default_run_target.workspace.path == str(Path.cwd().resolve())

    async def test_create_session_stores_local_run_target_with_repo_path(
        self, client, tmp_path
    ):
        response = await client.post(
            "/sessions",
            json={"repo_path": str(tmp_path)},
        )

        session = session_manager.get_session(response.json()["session_id"])

        assert isinstance(session.default_run_target.workspace, LocalPathWorkspaceRef)
        assert session.default_run_target.workspace.path == str(tmp_path.resolve())
        assert session.repo_path == tmp_path.resolve()

    async def test_http_create_session_rejects_execution_binding(self, client):
        response = await client.post(
            "/sessions",
            json={
                "execution_binding": {
                    "kind": "cloud",
                    "workspace_url": "https://workspace.example.com",
                    "workspace_id": "ws-123",
                }
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "extra_forbidden"
        assert response.json()["detail"][0]["loc"] == ["body", "execution_binding"]

    async def test_http_create_session_rejects_empty_default_run_target(self, client):
        response = await client.post(
            "/sessions",
            json={"default_run_target": {}},
        )

        assert response.status_code == 400
        assert "workspace must be an object" in response.json()["detail"]

    async def test_http_create_session_stores_cloud_run_target(self, client):
        response = await client.post(
            "/sessions",
            json={"run_target": _cloud_run_target_payload()},
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert isinstance(session.default_run_target.workspace, CloudWorkspaceRef)
        assert (
            session.default_run_target.workspace.workspace_url
            == "https://workspace.example.com"
        )
        assert session.default_run_target.workspace.workspace_id == "ws-123"

    @pytest.mark.parametrize("target_field", ["run_target", "default_run_target"])
    async def test_http_create_session_locks_user_run_target_to_default_local_sandbox(
        self,
        client,
        monkeypatch,
        tmp_path,
        target_field,
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        response = await client.post(
            "/sessions",
            json={target_field: _unsafe_local_run_target_payload(tmp_path)},
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert session.default_run_target.isolation == IsolationPolicy(
            kind="default_local_sandbox"
        )

    @pytest.mark.parametrize("target_field", ["run_target", "default_run_target"])
    async def test_http_create_session_locks_unauthenticated_run_target_to_default_local_sandbox(
        self,
        client,
        monkeypatch,
        tmp_path,
        target_field,
    ):
        monkeypatch.delenv("CODING_AGENT_SERVER_CONFIG", raising=False)
        monkeypatch.setattr(settings, "http_api_key", None)

        response = await client.post(
            "/sessions",
            json={target_field: _unsafe_local_run_target_payload(tmp_path)},
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert session.default_run_target.isolation == IsolationPolicy(
            kind="default_local_sandbox"
        )

    @pytest.mark.parametrize("target_field", ["run_target", "default_run_target"])
    async def test_http_create_session_allows_admin_dev_unsafe_disabled_run_target(
        self,
        client,
        monkeypatch,
        tmp_path,
        target_field,
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        response = await client.post(
            "/sessions",
            json={target_field: _unsafe_local_run_target_payload(tmp_path)},
            headers={"Authorization": "Bearer admin-token"},
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert session.default_run_target.isolation == IsolationPolicy(
            kind="dev_unsafe_disabled",
            network="unrestricted",
            filesystem="unrestricted",
            secrets="unrestricted",
        )

    @pytest.mark.parametrize("target_field", ["run_target", "default_run_target"])
    async def test_http_create_session_preserves_user_safe_isolation(
        self,
        client,
        monkeypatch,
        tmp_path,
        target_field,
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        response = await client.post(
            "/sessions",
            json={target_field: _cloud_run_target_payload()},
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert session.default_run_target.isolation == IsolationPolicy(
            kind="provider_sandbox",
            network="provider_managed",
            filesystem="provider_managed",
            secrets="provider_managed",
        )

    async def test_http_create_session_round_trips_workspace_provider_metadata(
        self, client
    ):
        response = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_provider="docker",
                    provider_instance_id="docker-host-a",
                )
            },
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert isinstance(session.default_run_target.workspace, CloudWorkspaceRef)
        assert session.default_run_target.workspace.workspace_provider == "docker"
        assert (
            session.default_run_target.workspace.provider_instance_id == "docker-host-a"
        )

    @pytest.mark.parametrize("field", ["workspace_provider", "provider_instance_id"])
    async def test_http_create_session_rejects_blank_workspace_provider_metadata(
        self, client, field
    ):
        run_target = _cloud_run_target_payload()
        workspace = cast(dict[str, object], run_target["workspace"])
        workspace[field] = "   "

        response = await client.post(
            "/sessions",
            json={"run_target": run_target},
        )

        assert response.status_code == 400

    async def test_http_create_session_provisions_docker_cloud_workspace(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
            lambda provider_config, binding: None,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
                **_test_runtime_profile_config(),
            },
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 200
        session = session_manager.get_session(response.json()["session_id"])
        assert isinstance(session.default_run_target.workspace, CloudWorkspaceRef)
        expected_origin = {
            "channel": "http",
            "placement_kind": "cloud_workspace",
            "executor_kind": "managed_pool",
            "workspace_source_kind": "docker",
            "workspace_provider": "docker",
            "workspace_root_ref": str(tmp_path),
        }
        assert session.origin == expected_origin
        assert (tmp_path / session.default_run_target.workspace.workspace_id).is_dir()

        info_response = await client.get(f"/sessions/{response.json()['session_id']}")
        assert info_response.status_code == 200
        assert info_response.json()["origin"] == expected_origin

    async def test_create_session_rejects_conflicting_workspace_target_inputs(
        self, client
    ):
        response = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(),
                "workspace_source": {"kind": "docker"},
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "run_target and workspace_source cannot be set together"
        )

    async def test_create_session_rejects_workspace_provisioning_when_disabled(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {},
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "cloud workspace provisioning requires cloud_workspace.enabled=true"
        )

    async def test_create_session_surfaces_redacted_setup_failure_detail(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
                **_test_runtime_profile_config(),
            },
        )

        def fail_setup(config: dict[str, object], source: dict[str, object]):
            del config, source
            exc = subprocess.CalledProcessError(
                returncode=42,
                cmd=["docker", "run", "--name", "secret-container"],
            )
            exc.add_note("setup phase stdout:\n[REDACTED]\n")
            exc.add_note("setup phase stderr:\nsetup failed intentionally\n")
            raise exc

        monkeypatch.setattr(
            "coding_agent.server.http_server.provision_cloud_binding_from_config",
            fail_setup,
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail == (
            "setup phase failed with exit code 42\n"
            "setup phase stdout:\n[REDACTED]\n"
            "\n"
            "setup phase stderr:\nsetup failed intentionally\n"
        )
        assert "docker run" not in detail
        assert "secret-container" not in detail

    async def test_create_session_provisions_git_workspace_source(
        self, client, monkeypatch, tmp_path
    ):
        binding = CloudWorkspaceRef(
            workspace_url="docker://agent-ws-git/workspace",
            workspace_id="ws-git",
        )
        captured_sources: list[dict[str, object]] = []
        captured_configs: list[dict[str, object]] = []

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
                **_test_runtime_profile_config(),
                "remote_sources": {"git": {"allowed_hosts": ["github.com"]}},
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.provision_cloud_binding_from_config",
            lambda config, source: (
                captured_configs.append(dict(config))
                or captured_sources.append(dict(source))
                or binding
            ),
        )

        response = await client.post(
            "/sessions",
            json={
                "workspace_source": {
                    "kind": "git",
                    "remote_url": "https://github.com/org/repo.git",
                    "base_ref": "main",
                    "base_sha": "abc123",
                    "runtime_profile": "universal",
                }
            },
        )

        assert response.status_code == 200
        assert captured_sources == [
            {
                "kind": "git",
                "remote_url": "https://github.com/org/repo.git",
                "base_ref": "main",
                "base_sha": "abc123",
                "runtime_profile": "universal",
            }
        ]
        assert captured_configs[0]["remote_sources"] == {
            "git": {"allowed_hosts": ["github.com"]}
        }
        session = session_manager.get_session(response.json()["session_id"])
        assert session.default_run_target.workspace == binding
        assert session.origin is not None
        assert session.origin["channel"] == "http"
        assert session.origin["placement_kind"] == "cloud_workspace"
        assert session.origin["workspace_source_kind"] == "git"

    async def test_create_session_rolls_back_provisioned_workspace_on_failure(
        self, client, monkeypatch
    ):
        binding = CloudWorkspaceRef(
            workspace_url="docker://agent-ws-orphan/workspace",
            workspace_id="ws-orphan",
        )
        cleaned: list[CloudWorkspaceRef] = []

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/tmp/unused",
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.provision_cloud_binding_from_config",
            lambda config, source: binding,
        )

        async def fail_create_session(**kwargs):
            del kwargs
            raise RuntimeError("session store unavailable")

        monkeypatch.setattr(session_manager, "create_session", fail_create_session)
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 500
        assert cleaned == [binding]

    async def test_create_session_rolls_back_provisioned_workspace_on_non_runtime_failure(
        self, client, monkeypatch
    ):
        binding = CloudWorkspaceRef(
            workspace_url="docker://agent-ws-orphan/workspace",
            workspace_id="ws-orphan-nonruntime",
        )
        cleaned: list[CloudWorkspaceRef] = []

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/tmp/unused",
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.provision_cloud_binding_from_config",
            lambda config, source: binding,
        )

        async def fail_create_session(**kwargs):
            del kwargs
            raise KeyError("owner store unavailable")

        monkeypatch.setattr(session_manager, "create_session", fail_create_session)
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 500
        assert cleaned == [binding]

    async def test_create_session_keeps_original_failure_when_rollback_cleanup_fails(
        self, client, monkeypatch
    ):
        binding = CloudWorkspaceRef(
            workspace_url="docker://agent-ws-orphan/workspace",
            workspace_id="ws-orphan-cleanup-fails",
        )

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/tmp/unused",
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.provision_cloud_binding_from_config",
            lambda config, source: binding,
        )

        async def fail_create_session(**kwargs):
            del kwargs
            raise RuntimeError("session store unavailable")

        def fail_cleanup(target_binding):
            del target_binding
            raise RuntimeError("cleanup unavailable")

        monkeypatch.setattr(session_manager, "create_session", fail_create_session)
        monkeypatch.setattr(
            "coding_agent.server.http_server._cleanup_provisioned_cloud_binding",
            fail_cleanup,
        )

        response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "session store unavailable"

    async def test_create_session_rolls_back_provisioned_workspace_on_cancellation(
        self, client, monkeypatch
    ):
        binding = CloudWorkspaceRef(
            workspace_url="docker://agent-ws-cancelled/workspace",
            workspace_id="ws-cancelled",
        )
        cleaned: list[CloudWorkspaceRef] = []

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": "/tmp/unused",
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.provision_cloud_binding_from_config",
            lambda config, source: binding,
        )

        async def cancel_create_session(**kwargs):
            del kwargs
            raise asyncio.CancelledError

        monkeypatch.setattr(session_manager, "create_session", cancel_create_session)
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        with pytest.raises(asyncio.CancelledError):
            await client.post(
                "/sessions",
                json={"workspace_source": {"kind": "docker"}},
            )

        assert cleaned == [binding]

    async def test_close_session_cleans_up_provisioned_workspace_on_delete(
        self, client, monkeypatch, tmp_path
    ):
        cleaned: list[CloudWorkspaceRef] = []

        monkeypatch.setattr(
            "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
            lambda provider_config, binding: None,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
                **_test_runtime_profile_config(),
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        create_response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )

        assert create_response.status_code == 200
        binding = session_manager.get_session(
            create_response.json()["session_id"]
        ).default_run_target.workspace
        assert isinstance(binding, CloudWorkspaceRef)

        close_response = await client.delete(
            f"/sessions/{create_response.json()['session_id']}"
        )

        assert close_response.status_code == 200
        assert cleaned == [binding]

    async def test_close_session_cleans_up_when_new_provisioning_is_disabled(
        self, client, monkeypatch, tmp_path
    ):
        cleaned: list[CloudWorkspaceRef] = []
        config_enabled = True

        monkeypatch.setattr(
            "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
            lambda provider_config, binding: None,
        )

        def cloud_workspace_config():
            return {
                "enabled": config_enabled,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
                **_test_runtime_profile_config(),
            }

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            cloud_workspace_config,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_binding_from_config",
            lambda config, target_binding: cleaned.append(target_binding),
        )

        create_response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )
        binding = session_manager.get_session(
            create_response.json()["session_id"]
        ).default_run_target.workspace
        assert isinstance(binding, CloudWorkspaceRef)
        config_enabled = False

        close_response = await client.delete(
            f"/sessions/{create_response.json()['session_id']}"
        )

        assert close_response.status_code == 200
        assert cleaned == [binding]

    async def test_create_session_accepts_runtime_provider_metadata(self, client):
        response = await client.post(
            "/sessions",
            json={
                "provider": "anthropic",
                "model": "claude-test-http",
                "base_url": "http://llm.local/v1",
                "max_steps": 9,
            },
        )
        assert response.status_code == 200

        session = session_manager.get_session(response.json()["session_id"])

        assert session.provider is None
        assert session.provider_name == "anthropic"
        assert session.model_name == "claude-test-http"
        assert session.base_url == "http://llm.local/v1"
        assert session.max_steps == 9

        info_response = await client.get(f"/sessions/{response.json()['session_id']}")
        assert info_response.status_code == 200
        info = info_response.json()
        assert info["provider_name"] == "anthropic"
        assert info["model_name"] == "claude-test-http"
        assert info["base_url"] == "http://llm.local/v1"
        assert info["max_steps"] == 9

    async def test_create_session_accepts_deepseek_runtime_provider(self, client):
        response = await client.post(
            "/sessions",
            json={
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
            },
        )
        assert response.status_code == 200

        session = session_manager.get_session(response.json()["session_id"])

        assert session.provider is None
        assert session.provider_name == "deepseek"
        assert session.model_name == "deepseek-v4-pro"

        info_response = await client.get(f"/sessions/{response.json()['session_id']}")
        assert info_response.status_code == 200
        info = info_response.json()
        assert info["provider_name"] == "deepseek"
        assert info["model_name"] == "deepseek-v4-pro"

    async def test_create_session_accepts_stepfun_runtime_provider(self, client):
        response = await client.post(
            "/sessions",
            json={
                "provider": "stepfun",
                "model": "step-3.7-flash",
            },
        )
        assert response.status_code == 200

        session = session_manager.get_session(response.json()["session_id"])

        assert session.provider is None
        assert session.provider_name == "stepfun"
        assert session.model_name == "step-3.7-flash"

        info_response = await client.get(f"/sessions/{response.json()['session_id']}")
        assert info_response.status_code == 200
        info = info_response.json()
        assert info["provider_name"] == "stepfun"
        assert info["model_name"] == "step-3.7-flash"

    async def test_create_session_uses_explicit_server_agent_defaults(
        self, client, monkeypatch, tmp_path
    ):
        config_path = tmp_path / "server.toml"
        config_path.write_text(
            """
[agent]
name = "test-agent"
model = "deepseek-v4-pro"
provider = "deepseek"
max_turns = 17
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(config_path))

        response = await client.post("/sessions", json={})
        assert response.status_code == 200

        session = session_manager.get_session(response.json()["session_id"])

        assert session.provider_name == "deepseek"
        assert session.model_name == "deepseek-v4-pro"
        assert session.max_steps == 17

    async def test_create_session_rejects_invalid_runtime_provider(self, client):
        response = await client.post(
            "/sessions",
            json={"provider": "not-a-provider", "model": "test-model"},
        )

        assert response.status_code == 422

    async def test_send_prompt_reports_managed_pool_unavailable_for_provisioned_workspace(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(
            "coding_agent.environment.docker_workspace_provider._start_docker_workspace_container",
            lambda provider_config, binding: None,
        )

        class _CreateAgentCapture:
            def __init__(self) -> None:
                self.environment: CloudEnvironment | None = None
                self.session_id: str | None = None
                self.workspace_root: object | None = None

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "workspace_root": str(tmp_path),
                "container_name_prefix": "agent-",
                **_test_runtime_profile_config(),
            },
        )
        object.__setattr__(
            session_manager._runtime_environment_resolver_service,
            "cloud_client_factory",
            http_server.cloud_client_factory_from_config(
                http_server._load_cloud_workspace_config()
            ),
        )

        captured = _CreateAgentCapture()

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del pipeline, ctx
                self._consumer = consumer

            async def run_turn(self, prompt: str) -> None:
                del prompt
                assert captured.session_id is not None
                await self._consumer.emit(
                    TurnEnd(
                        session_id=captured.session_id,
                        agent_id="",
                        turn_id="turn-provisioned",
                        completion_status=CompletionStatus.COMPLETED,
                    )
                )

        def fake_create_agent(**kwargs):
            raise AssertionError(f"managed pool prompt must not bootstrap: {kwargs!r}")

        monkeypatch.setattr(session_manager, "_create_agent", fake_create_agent)
        monkeypatch.setattr(
            "coding_agent.server.session_manager.PipelineAdapter", FakeAdapter
        )

        create_response = await client.post(
            "/sessions",
            json={"workspace_source": {"kind": "docker"}},
        )
        session_id = create_response.json()["session_id"]

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append(sse.event)
                if sse.event == "Error":
                    break

        assert captured.environment is None
        session = session_manager.get_session(session_id)
        binding = session.default_run_target.workspace
        assert isinstance(binding, CloudWorkspaceRef)
        assert events[-1] == "TurnEnd"
        assert session.turn_status == "failed"
        assert session.last_failure_details is not None
        assert "managed_pool" in session.last_failure_details

    def test_build_session_manager_enables_owner_store_for_pg_http_sessions(
        self, monkeypatch
    ):
        class FakeOwnerStore:
            def __init__(self, *, pg_pool) -> None:
                self.pg_pool = pg_pool

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_storage_config",
            lambda: {
                "http_session_backend": "pg",
                "tape_backend": "pg",
                "checkpoint_backend": "pg",
                "dsn": "postgresql://example",
                "owner_id": "pod-a",
                "fencing_token": 9,
                "owner_lease_seconds": 40.0,
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.SessionOwnerStore",
            FakeOwnerStore,
        )

        manager = _build_session_manager()
        try:
            assert isinstance(manager._owner_store, FakeOwnerStore)
            assert manager._owner_store.pg_pool is manager._pg_pool
            assert manager._owner_id == "pod-a"
            assert manager._fencing_token == 9
            assert manager.owner_lease_seconds == 40.0
        finally:
            asyncio.run(manager.close())

    def test_build_session_manager_enables_pg_durable_fencing_for_full_pg_storage(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_storage_config",
            lambda: {
                "http_session_backend": "pg",
                "tape_backend": "pg",
                "checkpoint_backend": "pg",
                "runtime_backend": "pg",
                "dsn": "postgresql://example",
                "owner_id": "pod-a",
                "fencing_token": 9,
                "owner_lease_seconds": 40.0,
            },
        )

        manager = _build_session_manager()
        try:
            assert manager._pg_durable_store is not None
            assert type(manager._tape_store).__name__ == "FencedPGTapeStore"
            assert (
                type(manager._checkpoint_service._store).__name__
                == "FencedPGCheckpointStore"
            )
            assert type(manager._runtime_store).__name__ == "FencedPGRuntimeStore"
        finally:
            asyncio.run(manager.close())

    @pytest.mark.parametrize("fencing_token", [None, 0, -1, "9"])
    def test_build_session_manager_requires_explicit_positive_fencing_token_for_pg_http_sessions(
        self, monkeypatch, fencing_token
    ):
        storage_config = {
            "http_session_backend": "pg",
            "tape_backend": "pg",
            "checkpoint_backend": "pg",
            "dsn": "postgresql://example",
            "owner_id": "pod-a",
            "owner_lease_seconds": 40.0,
        }
        if fencing_token is not None:
            storage_config["fencing_token"] = fencing_token
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_storage_config",
            lambda: storage_config,
        )

        with pytest.raises(ValueError, match="storage.fencing_token"):
            _build_session_manager()

    def test_build_session_manager_enables_owner_store_for_local_sqlite_bundle(
        self, monkeypatch, tmp_path
    ):
        local_path = tmp_path / "local.sqlite3"
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_storage_config",
            lambda: {
                "http_session_backend": "sqlite",
                "http_session_path": str(local_path),
                "tape_backend": "sqlite",
                "tape_path": str(local_path),
                "checkpoint_backend": "sqlite",
                "checkpoint_path": str(local_path),
                "runtime_backend": "sqlite",
                "runtime_path": str(local_path),
                "owner_id": "server-a",
                "owner_lease_seconds": 40.0,
            },
        )

        manager = _build_session_manager()
        try:
            assert manager._owner_store is not None
            assert type(manager._owner_store).__name__ == "SQLiteSessionOwnerStore"
            assert manager._owner_id == "server-a"
            assert manager.owner_lease_seconds == 40.0
            assert manager._local_durable_store is not None
        finally:
            asyncio.run(manager.close())

    def test_build_session_manager_enables_owner_store_for_default_sqlite_bundle(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_storage_config",
            lambda: {},
        )

        manager = _build_session_manager()
        try:
            assert manager._owner_store is not None
            assert type(manager._owner_store).__name__ == "SQLiteSessionOwnerStore"
            assert manager._owner_id is not None
            assert manager._local_durable_store is not None
        finally:
            asyncio.run(manager.close())

    def test_build_session_manager_normalizes_paths_local_for_sqlite_owner_store(
        self,
        monkeypatch,
        tmp_path,
    ):
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_storage_config",
            lambda: {
                "paths": {"local": "~/bundle/local.sqlite3"},
                "http_session_backend": "sqlite",
                "tape_backend": "sqlite",
                "checkpoint_backend": "sqlite",
                "runtime_backend": "sqlite",
                "owner_id": "server-a",
                "owner_lease_seconds": 40.0,
            },
        )

        manager = _build_session_manager()
        try:
            expected_path = home / "bundle" / "local.sqlite3"
            assert manager._owner_store is not None
            assert manager._local_durable_store is not None
            assert manager._owner_store._path.resolve() == expected_path.resolve()
            assert (
                manager._local_durable_store._path.resolve() == expected_path.resolve()
            )
            assert not Path("./~").exists()
        finally:
            asyncio.run(manager.close())

    def test_build_session_manager_enables_owner_store_for_equivalent_local_sqlite_bundle_paths(
        self, monkeypatch, tmp_path
    ):
        local_path = tmp_path / "local.sqlite3"
        equivalent_parent = tmp_path / "nested" / ".." / "local.sqlite3"
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_storage_config",
            lambda: {
                "http_session_backend": "sqlite",
                "http_session_path": str(local_path),
                "tape_backend": "sqlite",
                "tape_path": str(equivalent_parent),
                "checkpoint_backend": "sqlite",
                "checkpoint_path": str(local_path.resolve()),
                "runtime_backend": "sqlite",
                "runtime_path": str(equivalent_parent),
                "owner_id": "server-a",
                "owner_lease_seconds": 40.0,
            },
        )

        manager = _build_session_manager()
        try:
            assert manager._owner_store is not None
            assert type(manager._owner_store).__name__ == "SQLiteSessionOwnerStore"
            assert manager._local_durable_store is not None
        finally:
            asyncio.run(manager.close())

    def test_build_session_manager_does_not_enable_owner_store_for_non_bundle_storage(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_storage_config",
            lambda: {
                "http_session_backend": "redis",
                "tape_backend": "jsonl",
                "checkpoint_backend": "fs",
                "runtime_backend": "jsonl",
            },
        )

        manager = _build_session_manager()

        try:
            assert manager._owner_store is None
        finally:
            asyncio.run(manager.close())

    async def test_renew_owner_leases_exits_when_owner_leases_are_not_configured(
        self, monkeypatch
    ):
        events: list[str] = []

        async def fail_sleep(delay: float) -> None:
            del delay
            events.append("sleep")
            raise AssertionError("renew loop should not sleep without owner leases")

        async def fail_renew_owner_leases() -> None:
            events.append("renew")
            raise AssertionError("renew loop should not renew without owner leases")

        session_manager.configure_owner_leases(
            owner_store=None,
            owner_id=None,
            fencing_token=None,
        )
        monkeypatch.setattr(
            session_manager,
            "renew_owner_leases",
            fail_renew_owner_leases,
        )
        monkeypatch.setattr("coding_agent.server.http_server.asyncio.sleep", fail_sleep)

        await _renew_owner_leases()

        assert events == []

    async def test_renew_owner_leases_renews_current_sessions(self, monkeypatch):
        renew_calls: list[tuple[str, str, float, int, int]] = []

        class FakeOwnerStore:
            def __init__(self) -> None:
                self._owners = {
                    "session-a": SessionOwnerRecord(
                        owner_id="pod-a",
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=40),
                        fencing_token=9,
                    ),
                    "session-b": SessionOwnerRecord(
                        owner_id="pod-a",
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=40),
                        fencing_token=9,
                    ),
                }

            async def acquire(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                fencing_token: int = 1,
            ) -> bool:
                del session_id, owner_id, lease_seconds, fencing_token
                return True

            async def renew(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                new_fencing_token: int = 2,
                current_fencing_token: int = 1,
            ) -> bool:
                renew_calls.append(
                    (
                        session_id,
                        owner_id,
                        lease_seconds,
                        new_fencing_token,
                        current_fencing_token,
                    )
                )
                return True

            async def release(
                self,
                session_id: str,
                owner_id: str,
                fencing_token: int,
            ) -> bool:
                del session_id, owner_id, fencing_token
                return True

            async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
                return self._owners.get(session_id)

        sleep_calls = 0

        async def fake_sleep(delay: float) -> None:
            nonlocal sleep_calls
            assert delay == 20.0
            sleep_calls += 1
            if sleep_calls == 1:
                raise asyncio.CancelledError

        async def fake_list_sessions_async() -> list[str]:
            return ["session-a", "session-b"]

        session_manager.configure_owner_leases(
            owner_store=FakeOwnerStore(),
            owner_id="pod-a",
            fencing_token=9,
            owner_lease_seconds=40.0,
        )
        monkeypatch.setattr(
            session_manager,
            "list_sessions_async",
            fake_list_sessions_async,
        )
        monkeypatch.setattr("coding_agent.server.http_server.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await _renew_owner_leases()

        assert renew_calls == [
            ("session-a", "pod-a", 40.0, 9, 9),
            ("session-b", "pod-a", 40.0, 9, 9),
        ]

    async def test_renew_owner_leases_logs_and_continues_after_failure(
        self, monkeypatch, caplog
    ):
        renew_calls = 0

        async def fake_renew_owner_leases() -> None:
            nonlocal renew_calls
            renew_calls += 1
            if renew_calls == 1:
                raise RuntimeError("database temporarily unavailable")

        sleep_calls = 0

        async def fake_sleep(delay: float) -> None:
            nonlocal sleep_calls
            assert delay == 15.0
            sleep_calls += 1
            if sleep_calls == 2:
                raise asyncio.CancelledError

        session_manager.configure_owner_leases(
            owner_store=cast(Any, object()),
            owner_id="pod-a",
            fencing_token=9,
            owner_lease_seconds=30.0,
        )
        monkeypatch.setattr(
            session_manager,
            "renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr("coding_agent.server.http_server.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await _renew_owner_leases()

        assert renew_calls == 2
        assert "Error renewing owner leases" in caplog.text

    async def test_renew_owner_leases_renews_before_first_sleep(self, monkeypatch):
        events: list[str] = []

        async def fake_renew_owner_leases() -> None:
            events.append("renew")

        async def fake_sleep(delay: float) -> None:
            assert delay == 15.0
            events.append("sleep")
            raise asyncio.CancelledError

        session_manager.configure_owner_leases(
            owner_store=cast(Any, object()),
            owner_id="pod-a",
            fencing_token=9,
            owner_lease_seconds=30.0,
        )
        monkeypatch.setattr(
            session_manager,
            "renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr("coding_agent.server.http_server.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await _renew_owner_leases()

        assert events == ["renew", "sleep"]


class TestRuntimeConfigUpdate:
    """Tests for runtime config update endpoint."""

    async def test_approval_only_updates_live_plugin_without_runtime_replacement(
        self, client, monkeypatch
    ):
        response = await client.post("/sessions", json={"approval_policy": "auto"})
        session_id = response.json()["session_id"]
        await session_manager.ensure_session_runtime(session_id)
        session = session_manager.get_session(session_id)
        plugin = session.runtime_pipeline._registry.get("approval")
        assert isinstance(plugin, ApprovalPlugin)
        assert isinstance(
            plugin.approve_tool_call(tool_name="bash_run", arguments={}),
            AskUser,
        )

        async def fail_replace_session_runtime_config(*args, **kwargs):
            del args, kwargs
            raise AssertionError("approval update should not replace runtime")

        monkeypatch.setattr(
            session_manager,
            "replace_session_runtime_config",
            fail_replace_session_runtime_config,
        )

        update = await client.post(
            f"/sessions/{session_id}/runtime-config",
            json={"approval": "yolo"},
        )

        assert update.status_code == 200
        assert session.approval_policy == ApprovalPolicy.YOLO
        assert isinstance(
            plugin.approve_tool_call(tool_name="bash_run", arguments={}),
            Approve,
        )

    async def test_mixed_runtime_replacement_failure_leaves_approval_and_thinking_unchanged(
        self, client, monkeypatch
    ):
        response = await client.post("/sessions", json={"approval_policy": "auto"})
        session_id = response.json()["session_id"]
        await session_manager.ensure_session_runtime(session_id)
        session = session_manager.get_session(session_id)
        original_thinking_config = dict(session.thinking_config)
        persisted_before = session_manager._store.load(session_id)
        assert persisted_before is not None
        assert persisted_before["approval_policy"] == "auto"

        plugin = session.runtime_pipeline._registry.get("approval")
        assert isinstance(plugin, ApprovalPlugin)
        assert isinstance(
            plugin.approve_tool_call(tool_name="bash_run", arguments={}),
            AskUser,
        )

        replace_calls: list[dict[str, object]] = []

        async def fail_replace_session_runtime_config(*args, **kwargs):
            replace_calls.append(dict(kwargs))
            raise RuntimeError("runtime replacement failed")

        monkeypatch.setattr(
            session_manager,
            "replace_session_runtime_config",
            fail_replace_session_runtime_config,
        )

        update = await client.post(
            f"/sessions/{session_id}/runtime-config",
            json={
                "approval": "yolo",
                "thinking": {"enabled": False, "effort": "high"},
                "model": "replacement-model",
            },
        )

        assert update.status_code == 500
        assert replace_calls == [
            {
                "model_name": "replacement-model",
                "provider_name": None,
                "base_url": http_server.UNSET,
            }
        ]
        assert session.approval_policy == ApprovalPolicy.AUTO
        assert session.thinking_config == original_thinking_config
        assert isinstance(
            plugin.approve_tool_call(tool_name="bash_run", arguments={}),
            AskUser,
        )
        persisted_after = session_manager._store.load(session_id)
        assert persisted_after is not None
        assert persisted_after["approval_policy"] == "auto"

    async def test_approval_null_is_rejected(self, client):
        response = await client.post("/sessions", json={})
        session_id = response.json()["session_id"]

        update = await client.post(
            f"/sessions/{session_id}/runtime-config",
            json={"approval": None},
        )

        assert update.status_code == 422
        assert "approval may not be null" in update.text

    async def test_approval_update_rejects_turn_in_progress(self, client):
        response = await client.post("/sessions", json={})
        session_id = response.json()["session_id"]
        session = session_manager.get_session(session_id)
        session.turn_in_progress = True

        update = await client.post(
            f"/sessions/{session_id}/runtime-config",
            json={"approval": "interactive"},
        )

        assert update.status_code == 409
        assert update.json()["detail"] == "Turn already in progress"


class TestMemoryReviewTransitions:
    async def test_list_reviews_returns_session_candidates_by_status_without_legacy(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store = _direct_review_runtime_config()
        current = _review_candidate(
            "memory-current",
            title="Current auth memory",
            summary="Current JWT middleware convention",
        )
        accepted = _review_candidate(
            "memory-accepted",
            title="Accepted auth memory",
            summary="Accepted JWT middleware convention",
        )
        other = _review_candidate(
            "memory-other",
            session_id="other-session",
            tape_id="other-tape",
        )
        legacy = _review_candidate(
            "memory-legacy",
            title="Legacy auth memory",
            summary="Legacy JWT middleware convention",
            session_id=None,
            tape_id=None,
            profile=None,
        )
        review_store.add_candidate(current)
        review_store.add_candidate(accepted)
        review_store.add_candidate(other)
        review_store.add_candidate(legacy)
        review_store.accept_candidate_for_session(
            "memory-review-session",
            "memory-accepted",
            reason="Already reviewed",
        )
        ensure_calls = _install_memory_review_runtime(
            monkeypatch,
            "memory-review-session",
            _runtime_ctx(config),
        )

        response = await client.get(
            "/sessions/memory-review-session/memory/reviews?status=candidate"
        )

        assert response.status_code == 200
        assert ensure_calls == ["memory-review-session"]
        assert response.json() == [
            {
                "candidate_id": "memory-current",
                "status": "candidate",
                "review_reason": None,
                "kind": "fact",
                "title": "Current auth memory",
                "summary": "Current JWT middleware convention",
                "scope": "topic:topic-auth",
                "tags": ["auth", "jwt"],
                "confidence": 0.8,
                "topic_id": "topic-auth",
                "session_id": "memory-review-session",
                "tape_id": "memory-review-tape",
            },
        ]

    async def test_list_reviews_uses_visible_session_auth_before_runtime(
        self,
        client: AsyncClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)
        session = register_session("memory-review-private-session")
        session.origin = {"owner_label": "owner:other"}

        async def fail_ensure_session_runtime(session_id: str) -> object:
            del session_id
            raise AssertionError("invisible session runtime was reached")

        monkeypatch.setattr(
            session_manager,
            "ensure_session_runtime",
            fail_ensure_session_runtime,
        )

        response = await client.get(
            "/sessions/memory-review-private-session/memory/reviews",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"

    async def test_accept_candidate_updates_semantic_index(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, _syncer = _semantic_review_runtime_config()
        candidate = _review_candidate(
            "memory-auth",
            title="Auth memory",
            summary="Use JWT middleware",
        )
        review_store.add_candidate(candidate)
        ensure_calls = _install_memory_review_runtime(
            monkeypatch,
            "memory-review-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-review-session/memory/reviews/memory-auth",
            json={"status": "accepted", "reason": "Useful for future auth work"},
        )

        assert response.status_code == 200
        assert ensure_calls == ["memory-review-session"]
        assert response.json() == {
            "candidate_id": "memory-auth",
            "status": "accepted",
            "review_reason": "Useful for future auth work",
            "kind": "fact",
            "title": "Auth memory",
            "scope": "topic:topic-auth",
            "tags": ["auth", "jwt"],
            "confidence": 0.8,
        }
        stored = review_store.load_memory("memory-auth")
        assert stored is not None
        assert stored.status == "accepted"
        assert stored.review_reason == "Useful for future auth work"
        expected_id = _reviewed_memory_doc_id(stored)
        assert await backend.list_ids() == [expected_id]
        hits = await backend.search("JWT", limit=5)
        assert [
            (hit.memory_id, hit.text, hit.metadata["candidate_id"]) for hit in hits
        ] == [
            (
                expected_id,
                "Auth memory\n\nUse JWT middleware",
                "memory-auth",
            )
        ]

    async def test_accept_legacy_candidate_without_session_scope(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, _syncer = _semantic_review_runtime_config()
        candidate = _review_candidate(
            "memory-legacy",
            title="Legacy auth memory",
            summary="Legacy JWT middleware convention",
            session_id=None,
            tape_id=None,
            profile=None,
        )
        review_store.add_candidate(candidate)
        _install_memory_review_runtime(
            monkeypatch,
            "memory-review-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-review-session/memory/reviews/memory-legacy",
            json={"status": "accepted", "reason": "Migrated legacy review"},
        )

        assert response.status_code == 200
        stored = review_store.load_memory("memory-legacy")
        assert stored is not None
        assert stored.status == "accepted"
        assert stored.review_reason == "Migrated legacy review"
        expected_id = _reviewed_memory_doc_id(stored)
        assert await backend.list_ids() == [expected_id]
        hits = await backend.search("JWT", limit=5)
        assert [(hit.memory_id, hit.metadata) for hit in hits] == [
            (
                expected_id,
                {
                    "kind": "accepted_reviewed_memory",
                    "memory_kind": "fact",
                    "candidate_id": "memory-legacy",
                    "memory_status": "accepted",
                    "scope": "topic:topic-auth",
                    "tags": ["auth", "jwt"],
                    "source_refs": ["memory:memory-legacy"],
                },
            )
        ]

    async def test_rejects_candidate_from_another_session_without_store_or_index_side_effects(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, syncer = _semantic_review_runtime_config()
        candidate = _review_candidate(
            "memory-other-session",
            session_id="other-session",
            tape_id="other-tape",
        )
        review_store.add_candidate(candidate)
        accepted_other = ReviewedMemoryRecord(candidate=candidate, status="accepted")
        await syncer.sync_reviewed_memory(accepted_other)
        _install_memory_review_runtime(
            monkeypatch,
            "memory-review-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-review-session/memory/reviews/memory-other-session",
            json={"status": "rejected", "reason": "Wrong session"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == (
            "memory candidate not found: memory-other-session"
        )
        stored = review_store.load_memory("memory-other-session")
        assert stored is not None
        assert stored.status == "candidate"
        assert await backend.list_ids() == [_reviewed_memory_doc_id(accepted_other)]

    async def test_transitions_only_candidate_from_path_session_when_id_is_shared(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, syncer = _semantic_review_runtime_config()
        current = _review_candidate("memory-shared")
        other = _review_candidate(
            "memory-shared",
            session_id="other-session",
            tape_id="other-tape",
        )
        review_store.add_candidate(current)
        review_store.add_candidate(other)
        accepted_other = ReviewedMemoryRecord(candidate=other, status="accepted")
        await syncer.sync_reviewed_memory(accepted_other)
        _install_memory_review_runtime(
            monkeypatch,
            "memory-review-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-review-session/memory/reviews/memory-shared",
            json={"status": "rejected", "reason": "Wrong for this session"},
        )

        assert response.status_code == 200
        current_record = review_store.load_memory_for_session(
            "memory-review-session",
            "memory-shared",
        )
        other_record = review_store.load_memory_for_session(
            "other-session",
            "memory-shared",
        )
        assert current_record is not None
        assert current_record.status == "rejected"
        assert other_record is not None
        assert other_record.status == "candidate"
        assert await backend.list_ids() == [_reviewed_memory_doc_id(accepted_other)]

    async def test_reject_candidate_deletes_stale_semantic_index(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, syncer = _semantic_review_runtime_config()
        candidate = _review_candidate("memory-auth", session_id="memory-reject-session")
        await syncer.sync_reviewed_memory(
            ReviewedMemoryRecord(candidate=candidate, status="accepted")
        )
        review_store.add_candidate(candidate)
        _install_memory_review_runtime(
            monkeypatch,
            "memory-reject-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-reject-session/memory/reviews/memory-auth",
            json={"status": "rejected", "reason": "Too narrow"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert response.json()["review_reason"] == "Too narrow"
        stored = review_store.load_memory("memory-auth")
        assert stored is not None
        assert stored.status == "rejected"
        assert await backend.list_ids() == []
        assert await backend.search("JWT", limit=5) == []

    async def test_missing_candidate_returns_404_without_index_side_effects(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, _review_store, backend, _syncer = _semantic_review_runtime_config()
        _install_memory_review_runtime(
            monkeypatch,
            "memory-missing-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-missing-session/memory/reviews/memory-missing",
            json={"status": "accepted", "reason": "Useful"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "memory candidate not found: memory-missing"
        assert await backend.list_ids() == []

    async def test_terminal_transition_returns_400_without_index_side_effects(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, syncer = _semantic_review_runtime_config()
        candidate = _review_candidate(
            "memory-accepted",
            session_id="memory-terminal-session",
        )
        review_store.add_candidate(candidate)
        accepted = review_store.accept_candidate("memory-accepted", reason="Useful")
        await syncer.sync_reviewed_memory(accepted)
        _install_memory_review_runtime(
            monkeypatch,
            "memory-terminal-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-terminal-session/memory/reviews/memory-accepted",
            json={"status": "rejected", "reason": "Too narrow"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "memory candidate memory-accepted is already accepted"
        )
        stored = review_store.load_memory("memory-accepted")
        assert stored is not None
        assert stored.status == "accepted"
        assert await backend.list_ids() == [_reviewed_memory_doc_id(stored)]

    async def test_same_status_terminal_transition_resyncs_existing_record(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, syncer = _semantic_review_runtime_config()
        candidate = _review_candidate(
            "memory-idempotent",
            session_id="memory-idempotent-session",
        )
        review_store.add_candidate(candidate)
        accepted = review_store.accept_candidate("memory-idempotent", reason="Useful")
        await syncer.sync_reviewed_memory(accepted)
        sync_calls: list[str] = []
        original_sync_reviewed_memory = syncer.sync_reviewed_memory

        async def record_sync_call(record: ReviewedMemoryRecord) -> object:
            sync_calls.append(record.status)
            return await original_sync_reviewed_memory(record)

        monkeypatch.setattr(syncer, "sync_reviewed_memory", record_sync_call)
        _install_memory_review_runtime(
            monkeypatch,
            "memory-idempotent-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-idempotent-session/memory/reviews/memory-idempotent",
            json={"status": "accepted", "reason": "Still useful"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        assert response.json()["review_reason"] == "Useful"
        assert sync_calls == ["accepted"]
        stored = review_store.load_memory("memory-idempotent")
        assert stored is not None
        assert stored.status == "accepted"
        assert stored.review_reason == "Useful"
        assert await backend.list_ids() == [_reviewed_memory_doc_id(stored)]

    async def test_unsafe_reason_returns_400_without_store_or_index_side_effects(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, _syncer = _semantic_review_runtime_config()
        review_store.add_candidate(
            _review_candidate(
                "memory-unsafe-reason",
                session_id="memory-unsafe-reason-session",
            )
        )
        _install_memory_review_runtime(
            monkeypatch,
            "memory-unsafe-reason-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-unsafe-reason-session/memory/reviews/memory-unsafe-reason",
            json={"status": "accepted", "reason": "stdout: raw output"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "review_reason contains forbidden raw content marker"
        )
        stored = review_store.load_memory("memory-unsafe-reason")
        assert stored is not None
        assert stored.status == "candidate"
        assert await backend.list_ids() == []

    async def test_semantic_disabled_updates_review_store_directly(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store = _direct_review_runtime_config()
        review_store.add_candidate(
            _review_candidate("memory-direct", session_id="memory-direct-session")
        )
        _install_memory_review_runtime(
            monkeypatch,
            "memory-direct-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-direct-session/memory/reviews/memory-direct",
            json={"status": "archived", "reason": "Superseded"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "candidate_id": "memory-direct",
            "status": "archived",
            "review_reason": "Superseded",
            "kind": "fact",
            "title": "Auth convention",
            "scope": "topic:topic-auth",
            "tags": ["auth", "jwt"],
            "confidence": 0.8,
        }
        stored = review_store.load_memory("memory-direct")
        assert stored is not None
        assert stored.status == "archived"
        assert stored.review_reason == "Superseded"
        assert "semantic_memory_review_sync_service" not in config

    async def test_semantic_sync_failure_returns_500(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        review_store = MemoryReviewStore()
        review_store.add_candidate(
            _review_candidate("memory-failure", session_id="memory-failure-session")
        )
        service = SemanticMemoryReviewSyncService(
            review_store=review_store,
            syncer=_FailingReviewedMemorySyncer(),
        )
        _install_memory_review_runtime(
            monkeypatch,
            "memory-failure-session",
            _runtime_ctx(
                {
                    "memory_review_store": review_store,
                    "semantic_memory_review_sync_service": service,
                }
            ),
        )

        response = await client.post(
            "/sessions/memory-failure-session/memory/reviews/memory-failure",
            json={"status": "accepted", "reason": "Useful"},
        )

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Semantic memory review sync failed: semantic backend unavailable"
        )
        stored = review_store.load_memory("memory-failure")
        assert stored is not None
        assert stored.status == "accepted"
        assert stored.review_reason == "Useful"

    async def test_semantic_transition_race_value_error_returns_400(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config, review_store, backend, _syncer = _semantic_review_runtime_config()
        review_store.add_candidate(
            _review_candidate("memory-race", session_id="memory-race-session")
        )

        def fail_accept_after_prevalidation(
            session_id: str,
            candidate_id: str,
            *,
            reason: str | None = None,
        ) -> ReviewedMemoryRecord:
            del session_id, candidate_id, reason
            raise ValueError("memory candidate memory-race is already rejected")

        monkeypatch.setattr(
            review_store,
            "accept_candidate_for_session",
            fail_accept_after_prevalidation,
        )
        _install_memory_review_runtime(
            monkeypatch,
            "memory-race-session",
            _runtime_ctx(config),
        )

        response = await client.post(
            "/sessions/memory-race-session/memory/reviews/memory-race",
            json={"status": "accepted", "reason": "Useful"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "memory candidate memory-race is already rejected"
        )
        stored = review_store.load_memory("memory-race")
        assert stored is not None
        assert stored.status == "candidate"
        assert await backend.list_ids() == []

    async def test_semantic_sync_value_error_after_transition_returns_500(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        review_store = MemoryReviewStore()
        review_store.add_candidate(
            _review_candidate(
                "memory-sync-value-error",
                session_id="memory-sync-value-error-session",
            )
        )
        service = SemanticMemoryReviewSyncService(
            review_store=review_store,
            syncer=_FailingReviewedMemorySyncer(
                ValueError("semantic document schema mismatch")
            ),
        )
        _install_memory_review_runtime(
            monkeypatch,
            "memory-sync-value-error-session",
            _runtime_ctx(
                {
                    "memory_review_store": review_store,
                    "semantic_memory_review_sync_service": service,
                }
            ),
        )

        response = await client.post(
            "/sessions/memory-sync-value-error-session/memory/reviews/memory-sync-value-error",
            json={"status": "accepted", "reason": "Useful"},
        )

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Semantic memory review sync failed: semantic document schema mismatch"
        )
        stored = review_store.load_memory("memory-sync-value-error")
        assert stored is not None
        assert stored.status == "accepted"
        assert stored.review_reason == "Useful"


class TestPromptStreaming:
    """Tests for prompt streaming endpoint."""

    async def test_prompt_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.post(
            "/sessions/nonexistent/prompt",
            json={"prompt": "test"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_prompt_missing_session_returns_404_before_owner_check(self, client):
        class FailingOwnerStore:
            async def acquire(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                fencing_token: int = 1,
            ) -> bool:
                del session_id, owner_id, lease_seconds, fencing_token
                return True

            async def renew(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                new_fencing_token: int = 2,
                current_fencing_token: int = 1,
            ) -> bool:
                del (
                    session_id,
                    owner_id,
                    lease_seconds,
                    new_fencing_token,
                    current_fencing_token,
                )
                return True

            async def release(
                self,
                session_id: str,
                owner_id: str,
                fencing_token: int,
            ) -> bool:
                del session_id, owner_id, fencing_token
                return True

            async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
                raise AssertionError(f"owner check should not run for {session_id}")

        session_manager.configure_owner_leases(
            owner_store=FailingOwnerStore(),
            owner_id="owner-a",
            fencing_token=7,
        )

        response = await client.post(
            "/sessions/missing-session/prompt",
            json={"prompt": "Hello"},
        )

        assert response.status_code == 404

    async def test_prompt_returns_409_for_stale_owner_before_streaming(self, client):
        class FakeOwnerStore:
            async def acquire(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                fencing_token: int = 1,
            ) -> bool:
                del session_id, owner_id, lease_seconds, fencing_token
                return True

            async def renew(
                self,
                session_id: str,
                owner_id: str,
                lease_seconds: float = 30.0,
                new_fencing_token: int = 2,
                current_fencing_token: int = 1,
            ) -> bool:
                del (
                    session_id,
                    owner_id,
                    lease_seconds,
                    new_fencing_token,
                    current_fencing_token,
                )
                return True

            async def release(
                self,
                session_id: str,
                owner_id: str,
                fencing_token: int,
            ) -> bool:
                del session_id, owner_id, fencing_token
                return True

            async def get_owner(self, session_id: str) -> SessionOwnerRecord | None:
                del session_id
                return SessionOwnerRecord(
                    owner_id="other-owner",
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                    fencing_token=8,
                )

        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session_manager.configure_owner_leases(
            owner_store=FakeOwnerStore(),
            owner_id="owner-a",
            fencing_token=7,
        )

        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "stale owner or fencing token rejected"
        assert not session_manager.get_session(session_id).turn_in_progress

    async def test_prompt_streaming_events(self, client):
        """Test that prompt returns SSE events."""
        # Create session first
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Send prompt and collect SSE events
        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "TurnEnd" and not events[-1]["data"]["agent_id"]:
                    break

        # Verify events
        assert len(events) > 0
        assert events[-1]["event"] == "TurnEnd"
        assert events[-1]["data"]["completion_status"] in {
            CompletionStatus.COMPLETED.value,
            CompletionStatus.BLOCKED.value,
            CompletionStatus.ERROR.value,
        }

    async def test_prompt_display_events_projects_prompt_stream(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt?event_format=display",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                payload = json.loads(sse.data)
                events.append({"event": sse.event, "data": payload})
                if sse.event == "final_result":
                    break

        assert events
        assert events[-1]["event"] == "final_result"
        assert events[-1]["data"]["display_kind"] == "final_result"
        assert events[-1]["data"]["payload"]["completion_status"] in {
            CompletionStatus.COMPLETED.value,
            CompletionStatus.BLOCKED.value,
            CompletionStatus.ERROR.value,
        }
        assert all("event_kind" not in event["data"] for event in events)

    async def test_prompt_streams_owner_conflict_as_error_event_without_fake_turn(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def conflicting_run_agent(_session_id: str, _prompt: str) -> None:
            assert _session_id == session_id
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        monkeypatch.setattr(session_manager, "run_agent", conflicting_run_agent)

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                break

        assert [event["event"] for event in events] == ["Error"]
        assert events[0]["data"]["error"] == "stale owner or fencing token rejected"

    async def test_external_worker_prompt_creates_run_without_running_agent(
        self, client, monkeypatch
    ):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(kind="external_worker"),
            },
        )
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]

        async def fail_run_agent(_session_id: str, _prompt: str) -> None:
            raise AssertionError("external worker prompt must not run server agent")

        monkeypatch.setattr(session_manager, "run_agent", fail_run_agent)

        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "run locally"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                assert sse.event == "RunRequested"
                payload = json.loads(sse.data)
                run_id = payload["run_id"]
                break

        run = store.runs[run_id]
        assert run.status == "requested"
        assert run.metadata["prompt"] == "run locally"
        assert run.metadata["executor_ref_kind"] == "external_worker"
        assert run.metadata["workspace_surface"] == "external_worker_workspace_ref"
        assert run.metadata["execution_plane"] == "executor_plane"
        assert run.metadata["execution_placement"] == "local_attached"
        assert run.metadata["executor_kind"] == "local_cli"

    async def test_local_attached_prompt_creates_attached_executor_run(
        self, client, monkeypatch
    ):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(kind="local_attached"),
            },
        )
        assert create_resp.status_code == 200
        session_id = create_resp.json()["session_id"]

        async def fail_run_agent(_session_id: str, _prompt: str) -> None:
            raise AssertionError("local_attached prompt must not run server agent")

        monkeypatch.setattr(session_manager, "run_agent", fail_run_agent)

        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "run locally"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                assert sse.event == "RunRequested"
                payload = json.loads(sse.data)
                run_id = payload["run_id"]
                break

        run = store.runs[run_id]
        assert run.status == "requested"
        assert run.metadata["prompt"] == "run locally"
        assert run.metadata["executor_ref_kind"] == "local_attached"
        assert run.metadata["workspace_surface"] == "local_attached_workspace"
        assert run.metadata["execution_plane"] == "executor_plane"
        assert run.metadata["execution_placement"] == "local_attached"
        assert run.metadata["executor_kind"] == "local_cli"

    async def test_external_worker_claim_events_and_complete(self, client):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(kind="external_worker"),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")

        claim_resp = await client.post(
            "/worker/runs/claim",
            json={
                "worker_id": "worker-1",
                "executor_kind": "local_cli",
                "session_id": session_id,
            },
        )
        assert claim_resp.status_code == 200
        claim = claim_resp.json()
        assert claim["run_id"] == run.run_id
        assert claim["prompt"] == "hello"
        assert store.runs[run.run_id].status == "claimed"

        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        await session_manager.register_owned_event_queue_async(session_id, queue)
        events_resp = await client.post(
            f"/worker/runs/{run.run_id}/events",
            json={
                "worker_id": "worker-1",
                "claim_token": claim["claim_token"],
                "events": [
                    {
                        "event_id": "event-1",
                        "event": "StreamDelta",
                        "data": {
                            "session_id": session_id,
                            "agent_id": "",
                            "content": "hi",
                        },
                    }
                ],
            },
        )
        rebroadcast = await asyncio.wait_for(queue.get(), timeout=1.0)
        await session_manager.remove_event_queue_async(session_id, queue)
        assert events_resp.status_code == 200
        assert events_resp.json()["events"][0]["event_kind"] == "wire.StreamDelta"
        stored_event = store.events[0]
        assert stored_event.payload["session_id"] == session_id
        assert stored_event.payload["run_id"] == run.run_id
        assert stored_event.payload["execution_placement"] == "local_attached"
        assert stored_event.payload["executor_ref_kind"] == "external_worker"
        assert (
            stored_event.payload["workspace_surface"] == "external_worker_workspace_ref"
        )
        assert stored_event.payload["execution_plane"] == "executor_plane"
        assert stored_event.payload["executor_id"] == "worker-1"
        assert stored_event.payload["message_type"] == "StreamDelta"
        assert rebroadcast["event"] == "StreamDelta"
        assert json.loads(rebroadcast["data"])["content"] == "hi"

        final_tape_id = f"tape-final-{run.run_id}"
        complete_resp = await client.post(
            f"/worker/runs/{run.run_id}/complete",
            json={
                "worker_id": "worker-1",
                "claim_token": claim["claim_token"],
                "status": "completed",
                "result": {"stop_reason": "no_tool_calls"},
                "tape_id": final_tape_id,
                "tape_entries": [
                    {
                        "kind": "message",
                        "payload": {"role": "user", "content": "hello"},
                    }
                ],
            },
        )
        assert complete_resp.status_code == 200
        assert complete_resp.json()["status"] == "completed"
        session = await session_manager.get_session_async(session_id)
        assert session.turn_in_progress is False
        assert session.turn_status == "idle"
        assert session.tape_id == final_tape_id
        assert await session_manager._tape_store.load(final_tape_id) == [
            {
                "kind": "message",
                "payload": {"role": "user", "content": "hello"},
            }
        ]

    async def test_attached_executor_alias_endpoints_accept_executor_id(self, client):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(kind="local_attached"),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")

        claim_resp = await client.post(
            "/executor/runs/claim",
            json={
                "executor_id": "executor-1",
                "executor_kind": "local_cli",
                "session_id": session_id,
            },
        )
        assert claim_resp.status_code == 200
        claim = claim_resp.json()
        assert claim["run_id"] == run.run_id
        assert store.runs[run.run_id].metadata["worker_id"] == "executor-1"

        heartbeat_resp = await client.post(
            f"/executor/runs/{run.run_id}/heartbeat",
            json={
                "executor_id": "executor-1",
                "claim_token": claim["claim_token"],
            },
        )
        assert heartbeat_resp.status_code == 200
        assert heartbeat_resp.json()["run_id"] == run.run_id

        complete_resp = await client.post(
            f"/executor/runs/{run.run_id}/complete",
            json={
                "executor_id": "executor-1",
                "claim_token": claim["claim_token"],
                "status": "completed",
                "result": {"stop_reason": "no_tool_calls"},
            },
        )
        assert complete_resp.status_code == 200
        assert complete_resp.json()["status"] == "completed"

    async def test_external_worker_session_runs_endpoint_lists_runs(self, client):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(kind="external_worker"),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")
        claim = await session_manager.claim_external_worker_run(
            worker_id="worker-1",
            executor_kind="local_cli",
            session_id=session_id,
            lease_seconds=30,
        )
        assert claim is not None

        response = await client.get(f"/sessions/{session_id}/runs")

        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == session_id
        assert [item["run_id"] for item in payload["runs"]] == [run.run_id]
        assert payload["runs"][0]["metadata"]["worker_id"] == "worker-1"
        assert "claim_token_hash" not in payload["runs"][0]["metadata"]

    async def test_external_worker_workers_endpoint_reports_running_and_stale_workers(
        self, client, tmp_path: Path
    ):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(
                    kind="external_worker",
                    display_path=str(tmp_path),
                ),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")
        claim = await session_manager.claim_external_worker_run(
            worker_id="worker-1",
            executor_kind="local_cli",
            session_id=session_id,
            lease_seconds=30,
        )
        assert claim is not None

        running_response = await client.get("/workers")

        assert running_response.status_code == 200
        worker = running_response.json()["workers"][0]
        assert worker["worker_id"] == "worker-1"
        assert worker["status"] == "running"
        assert worker["current_run_id"] == run.run_id
        assert worker["worker_pool"] == "default"
        assert worker["workspace_ref"] == {
            "kind": "local_path",
            "display_path": str(tmp_path),
        }

        executors_response = await client.get("/executors")
        assert executors_response.status_code == 200
        executor = executors_response.json()["executors"][0]
        assert executor["executor_id"] == "worker-1"
        assert executor["worker_id"] == "worker-1"
        assert executor["status"] == "running"

        executor_response = await client.get("/executors/worker-1")
        assert executor_response.status_code == 200
        assert executor_response.json()["executor_id"] == "worker-1"

        claimed = store.runs[run.run_id]
        store.runs[run.run_id] = replace(
            claimed,
            metadata={
                **claimed.metadata,
                "lease_expires_at": "2026-01-01T00:00:00+00:00",
            },
        )

        stale_response = await client.get("/workers/worker-1")

        assert stale_response.status_code == 200
        assert stale_response.json()["status"] == "stale"

        old_seen_at = (
            datetime.now(UTC)
            - timedelta(seconds=http_server.WORKER_OFFLINE_AFTER_SECONDS + 1)
        ).isoformat()
        stale_run = store.runs[run.run_id]
        store.runs[run.run_id] = replace(
            stale_run,
            status="completed",
            ended_at=datetime.now(UTC),
            metadata={
                **stale_run.metadata,
                "claimed_at": old_seen_at,
                "last_heartbeat_at": old_seen_at,
                "finalized_at": old_seen_at,
            },
        )

        offline_response = await client.get("/workers/worker-1")

        assert offline_response.status_code == 200
        assert offline_response.json()["status"] == "offline"

        active_without_lease = store.runs[run.run_id]
        metadata_without_lease = {
            **active_without_lease.metadata,
            "last_heartbeat_at": old_seen_at,
        }
        metadata_without_lease.pop("lease_expires_at", None)
        store.runs[run.run_id] = replace(
            active_without_lease,
            status="running",
            ended_at=None,
            metadata=metadata_without_lease,
        )

        active_offline_response = await client.get("/workers/worker-1")

        assert active_offline_response.status_code == 200
        assert active_offline_response.json()["status"] == "offline"

    async def test_external_worker_worker_metadata_surfaces_in_status(
        self, client, tmp_path: Path
    ):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(
                    kind="external_worker",
                    display_path=str(tmp_path),
                ),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")

        claim_resp = await client.post(
            "/worker/runs/claim",
            json={
                "worker_id": "worker-1",
                "executor_kind": "local_cli",
                "session_id": session_id,
                "worker_instance_id": "worker-1:instance-1",
                "process_id": 1234,
                "capabilities": {
                    "process_reconnect": "metadata_only",
                    "workspace_sync": "metadata_only",
                },
                "workspace_sync": {
                    "mode": "none",
                    "workspace_ref_kind": "local_path",
                },
            },
        )
        assert claim_resp.status_code == 200
        claim = claim_resp.json()

        heartbeat_resp = await client.post(
            f"/worker/runs/{run.run_id}/heartbeat",
            json={
                "worker_id": "worker-1",
                "claim_token": claim["claim_token"],
                "worker_instance_id": "worker-1:instance-2",
                "process_id": 5678,
                "capabilities": {"process_reconnect": "metadata_only"},
                "workspace_sync": {"mode": "none"},
            },
        )
        assert heartbeat_resp.status_code == 200

        status_resp = await client.get("/workers/worker-1")

        assert status_resp.status_code == 200
        worker = status_resp.json()
        assert worker["worker_instance_id"] == "worker-1:instance-2"
        assert worker["process_id"] == 5678
        assert worker["capabilities"] == {"process_reconnect": "metadata_only"}
        assert worker["workspace_sync"] == {"mode": "none"}

    async def test_external_worker_approval_uses_server_approval_flow(
        self, client, monkeypatch
    ):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "interactive",
                "run_target": _attached_run_target_payload(kind="external_worker"),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")
        claim_resp = await client.post(
            "/worker/runs/claim",
            json={
                "worker_id": "worker-1",
                "executor_kind": "local_cli",
                "session_id": session_id,
            },
        )
        claim = claim_resp.json()
        observed: list[ApprovalRequest] = []

        async def fake_wait_for_approval(
            requested_session_id: str,
            approval_req: ApprovalRequest,
        ) -> ApprovalResponse:
            assert requested_session_id == session_id
            observed.append(approval_req)
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=True,
                scope="once",
            )

        monkeypatch.setattr(http_server, "wait_for_approval", fake_wait_for_approval)

        approval_resp = await client.post(
            f"/worker/runs/{run.run_id}/approval",
            json={
                "worker_id": "worker-1",
                "claim_token": claim["claim_token"],
                "request_id": "approval-1",
                "tool_name": "shell_execute",
                "arguments": {"command": "pwd"},
            },
        )

        assert approval_resp.status_code == 200
        assert approval_resp.json() == {
            "request_id": "approval-1",
            "approved": True,
            "feedback": None,
            "scope": "once",
        }
        assert observed[0].tool == "shell_execute"
        assert observed[0].args == {"command": "pwd"}

    async def test_runtime_run_interactions_endpoint_lists_interactions(self, client):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "interactive",
                "run_target": _attached_run_target_payload(kind="external_worker"),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")
        approval_req = ApprovalRequest(
            session_id=session_id,
            agent_id="",
            request_id="approval-1",
            tool="shell_execute",
            args={"command": "pwd"},
        )

        approval_task = asyncio.create_task(
            session_manager.wait_for_http_approval(
                session_id,
                approval_req,
                timeout_seconds=30,
            )
        )
        await _wait_for_fake_interaction(store, approval_task)

        response = await client.get(f"/runs/{run.run_id}/interactions")

        assert response.status_code == 200
        interactions = response.json()["interactions"]
        assert len(interactions) == 1
        assert interactions[0]["interaction_id"] == f"{run.run_id}:approval:approval-1"
        assert interactions[0]["status"] == "pending"
        assert interactions[0]["metadata"]["session_id"] == session_id

        approval_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await approval_task

    async def test_runtime_interaction_resolve_uses_session_approval_flow(self, client):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "interactive",
                "run_target": _attached_run_target_payload(kind="external_worker"),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")
        approval_req = ApprovalRequest(
            session_id=session_id,
            agent_id="",
            request_id="approval-1",
            tool="shell_execute",
            args={"command": "pwd"},
        )
        approval_task = asyncio.create_task(
            session_manager.wait_for_http_approval(
                session_id,
                approval_req,
                timeout_seconds=30,
            )
        )
        await _wait_for_fake_interaction(store, approval_task)

        response = await client.post(
            f"/interactions/{run.run_id}:approval:approval-1/resolve",
            json={
                "approved": True,
                "feedback": "ok",
                "scope": "once",
            },
        )
        approval = await approval_task

        assert response.status_code == 200
        resolved = response.json()
        assert resolved["status"] == "approved"
        assert resolved["response_payload"]["approved"] is True
        assert approval.approved is True
        assert approval.feedback == "ok"

    async def test_external_worker_recovery_expires_stale_claim(self, client):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post(
            "/sessions",
            json={
                "approval_policy": "yolo",
                "run_target": _attached_run_target_payload(kind="external_worker"),
            },
        )
        session_id = create_resp.json()["session_id"]
        run = await session_manager.request_external_worker_run(session_id, "hello")
        claim = await session_manager.claim_external_worker_run(
            worker_id="worker-1",
            executor_kind="local_cli",
            session_id=session_id,
            lease_seconds=30,
        )
        assert claim is not None
        stale_run = store.runs[run.run_id]
        store.runs[run.run_id] = replace(
            stale_run,
            metadata={
                **stale_run.metadata,
                "lease_expires_at": "2026-01-01T00:00:00+00:00",
            },
        )

        recovered = await session_manager.recover_stale_runtime_runs(
            recovered_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        )

        assert recovered == 1
        recovered_run = store.runs[run.run_id]
        assert recovered_run.status == "expired"
        assert recovered_run.metadata["recovery_reason"] == (
            "attached_executor_lease_expired"
        )
        assert recovered_run.metadata["legacy_recovery_reason"] == (
            "external_worker_lease_expired"
        )
        assert recovered_run.metadata["reclaimable"] is True

    async def test_external_worker_fake_store_only_claims_reclaimable_runs(self):
        store = FakeExternalWorkerRuntimeStore()
        now = datetime.now(UTC)
        store.runs["run-completed"] = AgentRunRecord(
            run_id="run-completed",
            session_id="session-1",
            tape_id=None,
            parent_run_id=None,
            agent_id=None,
            status="completed",
            started_at=now,
            ended_at=now,
            metadata={
                "executor_ref_kind": "external_worker",
                "executor_kind": "local_cli",
            },
            result={},
            error=None,
        )

        claim = await store.claim_external_worker_run(
            session_id="session-1",
            executor_kind="local_cli",
            claim_metadata={"worker_id": "worker-1"},
        )

        assert claim is None

    async def test_http_resume_session_streams_resumed_run(self, client, monkeypatch):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post("/sessions", json={"approval_policy": "yolo"})
        session_id = create_resp.json()["session_id"]
        previous_run = AgentRunRecord(
            run_id="run-interrupted",
            session_id=session_id,
            tape_id="stable-tape",
            parent_run_id=None,
            agent_id=None,
            status="interrupted",
            started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
            metadata={},
            result={},
            error="interrupted",
        )
        store.runs[previous_run.run_id] = previous_run
        await store.append_runtime_event(
            RuntimeEventRecord(
                event_id="event-resume-from",
                run_id=previous_run.run_id,
                event_kind="wire.StreamDelta",
                payload={"content": "partial"},
                created_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
            )
        )
        observed_prompts: list[str] = []

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del pipeline, ctx
                self._consumer = consumer

            async def run_turn(self, prompt: str) -> TurnOutcome:
                observed_prompts.append(prompt)
                await self._consumer.emit(
                    StreamDelta(
                        session_id=session_id,
                        agent_id="",
                        content="resumed",
                    )
                )
                await self._consumer.emit(
                    TurnEnd(
                        session_id=session_id,
                        agent_id="",
                        turn_id="turn-resumed",
                        completion_status=CompletionStatus.COMPLETED,
                    )
                )
                return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS)

        fake_pipeline = types.SimpleNamespace(
            _registry=types.SimpleNamespace(
                get=lambda _: types.SimpleNamespace(_instance=None)
            )
        )

        def fake_create_agent(**kwargs):
            return fake_pipeline, types.SimpleNamespace(
                session_id=kwargs["session_id_override"],
                config={},
                tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            )

        monkeypatch.setattr(session_manager, "_create_agent", fake_create_agent)
        monkeypatch.setattr(
            "coding_agent.server.session_manager.PipelineAdapter", FakeAdapter
        )

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/resume",
            json={"prompt": "keep going", "resume_reason": "user_resume"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "TurnEnd":
                    break

        resumed_runs = [
            run for run in store.runs.values() if run.run_id != previous_run.run_id
        ]
        assert [event["event"] for event in events] == ["StreamDelta", "TurnEnd"]
        assert observed_prompts
        assert "Previous run was interrupted." in observed_prompts[0]
        assert "keep going" in observed_prompts[0]
        assert len(resumed_runs) == 1
        resumed_run = resumed_runs[0]
        assert resumed_run.parent_run_id == previous_run.run_id
        assert resumed_run.metadata["resume_from_run_id"] == previous_run.run_id
        assert resumed_run.metadata["resume_from_event_id"] == "event-resume-from"

    async def test_http_resume_external_executor_session_requests_linked_run(
        self, client
    ):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        session_id = await session_manager.create_session(
            approval_policy=ApprovalPolicy.YOLO,
            default_run_target=_external_worker_run_target(),
        )
        previous_run = AgentRunRecord(
            run_id="run-cancelled",
            session_id=session_id,
            tape_id="stable-tape",
            parent_run_id=None,
            agent_id=None,
            status="cancelled",
            started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
            metadata={},
            result={},
            error="cancelled",
        )
        store.runs[previous_run.run_id] = previous_run

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/resume",
            json={"prompt": "resume on local executor"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                break

        requested_runs = [
            run for run in store.runs.values() if run.run_id != previous_run.run_id
        ]
        assert events[0]["event"] == "RunRequested"
        assert len(requested_runs) == 1
        requested = requested_runs[0]
        assert requested.status == "requested"
        assert requested.parent_run_id == previous_run.run_id
        assert requested.metadata["resume_from_run_id"] == previous_run.run_id
        assert requested.metadata["prompt"].startswith("Previous run was interrupted.")

    async def test_http_resume_session_display_events_project_resumed_run(
        self, client, monkeypatch
    ):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post("/sessions", json={"approval_policy": "yolo"})
        session_id = create_resp.json()["session_id"]
        previous_run = AgentRunRecord(
            run_id="run-interrupted-display",
            session_id=session_id,
            tape_id="stable-tape",
            parent_run_id=None,
            agent_id=None,
            status="interrupted",
            started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
            metadata={},
            result={},
            error="interrupted",
        )
        store.runs[previous_run.run_id] = previous_run
        await store.append_runtime_event(
            RuntimeEventRecord(
                event_id="event-resume-display-from",
                run_id=previous_run.run_id,
                event_kind="wire.StreamDelta",
                payload={"content": "partial"},
                created_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
            )
        )

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del pipeline, ctx
                self._consumer = consumer

            async def run_turn(self, prompt: str) -> TurnOutcome:
                assert "display resume" in prompt
                await self._consumer.emit(
                    StreamDelta(
                        session_id=session_id,
                        agent_id="",
                        content="resumed",
                    )
                )
                await self._consumer.emit(
                    TurnEnd(
                        session_id=session_id,
                        agent_id="",
                        turn_id="turn-resumed-display",
                        completion_status=CompletionStatus.COMPLETED,
                    )
                )
                return TurnOutcome(stop_reason=StopReason.NO_TOOL_CALLS)

        fake_pipeline = types.SimpleNamespace(
            _registry=types.SimpleNamespace(
                get=lambda _: types.SimpleNamespace(_instance=None)
            )
        )

        def fake_create_agent(**kwargs):
            return fake_pipeline, types.SimpleNamespace(
                session_id=kwargs["session_id_override"],
                config={},
                tape=kwargs.get("tape") or Tape(tape_id="stable-tape"),
            )

        monkeypatch.setattr(session_manager, "_create_agent", fake_create_agent)
        monkeypatch.setattr(
            "coding_agent.server.session_manager.PipelineAdapter", FakeAdapter
        )

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/resume?event_format=display",
            json={"prompt": "display resume", "resume_reason": "user_resume"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "final_result":
                    break

        assert [event["event"] for event in events] == [
            "assistant_text_delta",
            "final_result",
        ]
        assert events[0]["data"]["payload"]["content"] == "resumed"
        assert events[0]["data"]["payload"]["role"] == "assistant"
        assert events[1]["data"]["payload"]["completion_status"] == "completed"

    async def test_session_summary_reports_resume_and_checkpoint_context(
        self, client, monkeypatch
    ):
        store = FakeExternalWorkerRuntimeStore()
        session_manager.configure_runtime_store(store)
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session = await session_manager.get_session_async(session_id)
        session.tape_id = "stable-tape"
        await session_manager._persist_session_async(session)
        previous_run = AgentRunRecord(
            run_id="run-interrupted",
            session_id=session_id,
            tape_id="stable-tape",
            parent_run_id=None,
            agent_id=None,
            status="interrupted",
            started_at=datetime(2026, 5, 19, 12, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
            metadata={},
            result={},
            error="interrupted",
        )
        store.runs[previous_run.run_id] = previous_run
        await store.append_runtime_event(
            RuntimeEventRecord(
                event_id="event-last",
                run_id=previous_run.run_id,
                event_kind="wire.TurnEnd",
                payload={"message_type": "TurnEnd"},
                created_at=datetime(2026, 5, 19, 12, 1, tzinfo=UTC),
            )
        )

        class FakeCheckpointService:
            async def list(self, tape_id: str):
                assert tape_id == "stable-tape"
                return [
                    CheckpointMeta(
                        checkpoint_id="cp-latest",
                        tape_id=tape_id,
                        session_id=session_id,
                        entry_count=2,
                        window_start=0,
                        created_at=datetime(2026, 5, 19, 12, 2, tzinfo=UTC),
                        label="resume-point",
                    )
                ]

        monkeypatch.setattr(
            session_manager,
            "_checkpoint_service",
            FakeCheckpointService(),
        )

        response = await client.get(f"/sessions/{session_id}")
        listed = await client.get("/sessions")

        assert response.status_code == 200
        payload = response.json()
        assert payload["resumable"] is True
        assert payload["last_run_id"] == "run-interrupted"
        assert payload["last_run_status"] == "interrupted"
        assert payload["last_interrupted_run_id"] == "run-interrupted"
        assert payload["resume_from_event_id"] == "event-last"
        assert payload["checkpoint_count"] == 1
        assert payload["latest_checkpoint_id"] == "cp-latest"
        assert payload["latest_checkpoint_label"] == "resume-point"
        assert listed.status_code == 200
        listed_payload = listed.json()["sessions"][0]
        assert listed_payload["resumable"] is True
        assert listed_payload["latest_checkpoint_id"] == "cp-latest"

    async def test_prompt_returns_parent_turn_end_when_agent_bootstrap_fails(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        with patch(
            "coding_agent.server.session_manager.importlib.import_module"
        ) as import_module:
            import_module.return_value = types.SimpleNamespace(
                create_agent=lambda **kwargs: (_ for _ in ()).throw(
                    RuntimeError("bootstrap exploded")
                )
            )

            events = []
            async with aconnect_sse(
                client,
                "POST",
                f"/sessions/{session_id}/prompt",
                json={"prompt": "Hello"},
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    events.append({"event": sse.event, "data": json.loads(sse.data)})
                    if sse.event == "TurnEnd":
                        break

        assert events[0]["event"] == "StreamDelta"
        assert "bootstrap exploded" in events[0]["data"]["content"]
        assert events[-1]["event"] == "TurnEnd"
        assert events[-1]["data"]["agent_id"] == ""
        assert events[-1]["data"]["completion_status"] == CompletionStatus.ERROR.value

    async def test_prompt_streams_fatal_tool_execution_error_as_error_event_without_fake_turn(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        class FakeAdapter:
            def __init__(self, pipeline, ctx, consumer) -> None:
                del pipeline, consumer
                self.ctx = ctx

            async def run_turn(self, prompt: str) -> None:
                del prompt
                raise FatalToolExecutionError("fatal tool failure")

            async def close(self) -> None:
                return None

        fake_pipeline = types.SimpleNamespace(
            _registry=types.SimpleNamespace(
                get=lambda _: types.SimpleNamespace(_instance=None)
            ),
            _directive_executor=None,
        )

        monkeypatch.setattr(
            "coding_agent.__main__.create_agent",
            lambda **kwargs: (
                fake_pipeline,
                types.SimpleNamespace(config={}, tape=kwargs.get("tape") or Tape()),
            ),
        )
        monkeypatch.setattr(
            "coding_agent.server.session_manager.PipelineAdapter", FakeAdapter
        )

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event in {"Error", "TurnEnd"}:
                    break

        assert [event["event"] for event in events] == ["Error"]
        assert events[0]["data"]["error"] == "fatal tool failure"

    async def test_prompt_sets_turn_in_progress(self, client):
        """Test that prompt sets turn_in_progress flag."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_run_agent(_session_id: str, _prompt: str) -> None:
            started.set()
            await release.wait()
            await session_manager.get_session(session_id).wire.send(
                TurnEnd(
                    session_id=session_id,
                    completion_status=CompletionStatus.COMPLETED,
                    turn_id="test-turn",
                )
            )

        # Start prompt in background
        async def send_prompt():
            async with aconnect_sse(
                client,
                "POST",
                f"/sessions/{session_id}/prompt",
                json={"prompt": "Hello"},
            ) as event_source:
                async for sse in event_source.aiter_sse():
                    if sse.event == "TurnEnd":
                        break

        # Check turn_in_progress during execution
        with patch.object(session_manager, "run_agent", side_effect=fake_run_agent):
            task = asyncio.create_task(send_prompt())
            await asyncio.wait_for(started.wait(), timeout=1)
            assert session_manager.get_session(session_id).turn_in_progress
            release.set()
            await task

        assert not session_manager.get_session(session_id).turn_in_progress

    async def test_prompt_surfaces_subagent_tool_failure_in_real_http_session(
        self, client, tmp_path
    ):
        class ScriptedSubagentProvider:
            def __init__(self) -> None:
                self.calls = 0

            @property
            def model_name(self) -> str:
                return "scripted-subagent"

            @property
            def max_context_size(self) -> int:
                return 128000

            async def stream(self, messages, tools=None, **kwargs):
                del messages, kwargs
                self.calls += 1
                if self.calls == 1:
                    yield ToolCallEvent(
                        tool_call_id="tc-http-subagent",
                        name="subagent",
                        arguments={"goal": "Inspect child task"},
                    )
                    yield DoneEvent()
                    return

                if self.calls == 2:
                    assert tools is not None
                    tool_names = {
                        tool["function"]["name"]
                        for tool in tools
                        if isinstance(tool, dict)
                        and isinstance(tool.get("function"), dict)
                    }
                    assert "subagent" not in tool_names
                    yield TextEvent(text="Child finished summary")
                    yield DoneEvent()
                    return

                yield TextEvent(text="Parent received child result")
                yield DoneEvent()

        provider = ScriptedSubagentProvider()
        session_id = "http-subagent-session"
        register_session(
            session_id,
            provider=provider,
            repo_path=tmp_path,
            approval_policy=ApprovalPolicy.YOLO,
        )

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Please delegate this to a subagent"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "TurnEnd" and not events[-1]["data"]["agent_id"]:
                    break

        assert any(
            event["event"] == "ToolCallDelta"
            and event["data"]["tool_name"] == "subagent"
            for event in events
        )
        assert any(
            event["event"] == "ToolResultDelta"
            and event["data"]["tool_name"] == "subagent"
            and event["data"]["display_result"]
            == "Subagent completed: Child finished summary"
            and event["data"]["is_error"] is False
            and event["data"]["result"] is None
            for event in events
        )
        assert any(
            event["event"] == "StreamDelta"
            and event["data"]["agent_id"] == "child-1"
            and event["data"]["content"] == "Child finished summary"
            for event in events
        )
        assert any(
            event["event"] == "StreamDelta"
            and event["data"]["agent_id"] == ""
            and event["data"]["content"] == "Parent received child result"
            for event in events
        )
        assert provider.calls == 3

    async def test_prompt_streams_fatal_subagent_summary_publish_as_error_event_in_real_http_session(
        self, client, tmp_path, monkeypatch
    ):
        class FatalSubagentProvider:
            def __init__(self) -> None:
                self.calls = 0

            @property
            def model_name(self) -> str:
                return "scripted-subagent-fatal"

            @property
            def max_context_size(self) -> int:
                return 128000

            async def stream(self, messages, tools=None, **kwargs):
                del messages, kwargs
                self.calls += 1
                if self.calls == 1:
                    yield ToolCallEvent(
                        tool_call_id="tc-http-subagent",
                        name="subagent",
                        arguments={"goal": "Inspect child task"},
                    )
                    yield DoneEvent()
                    return

                if self.calls == 2:
                    assert tools is not None
                    tool_names = {
                        tool["function"]["name"]
                        for tool in tools
                        if isinstance(tool, dict)
                        and isinstance(tool.get("function"), dict)
                    }
                    assert "subagent" not in tool_names
                    yield TextEvent(text="Child finished summary")
                    yield DoneEvent()
                    return

                yield TextEvent(text="Parent should not receive child result")
                yield DoneEvent()

        async def fatal_publish_subagent_message(
            session_id: str,
            text: str,
            *,
            message_id: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> bool:
            del session_id, text, message_id, metadata
            raise FatalToolExecutionError("fatal summary publish rejected")

        provider = FatalSubagentProvider()
        session_id = "http-subagent-fatal-session"
        session = register_session(
            session_id,
            provider=provider,
            repo_path=tmp_path,
            approval_policy=ApprovalPolicy.YOLO,
        )
        monkeypatch.setattr(
            session_manager,
            "publish_subagent_message",
            fatal_publish_subagent_message,
        )
        session_manager._runtime_context_binding_service = RuntimeContextBindingService(
            publish_subagent_message=fatal_publish_subagent_message
        )
        session_manager._local_daemon_runtime_preparation = replace(
            session_manager._local_daemon_runtime_preparation,
            bind_subagent_message_publisher=(
                session_manager._runtime_context_binding_service.bind_subagent_message_publisher
            ),
        )
        session_manager._runtime_turn_service_factory = replace(
            session_manager._runtime_turn_service_factory,
            prepare_runtime=session_manager._local_daemon_runtime_preparation.prepare_runtime,
            bind_root_run_identity=(
                session_manager._runtime_context_binding_service.bind_root_run_identity
            ),
            bind_subagent_message_publisher=(
                session_manager._runtime_context_binding_service.bind_subagent_message_publisher
            ),
        )
        session_manager._runtime_turn_service = (
            session_manager._build_runtime_turn_service()
        )
        session.runtime_pipeline = None
        session.runtime_ctx = None
        session.runtime_adapter = None

        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Please delegate this to a subagent"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append({"event": sse.event, "data": json.loads(sse.data)})
                if sse.event == "Error":
                    break

        assert any(
            event["event"] == "ToolCallDelta"
            and event["data"]["tool_name"] == "subagent"
            for event in events
        )
        assert any(
            event["event"] == "Error"
            and event["data"]["error"] == "fatal summary publish rejected"
            for event in events
        )
        assert provider.calls == 2


class TestConcurrentTurns:
    """Tests for 409 conflict on concurrent turns."""

    async def test_concurrent_turn_returns_409(self, client):
        """Test that concurrent turns return 409."""
        # Create session
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Manually set turn_in_progress
        session_manager.get_session(session_id).turn_in_progress = True

        # Try to send another prompt
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        )
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"].lower()

    async def test_turn_in_progress_cleared_after_completion(self, client):
        """Test that turn_in_progress is cleared after turn completes."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Complete a turn
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                if sse.event == "TurnEnd":
                    break

        # Should be able to start another turn
        assert not session_manager.get_session(session_id).turn_in_progress
        response = await client.post(
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello again"},
        )
        assert response.status_code == 200


class TestApprovalEndpoint:
    """Tests for approval endpoint."""

    async def test_approve_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.post(
            "/sessions/nonexistent/approve",
            json={"request_id": "req1", "approved": True},
        )
        assert response.status_code == 404

    async def test_approve_no_pending_request(self, client):
        """Test 400 when no pending approval (legacy check)."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Without adding request to ApprovalStore or setting legacy pending_approval,
        # it will fail the legacy check (400) if legacy session exists
        # But if no legacy session, it should try ApprovalStore (which returns 404)
        # Since create_session creates both, we expect 400 from legacy check
        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req1", "approved": True},
        )
        # Legacy session exists and pending_approval is None -> 400
        assert response.status_code == 400
        assert "no pending" in response.json()["detail"].lower()

    async def test_approve_rejects_unknown_request_id(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        add_store_backed_approval_request(session, session_id, "correct_id")

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "wrong_id", "approved": True},
        )
        assert response.status_code == 400
        assert "no pending approval request" in response.json()["detail"].lower()

    async def test_approve_returns_409_for_stale_owner_conflict(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        add_store_backed_approval_request(session, session_id, "req123")

        async def conflicting_submit_approval_response(**kwargs) -> ApprovalResponse:
            assert kwargs["session_id"] == session_id
            assert kwargs["request_id"] == "req123"
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        monkeypatch.setattr(
            session_manager,
            "submit_approval_response",
            conflicting_submit_approval_response,
        )

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "stale owner or fencing token rejected"

    async def test_approve_returns_500_without_internal_detail_for_unexpected_error(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_submit_approval_response(**kwargs) -> ApprovalResponse:
            assert kwargs["session_id"] == session_id
            assert kwargs["request_id"] == "req123"
            raise RuntimeError("secret internal failure")

        monkeypatch.setattr(
            session_manager,
            "submit_approval_response",
            failing_submit_approval_response,
        )

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True},
        )

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"

    async def test_approve_success(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        session.pending_approval = None
        session.approval_event.clear()
        add_store_backed_approval_request(session, session_id, "req123")

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "Looks good"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "approved"
        assert session.approval_event.is_set()
        assert session.pending_approval is None

    async def test_approve_success_clears_pending_projection_for_coordinator_backed_request(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        session.pending_approval = None
        session.approval_event.clear()

        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call-req123",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)
        session.pending_approval = session.approval_coordinator.projection()

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "Looks good"},
        )

        assert response.status_code == 200
        assert session.approval_event.is_set()
        assert session.pending_approval is None

    async def test_approve_retry_with_changed_body_uses_first_decision_before_waiter_consumes(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=ToolCallDelta(
                session_id=session_id,
                tool_name="bash",
                arguments={"command": "ls"},
                call_id="call1",
            ),
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        first = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "first"},
        )
        retry = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": False, "feedback": "changed"},
        )

        assert first.status_code == 200
        assert retry.status_code == 200
        assert retry.json()["decision"] == "approved"
        response = await session.approval_coordinator.wait_for_response(
            "req123",
            timeout=0.01,
        )
        assert response is not None
        assert response.approved is True
        assert response.feedback == "first"

    async def test_approve_retry_with_changed_body_uses_first_decision_after_waiter_consumes(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=ToolCallDelta(
                session_id=session_id,
                tool_name="bash",
                arguments={"command": "ls"},
                call_id="call1",
            ),
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        first = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "first"},
        )
        applied = await session.approval_coordinator.wait_for_response(
            "req123",
            timeout=0.01,
        )
        retry = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": False, "feedback": "changed"},
        )

        assert first.status_code == 200
        assert applied is not None
        assert applied.approved is True
        assert applied.feedback == "first"
        assert session.approval_store.get_request("req123") is None
        assert retry.status_code == 200
        assert retry.json()["decision"] == "approved"

    async def test_deny_success(self, client):
        """Test successful denial."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        session.pending_approval = {"request_id": "req123"}
        session.approval_event.clear()
        add_store_backed_approval_request(session, session_id, "req123")

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": False, "feedback": "Too risky"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["decision"] == "denied"
        assert session.approval_event.is_set()
        assert session.pending_approval is None

    async def test_approve_rejects_stale_pending_projection_without_store_request(
        self, client
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        session.pending_approval = {"request_id": "req123"}
        session.approval_event.clear()
        assert session.approval_store.get_request("req123") is None

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={"request_id": "req123", "approved": True, "feedback": "Looks good"},
        )

        assert response.status_code == 400
        assert "no pending approval request" in response.json()["detail"].lower()
        assert session.pending_approval == {"request_id": "req123"}
        assert session.approval_event.is_set() is False

    async def test_approve_with_approval_store(self, client):
        """Test approval using ApprovalStore."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Add request to ApprovalStore directly (bypassing legacy check)
        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        session.pending_approval = None

        # Now approve via submit_approval (which uses ApprovalStore)
        success = await session_manager.submit_approval(
            session_id=session_id,
            request_id="req123",
            approved=True,
            feedback="Looks good",
        )
        assert success is True

    async def test_approve_endpoint_can_set_session_scope(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={
                "request_id": "req123",
                "approved": True,
                "feedback": "Looks good",
                "scope": "session",
            },
        )

        assert response.status_code == 200
        assert session.approval_coordinator.is_session_approved(
            ApprovalRequest(
                session_id=session_id,
                request_id="req456",
                tool_call=ToolCallDelta(
                    session_id=session_id,
                    tool_name="bash",
                    arguments={"command": "pwd"},
                    call_id="call2",
                ),
            )
        )

    async def test_approve_endpoint_query_params_can_set_session_scope(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        response = await client.post(
            f"/sessions/{session_id}/approve?request_id=req123&approved=true&scope=session"
        )

        assert response.status_code == 200
        assert session.approval_coordinator.is_session_approved(
            ApprovalRequest(
                session_id=session_id,
                request_id="req456",
                tool_call=ToolCallDelta(
                    session_id=session_id,
                    tool_name="bash",
                    arguments={"command": "pwd"},
                    call_id="call2",
                ),
            )
        )

    async def test_approve_endpoint_rejects_legacy_always_scope(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req123",
            tool_call=ToolCallDelta(
                session_id=session_id,
                tool_name="bash",
                arguments={"command": "ls"},
                call_id="call1",
            ),
            timeout_seconds=120,
        )
        session.approval_coordinator.add_request(approval_req)

        response = await client.post(
            f"/sessions/{session_id}/approve",
            json={
                "request_id": "req123",
                "approved": True,
                "feedback": "Looks good",
                "scope": "always",
            },
        )

        assert response.status_code == 422


class TestEventsFanOut:
    """Tests for SSE fan-out with multiple clients."""

    async def test_event_queues_registered(self, client):
        """Test that event queues are registered for fan-out."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Manually add queues to test fan-out
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        session = session_manager.get_session(session_id)
        session.event_queues = [queue1, queue2]

        # Broadcast an event
        test_event = {"event": "Test", "data": "{}"}
        await _broadcast_event(session, test_event)

        # Both queues should receive the event
        assert await queue1.get() == test_event
        assert await queue2.get() == test_event

    async def test_multiple_queues_in_session(self, client):
        """Test that a session can have multiple event queues."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Verify the session has event_queues list
        session = session_manager.get_session(session_id)
        assert hasattr(session, "event_queues")
        assert isinstance(session.event_queues, list)

    async def test_session_display_events_projects_live_wire_events(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session = session_manager.get_session(session_id)
        session.current_turn_id = "run-live"

        class FakeEventSourceResponse:
            def __init__(self, body_iterator):
                self.body_iterator = body_iterator

        monkeypatch.setattr(
            "coding_agent.server.http_server.EventSourceResponse",
            FakeEventSourceResponse,
        )

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session_id}/display-events",
                "headers": [],
            }
        )
        response = await get_session_display_events(request, session_id, None, None)
        event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)
        assert len(session.event_queues) == 1

        await session.event_queues[0].put(
            _wire_message_to_event(
                StreamDelta(
                    session_id=session_id,
                    agent_id="agent-1",
                    content="hello",
                    role="assistant",
                    timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
                )
            )
        )

        event = await anext(event_generator)
        assert event["event"] == "assistant_text_delta"
        data = json.loads(event["data"])
        assert data["source_event_id"].startswith(f"live:{session_id}:")
        assert data["run_id"] == "run-live"
        assert data["sequence"] is None
        assert data["display_kind"] == "assistant_text_delta"
        assert data["payload"] == {
            "agent_id": "agent-1",
            "content": "hello",
            "role": "assistant",
        }
        assert data["created_at"] == "2026-01-02T03:04:05+00:00"

        await event_generator.aclose()
        assert session.event_queues == []

    async def test_session_display_events_rejects_non_owner(self, client, monkeypatch):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session = session_manager.get_session(session_id)
        session.origin = {"owner_label": "owner:a"}

        class FakeEventSourceResponse:
            def __init__(self, body_iterator):
                self.body_iterator = body_iterator

        monkeypatch.setattr(
            "coding_agent.server.http_server.EventSourceResponse",
            FakeEventSourceResponse,
        )

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session_id}/display-events",
                "headers": [],
            }
        )
        auth_context = AuthContext(
            scope="user",
            token="token-b",
            token_digest="digest-b",
            owner_label="owner:b",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_session_display_events(
                request,
                session_id,
                None,
                auth_context,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Session not found"
        assert session.event_queues == []

    async def test_session_events_rejects_non_owner(self, client, monkeypatch):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session = session_manager.get_session(session_id)
        session.origin = {"owner_label": "owner:a"}

        class FakeEventSourceResponse:
            def __init__(self, body_iterator):
                self.body_iterator = body_iterator

        monkeypatch.setattr(
            "coding_agent.server.http_server.EventSourceResponse",
            FakeEventSourceResponse,
        )

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session_id}/events",
                "headers": [],
            }
        )
        auth_context = AuthContext(
            scope="user",
            token="token-b",
            token_digest="digest-b",
            owner_label="owner:b",
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_events(
                request,
                session_id,
                None,
                auth_context,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Session not found"
        assert session.event_queues == []

    async def test_session_display_events_redacts_live_tool_result_payload(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session = session_manager.get_session(session_id)
        session.current_turn_id = "run-live"

        class FakeEventSourceResponse:
            def __init__(self, body_iterator):
                self.body_iterator = body_iterator

        monkeypatch.setattr(
            "coding_agent.server.http_server.EventSourceResponse",
            FakeEventSourceResponse,
        )

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session_id}/display-events",
                "headers": [],
            }
        )
        response = await get_session_display_events(request, session_id, None, None)
        event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)
        await session.event_queues[0].put(
            _wire_message_to_event(
                ToolResultDelta(
                    session_id=session_id,
                    agent_id="agent-1",
                    call_id="call-1",
                    tool_name="bash_run",
                    result={"stdout": "SECRET=abc123"},
                    display_result="command succeeded",
                    timestamp=datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC),
                )
            )
        )

        event = await anext(event_generator)
        assert event["event"] == "tool_result"
        data = json.loads(event["data"])
        assert data["payload"] == {
            "agent_id": "agent-1",
            "call_id": "call-1",
            "tool_name": "bash_run",
            "display_result": "command succeeded",
            "is_error": False,
        }
        assert "SECRET" not in json.dumps(data)

        await event_generator.aclose()
        assert session.event_queues == []

    async def test_event_queue_cleanup_is_shielded_on_disconnect(self, monkeypatch):
        session_id = "disconnect-session"
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)

        cleanup_started = asyncio.Event()
        cleanup_released = asyncio.Event()
        cleaned: list[tuple[str, object]] = []

        async def fake_remove_event_queue_async(current_session_id: str, queue) -> None:
            cleaned.append((current_session_id, queue))
            cleanup_started.set()
            await cleanup_released.wait()

        monkeypatch.setattr(
            session_manager,
            "remove_event_queue_async",
            fake_remove_event_queue_async,
        )

        task = asyncio.create_task(
            _cleanup_event_queue_on_disconnect(session_id, queue)
        )
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        cleanup_released.set()
        await task

        assert len(cleaned) == 1
        assert cleaned == [(session_id, queue)]

    async def test_event_queue_cleanup_ignores_missing_session(self, monkeypatch):
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)

        async def fake_remove_event_queue_async(current_session_id: str, queue) -> None:
            _ = (current_session_id, queue)
            raise KeyError("Session not found: removed")

        monkeypatch.setattr(
            session_manager,
            "remove_event_queue_async",
            fake_remove_event_queue_async,
        )

        await _cleanup_event_queue_on_disconnect("removed", queue)

    async def test_event_generator_uses_public_session_apis_for_keepalive_exit(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        has_session_calls: list[str] = []
        get_session_calls: list[str] = []
        has_session_results = iter([True, False])

        class FakeEventSourceResponse:
            def __init__(self, body_iterator):
                self.body_iterator = body_iterator

        async def fake_has_session_async(current_session_id: str) -> bool:
            has_session_calls.append(current_session_id)
            return next(has_session_results)

        async def fake_get_session_async(current_session_id: str):
            get_session_calls.append(current_session_id)
            return session_manager.get_session(current_session_id)

        monkeypatch.setattr(
            session_manager, "has_session_async", fake_has_session_async
        )
        monkeypatch.setattr(
            session_manager, "get_session_async", fake_get_session_async
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.EventSourceResponse",
            FakeEventSourceResponse,
        )

        real_wait_for = asyncio.wait_for

        async def fake_wait_for(awaitable, timeout):
            if timeout == 30.0:
                awaitable.close()
                raise asyncio.TimeoutError
            return await real_wait_for(awaitable, timeout)

        monkeypatch.setattr(
            "coding_agent.server.http_server.asyncio.wait_for", fake_wait_for
        )

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session_id}/events",
                "headers": [],
            }
        )
        response = await get_events(request, session_id, None, None)
        event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)
        event = await anext(event_generator)

        assert event == {"event": "ping", "data": ""}

        with pytest.raises(StopAsyncIteration):
            await anext(event_generator)

        assert has_session_calls == [session_id, session_id]
        assert len(get_session_calls) >= 2
        assert all(
            call_session_id == session_id for call_session_id in get_session_calls
        )

    async def test_event_generator_exits_cleanly_when_session_disappears_during_keepalive(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        has_session_results = iter([True, True])

        class FakeEventSourceResponse:
            def __init__(self, body_iterator):
                self.body_iterator = body_iterator

        async def fake_has_session_async(current_session_id: str) -> bool:
            assert current_session_id == session_id
            return next(has_session_results)

        async def fake_has_event_queue_async(current_session_id: str, queue) -> bool:
            _ = queue
            assert current_session_id == session_id
            raise KeyError(f"Session not found: {session_id}")

        monkeypatch.setattr(
            session_manager, "has_session_async", fake_has_session_async
        )
        monkeypatch.setattr(
            session_manager, "has_event_queue_async", fake_has_event_queue_async
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.EventSourceResponse",
            FakeEventSourceResponse,
        )

        real_wait_for = asyncio.wait_for

        async def fake_wait_for(awaitable, timeout):
            if timeout == 30.0:
                awaitable.close()
                raise asyncio.TimeoutError
            return await real_wait_for(awaitable, timeout)

        monkeypatch.setattr(
            "coding_agent.server.http_server.asyncio.wait_for", fake_wait_for
        )

        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/sessions/{session_id}/events",
                "headers": [],
            }
        )
        response = await get_events(request, session_id, None, None)
        event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)

        with pytest.raises(StopAsyncIteration):
            await anext(event_generator)


class TestLifespanShutdown:
    async def test_lifespan_runs_startup_cloud_workspace_cleanup_when_configured(
        self, monkeypatch
    ):
        events: list[str] = []
        cloud_workspace_config = {
            "enabled": True,
            "provider": "docker",
            "cleanup_on_startup": True,
        }

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_renew_owner_leases() -> None:
            raise asyncio.CancelledError

        def fake_cleanup_stale(config: dict[str, object]) -> int:
            assert config == {**cloud_workspace_config, "_active_workspace_ids": []}
            events.append("startup-gc")
            return 2

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            events.append("close")

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: cloud_workspace_config,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_stale_cloud_workspaces_from_config",
            fake_cleanup_stale,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert events == ["startup-gc", "close"]

    async def test_cloud_workspace_gc_excludes_active_cloud_sessions(self, monkeypatch):
        active_binding = CloudWorkspaceRef(
            workspace_url="docker://agent-ws-active/workspace",
            workspace_id="ws-active",
        )
        session_id = await session_manager.create_session(
            origin={
                "placement_kind": "cloud_workspace",
                "workspace_source_kind": "docker",
            },
            default_run_target=_cloud_run_target(active_binding),
        )
        seen_configs: list[dict[str, object]] = []

        def fake_cleanup_stale(config: dict[str, object]) -> int:
            seen_configs.append(dict(config))
            return 0

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {
                "enabled": True,
                "provider": "docker",
                "cleanup_on_startup": True,
            },
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_stale_cloud_workspaces_from_config",
            fake_cleanup_stale,
        )

        await http_server._cleanup_cloud_workspaces_on_startup()

        assert seen_configs == [
            {
                "enabled": True,
                "provider": "docker",
                "cleanup_on_startup": True,
                "_active_workspace_ids": ["ws-active"],
            }
        ]
        await session_manager.close_session(session_id)

    async def test_durable_cloud_workspace_gc_cleans_expired_local_ttl_records(
        self, monkeypatch
    ) -> None:
        cleaned: list[tuple[str, set[str]]] = []
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-expired",
                    workspace_id="ws-expired",
                    session_id="session-expired",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="ttl",
                    expires_at=datetime.now(UTC) - timedelta(seconds=60),
                ),
                WorkspaceRecord(
                    workspace_record_id="wr-remote",
                    workspace_id="ws-remote",
                    session_id="session-remote",
                    provider="docker",
                    provider_instance_id="docker-remote",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="remote-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="ttl",
                    expires_at=datetime.now(UTC) - timedelta(seconds=60),
                ),
                WorkspaceRecord(
                    workspace_record_id="wr-active",
                    workspace_id="ws-active",
                    session_id="session-active",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="active",
                    retention_policy="ttl",
                    expires_at=datetime.now(UTC) - timedelta(seconds=60),
                ),
                WorkspaceRecord(
                    workspace_record_id="wr-pinned",
                    workspace_id="ws-pinned",
                    session_id="session-pinned",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="pinned",
                    expires_at=datetime.now(UTC) - timedelta(seconds=60),
                ),
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        def fake_cleanup(
            config: dict[str, object],
            workspace_id: str,
            *,
            active_workspace_ids: set[str] | None = None,
        ) -> object:
            del config
            cleaned.append((workspace_id, set(active_workspace_ids or set())))
            return SimpleNamespace(workspace_id=workspace_id, status="cleaned")

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_workspace_from_config",
            fake_cleanup,
        )

        cleaned_count = await http_server._cleanup_durable_cloud_workspaces(
            {
                "enabled": True,
                "provider": "docker",
                "provider_instance_id": "docker-local",
                "_active_workspace_ids": ["ws-active"],
            }
        )

        assert cleaned_count == 1
        assert cleaned == [("ws-expired", {"ws-active"})]
        assert store.status_updates == [
            ("wr-expired", "cleaning", None),
            ("wr-expired", "cleaned", None),
        ]

    async def test_durable_cloud_workspace_gc_marks_cleanup_failure(
        self, monkeypatch
    ) -> None:
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-fails",
                    workspace_id="ws-fails",
                    session_id="session-fails",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="stale",
                    retention_policy="delete_on_close",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        def fake_cleanup(
            config: dict[str, object],
            workspace_id: str,
            *,
            active_workspace_ids: set[str] | None = None,
        ) -> object:
            del config, workspace_id, active_workspace_ids
            raise RuntimeError("docker unavailable")

        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_workspace_from_config",
            fake_cleanup,
        )

        cleaned_count = await http_server._cleanup_durable_cloud_workspaces(
            {
                "enabled": True,
                "provider": "docker",
                "provider_instance_id": "docker-local",
                "_active_workspace_ids": [],
            }
        )

        assert cleaned_count == 0
        assert store.status_updates == [
            ("wr-fails", "cleaning", None),
            ("wr-fails", "cleanup_failed", "docker unavailable"),
        ]

    async def test_periodic_cloud_workspace_gc_runs_at_configured_interval(
        self, monkeypatch
    ):
        events: list[str] = []
        cloud_workspace_config = {
            "enabled": True,
            "provider": "docker",
            "gc_interval_seconds": 300,
            "max_workspace_age_seconds": 3600,
        }

        def fake_cleanup_stale(config: dict[str, object]) -> int:
            assert config == {**cloud_workspace_config, "_active_workspace_ids": []}
            events.append("periodic-gc")
            return 1

        async def fake_sleep(delay: float) -> None:
            events.append(f"sleep:{delay}")
            raise asyncio.CancelledError

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: cloud_workspace_config,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_stale_cloud_workspaces_from_config",
            fake_cleanup_stale,
        )
        monkeypatch.setattr("coding_agent.server.http_server.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await http_server._cleanup_stale_cloud_workspaces_periodically()

        assert events == ["periodic-gc", "sleep:300.0"]

    def test_cloud_workspace_gc_interval_rejects_boolean_numeric_values(self):
        assert (
            http_server._cloud_workspace_gc_interval_seconds(
                {
                    "enabled": True,
                    "gc_interval_seconds": True,
                    "max_workspace_age_seconds": 3600,
                }
            )
            is None
        )
        assert (
            http_server._cloud_workspace_gc_interval_seconds(
                {
                    "enabled": True,
                    "gc_interval_seconds": 300,
                    "max_workspace_age_seconds": True,
                }
            )
            is None
        )

    async def test_lifespan_shutdown_continues_after_session_failure(self, monkeypatch):
        observed_shutdowns: list[str] = []
        close_calls: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_list_sessions_async() -> list[str]:
            return ["session-a", "session-b"]

        async def fake_shutdown_session_runtime(
            session_id: str,
            *,
            interrupt_active_turn: bool = False,
        ) -> None:
            assert interrupt_active_turn is True
            observed_shutdowns.append(session_id)
            if session_id == "session-a":
                raise RuntimeError("boom")

        async def fake_close() -> None:
            close_calls.append("closed")

        monkeypatch.setattr(
            "coding_agent.server.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(
            session_manager,
            "shutdown_session_runtime",
            fake_shutdown_session_runtime,
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await cm.__aexit__(None, None, None)

        assert observed_shutdowns == ["session-a", "session-b"]
        assert close_calls == ["closed"]

    async def test_lifespan_shutdown_marks_active_turn_interrupted(self, monkeypatch):
        runtime_store = FakeExternalWorkerRuntimeStore()
        events: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_renew_owner_leases() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_close() -> None:
            events.append("close")

        monkeypatch.setattr(
            "coding_agent.server.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(session_manager, "close", fake_close)
        session_manager.configure_runtime_store(runtime_store)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        try:
            session_id = await session_manager.create_session()
            session = await session_manager.get_session_async(session_id)
            run_id = "run-shutdown"
            started_at = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
            runtime_store.runs[run_id] = AgentRunRecord(
                run_id=run_id,
                session_id=session_id,
                tape_id="tape-1",
                parent_run_id=None,
                agent_id=None,
                status="running",
                started_at=started_at,
                metadata={"provider_name": "test-provider"},
                result={"steps_taken": 1},
            )

            async def running_turn() -> None:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    await runtime_store.update_agent_run(
                        run_id,
                        status="cancelled",
                        ended_at=datetime(2026, 6, 16, 12, 1, tzinfo=UTC),
                        metadata={"provider_name": "test-provider"},
                        result={"steps_taken": 1},
                        error="cancelled",
                    )
                    raise

            task = asyncio.create_task(running_turn())
            session.task = task
            session.current_turn_id = run_id
            session.turn_in_progress = True
            session.turn_status = "running"
            await asyncio.sleep(0)
        finally:
            await cm.__aexit__(None, None, None)

        interrupted_run = runtime_store.runs[run_id]
        assert interrupted_run.status == "interrupted"
        assert interrupted_run.ended_at is not None
        assert isinstance(interrupted_run.metadata["recovered_at"], str)
        metadata_without_time = dict(interrupted_run.metadata)
        metadata_without_time.pop("recovered_at")
        assert metadata_without_time == {
            "provider_name": "test-provider",
            "reclaimable": True,
            "recovery_reason": "graceful_shutdown",
        }
        assert interrupted_run.result == {"steps_taken": 1}
        assert (
            interrupted_run.error
            == "runtime run was interrupted during graceful shutdown"
        )
        assert events == ["close"]

    async def test_lifespan_shutdown_logs_failed_owner_renew_task(
        self, monkeypatch, caplog
    ):
        close_calls: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_renew_owner_leases() -> None:
            raise RuntimeError("renew task failed before shutdown")

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            close_calls.append("closed")

        monkeypatch.setattr(
            "coding_agent.server.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert close_calls == ["closed"]
        assert "Owner lease renewal task failed during shutdown" in caplog.text

    async def test_lifespan_backfills_owner_leases_before_renewal(self, monkeypatch):
        events: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_backfill_owner_leases() -> None:
            events.append("backfill")

        async def fake_renew_owner_leases() -> None:
            events.append("renew")
            raise asyncio.CancelledError

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            events.append("close")

        monkeypatch.setattr(
            "coding_agent.server.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager,
            "backfill_owner_leases",
            fake_backfill_owner_leases,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert events == ["backfill", "renew", "close"]

    async def test_lifespan_recovers_stale_runtime_runs_after_backfill_before_renewal(
        self, monkeypatch
    ):
        events: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_backfill_owner_leases() -> None:
            events.append("backfill")

        async def fake_recover_stale_runtime_runs() -> int:
            events.append("recover")
            return 2

        async def fake_renew_owner_leases() -> None:
            events.append("renew")
            raise asyncio.CancelledError

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            events.append("close")

        monkeypatch.setattr(
            "coding_agent.server.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager,
            "backfill_owner_leases",
            fake_backfill_owner_leases,
        )
        monkeypatch.setattr(
            session_manager,
            "recover_stale_runtime_runs",
            fake_recover_stale_runtime_runs,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert events == ["backfill", "recover", "renew", "close"]

    async def test_lifespan_logs_backfill_failure_and_still_starts(
        self, monkeypatch, caplog
    ):
        events: list[str] = []

        async def fake_cleanup_idle_sessions() -> None:
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        async def fake_backfill_owner_leases() -> None:
            events.append("backfill")
            raise RuntimeError("backfill failed")

        async def fake_renew_owner_leases() -> None:
            events.append("renew")
            raise asyncio.CancelledError

        async def fake_list_sessions_async() -> list[str]:
            return []

        async def fake_close() -> None:
            events.append("close")

        monkeypatch.setattr(
            "coding_agent.server.http_server._cleanup_idle_sessions",
            fake_cleanup_idle_sessions,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._renew_owner_leases",
            fake_renew_owner_leases,
        )
        monkeypatch.setattr(
            session_manager,
            "backfill_owner_leases",
            fake_backfill_owner_leases,
        )
        monkeypatch.setattr(
            session_manager, "list_sessions_async", fake_list_sessions_async
        )
        monkeypatch.setattr(session_manager, "close", fake_close)

        cm = app.router.lifespan_context(app)
        await cm.__aenter__()
        await asyncio.sleep(0)
        await cm.__aexit__(None, None, None)

        assert events == ["backfill", "renew", "close"]
        assert "Failed to backfill owner leases during startup" in caplog.text


class TestGetSession:
    """Tests for get session endpoint."""

    async def test_list_sessions_returns_only_sessions_owned_by_user_token(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        first = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer user-token-a"},
            json={},
        )
        second = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )

        assert first.status_code == 200
        assert second.status_code == 200

        response = await client.get(
            "/sessions",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 200
        assert [item["session_id"] for item in response.json()["sessions"]] == [
            first.json()["session_id"]
        ]

    async def test_list_sessions_returns_all_sessions_for_admin_token(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        first = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer user-token-a"},
            json={},
        )
        second = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )

        response = await client.get(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
        )

        assert response.status_code == 200
        assert {item["session_id"] for item in response.json()["sessions"]} == {
            first.json()["session_id"],
            second.json()["session_id"],
        }

    async def test_get_session_response_includes_status_and_workspace_summary(
        self, client: AsyncClient
    ):
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-owned/workspace",
                    workspace_id="ws-owned",
                ),
                "provider": "openai",
                "model": "test-model",
                "max_steps": 7,
            },
        )
        session_id = create_resp.json()["session_id"]

        response = await client.get(f"/sessions/{session_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["id"] == session_id
        assert data["status"] == "created"
        assert data["turn_status"] == "idle"
        assert data["default_run_target"]["workspace"]["kind"] == "cloud_workspace"
        assert data["workspace_id"] == "ws-owned"
        assert data["provider_name"] == "openai"
        assert data["model_name"] == "test-model"
        assert data["max_steps"] == 7

    async def test_get_session_hides_other_user_session(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        created = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )

        response = await client.get(
            f"/sessions/{created.json()['session_id']}",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 404

    async def test_get_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.get("/sessions/nonexistent")
        assert response.status_code == 404

    async def test_get_session_success(self, client):
        """Test getting session details."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert "created_at" in data
        assert "last_activity" in data
        assert "turn_in_progress" in data
        assert "pending_approval" in data


class TestRemoteResultPublicationContract:
    async def test_session_result_endpoint_uses_agentkit_reducer_without_response_change(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = register_session(
            "result-agentkit-reducer",
            default_run_target=_cloud_run_target(
                CloudWorkspaceRef(
                    workspace_url="docker://agent-ws-result/workspace",
                    workspace_id="ws-agentkit-result",
                )
            ),
            provider_name="openai",
            model_name="result-model",
        )
        tape = Tape(tape_id="runtime-tape")
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix it"})
        )
        tape.append(
            Entry(kind="message", payload={"role": "assistant", "content": "ignored"})
        )
        session.runtime_ctx = SimpleNamespace(tape=tape)
        session.tape_id = tape.tape_id

        def fake_result_from_turn_trace(turn: TurnTrace) -> TurnResult:
            assert turn.user_input == "fix it"
            return TurnResult(
                final_output="Reducer final answer.",
                verification_summary=VerificationSummary(
                    summary="Reducer verification summary."
                ),
            )

        monkeypatch.setattr(
            http_server,
            "result_from_turn_trace",
            fake_result_from_turn_trace,
        )

        response = await client.get(f"/sessions/{session.id}/result")

        assert response.status_code == 200
        data = response.json()
        assert data["final_answer"] == "Reducer final answer."
        assert data["verification_summary"] == "Reducer verification summary."
        assert data["failure_details"] is None

    async def test_session_result_includes_final_answer_and_tool_activity_from_runtime_tape(
        self, client: AsyncClient
    ) -> None:
        session = register_session(
            "result-runtime-tape",
            default_run_target=_cloud_run_target(
                CloudWorkspaceRef(
                    workspace_url="docker://agent-ws-result/workspace",
                    workspace_id="ws-runtime-result",
                )
            ),
            provider_name="openai",
            model_name="result-model",
        )
        tape = Tape(tape_id="runtime-tape")
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "fix it"})
        )
        tape.append(
            Entry(
                kind="tool_call",
                payload={
                    "id": "tool-1",
                    "name": "shell_command",
                    "arguments": {"command": "uv run pytest tests/foo.py -q"},
                },
            )
        )
        tape.append(
            Entry(
                kind="tool_result",
                payload={"tool_call_id": "tool-1", "content": "1 passed"},
            )
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "Fixed and verified."},
            )
        )
        session.runtime_ctx = SimpleNamespace(tape=tape)
        session.tape_id = tape.tape_id

        response = await client.get(f"/sessions/{session.id}/result")

        assert response.status_code == 200
        data = response.json()
        assert data["final_answer"] == "Fixed and verified."
        assert (
            data["verification_summary"]
            == "Tool activity: shell_command: uv run pytest tests/foo.py -q"
        )
        assert data["failure_details"] is None

    async def test_session_result_restores_persisted_tape_when_runtime_is_unloaded(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = register_session(
            "result-persisted-tape",
            default_run_target=_cloud_run_target(
                CloudWorkspaceRef(
                    workspace_url="docker://agent-ws-result/workspace",
                    workspace_id="ws-persisted-result",
                )
            ),
            provider_name="openai",
            model_name="result-model",
            tape_id="persisted-tape",
        )
        tape = Tape(tape_id="persisted-tape")
        tape.append(
            Entry(kind="message", payload={"role": "user", "content": "try again"})
        )
        tape.append(
            Entry(
                kind="message",
                payload={"role": "assistant", "content": "Persisted answer."},
            )
        )
        restored_tape_ids: list[str | None] = []

        async def fake_restore_tape(tape_id: str | None) -> Tape | None:
            restored_tape_ids.append(tape_id)
            return tape

        monkeypatch.setattr(session_manager, "_restore_tape", fake_restore_tape)

        response = await client.get(f"/sessions/{session.id}/result")

        assert response.status_code == 200
        assert restored_tape_ids == ["persisted-tape"]
        data = response.json()
        assert data["final_answer"] == "Persisted answer."
        assert data["verification_summary"] is None

    async def test_session_result_restores_from_persisted_runtime_events_when_tape_is_empty(
        self, client: AsyncClient
    ) -> None:
        session_id = "result-runtime-events"
        run_id = "run-runtime-events"
        register_session(
            session_id,
            default_run_target=_local_run_target(Path.cwd()),
            provider_name="openai",
            model_name="result-model",
            tape_id="empty-runtime-events-tape",
        )
        run = AgentRunRecord(
            run_id=run_id,
            session_id=session_id,
            tape_id="empty-runtime-events-tape",
            parent_run_id=None,
            agent_id=None,
            status="completed",
            started_at=datetime(2026, 1, 2, 3, 4, 5),
            ended_at=datetime(2026, 1, 2, 3, 6, 0),
            metadata={"provider_name": "test-provider"},
            result={"stop_reason": "no_tool_calls"},
            error=None,
        )
        session_manager.configure_runtime_store(
            FakeRuntimeReplayStore(
                run=run,
                events=[
                    RuntimeEventRecord(
                        sequence=1,
                        event_id="event-tool",
                        run_id=run_id,
                        event_kind="wire.ToolCallDelta",
                        payload={
                            "message_type": "ToolCallDelta",
                            "message": {
                                "call_id": "call-1",
                                "tool_name": "shell_command",
                                "arguments": {
                                    "command": "uv run pytest tests/foo.py -q"
                                },
                            },
                        },
                        created_at=datetime(2026, 1, 2, 3, 5, 0),
                    ),
                    RuntimeEventRecord(
                        sequence=2,
                        event_id="event-text-1",
                        run_id=run_id,
                        event_kind="wire.StreamDelta",
                        payload={
                            "message_type": "StreamDelta",
                            "message": {
                                "content": "Fixed ",
                                "role": "assistant",
                            },
                        },
                        created_at=datetime(2026, 1, 2, 3, 5, 1),
                    ),
                    RuntimeEventRecord(
                        sequence=3,
                        event_id="event-text-2",
                        run_id=run_id,
                        event_kind="wire.StreamDelta",
                        payload={
                            "message_type": "StreamDelta",
                            "message": {
                                "content": "after restart.",
                                "role": "assistant",
                            },
                        },
                        created_at=datetime(2026, 1, 2, 3, 5, 2),
                    ),
                    RuntimeEventRecord(
                        sequence=4,
                        event_id="event-end",
                        run_id=run_id,
                        event_kind="wire.TurnEnd",
                        payload={
                            "message_type": "TurnEnd",
                            "message": {"completion_status": "completed"},
                        },
                        created_at=datetime(2026, 1, 2, 3, 5, 3),
                    ),
                ],
            )
        )

        response = await client.get(f"/sessions/{session_id}/result")

        assert response.status_code == 200
        data = response.json()
        assert data["turn_id"] == run_id
        assert data["final_answer"] == "Fixed after restart."
        assert (
            data["verification_summary"]
            == "Tool activity: shell_command: uv run pytest tests/foo.py -q"
        )

    async def test_session_result_includes_recorded_failure_details(
        self, client: AsyncClient
    ) -> None:
        session = register_session(
            "result-failed-session",
            default_run_target=_cloud_run_target(
                CloudWorkspaceRef(
                    workspace_url="docker://agent-ws-result/workspace",
                    workspace_id="ws-failed-result",
                )
            ),
        )
        session.turn_status = "failed"
        session.last_failure_details = "HTTP session turn failed: model timeout"

        response = await client.get(f"/sessions/{session.id}/result")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["turn_status"] == "failed"
        assert data["failure_details"] == "HTTP session turn failed: model timeout"

    async def test_session_result_uses_default_failure_details_when_missing(
        self, client: AsyncClient
    ) -> None:
        session = register_session(
            "result-failed-default-details",
            default_run_target=_cloud_run_target(
                CloudWorkspaceRef(
                    workspace_url="docker://agent-ws-result/workspace",
                    workspace_id="ws-failed-default-result",
                )
            ),
        )
        session.turn_status = "failed"
        session.last_failure_details = None

        response = await client.get(f"/sessions/{session.id}/result")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["turn_status"] == "failed"
        assert (
            data["failure_details"]
            == "Session turn failed; no failure details were recorded."
        )

    async def test_session_result_returns_stable_contract_for_existing_session(
        self, client: AsyncClient
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-result/workspace",
                    workspace_id="ws-result",
                ),
                "provider": "openai",
                "model": "result-model",
                "max_steps": 5,
            },
        )
        session_id = create_resp.json()["session_id"]

        response = await client.get(f"/sessions/{session_id}/result")

        assert response.status_code == 200
        data = response.json()
        assert data == {
            "session_id": session_id,
            "status": "created",
            "turn_status": "idle",
            "turn_id": None,
            "workspace_id": "ws-result",
            "origin": {
                "channel": "http",
                "placement_kind": "cloud_workspace",
                "executor_kind": "local_daemon",
            },
            "provider_name": "openai",
            "model_name": "result-model",
            "final_answer": None,
            "verification_summary": None,
            "failure_details": None,
        }

    async def test_session_result_hides_other_user_session(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        created = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )

        response = await client.get(
            f"/sessions/{created.json()['session_id']}/result",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 404

    async def test_workspace_diff_and_patch_hide_other_user_session(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        created = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-private/workspace",
                    workspace_id="ws-private",
                ),
            },
        )
        session_id = created.json()["session_id"]

        diff = await client.get(
            f"/sessions/{session_id}/workspace/diff",
            headers={"Authorization": "Bearer user-token-a"},
        )
        patch = await client.get(
            f"/sessions/{session_id}/workspace/patch",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert diff.status_code == 404
        assert patch.status_code == 404


class FakeWorkspaceMetadataStore:
    def __init__(self, records: list[WorkspaceRecord]) -> None:
        self.records = records
        self.status_updates: list[tuple[str, str, str | None]] = []
        self.retention_updates: list[tuple[str, str, datetime | None, str]] = []
        self.result_ref_updates: list[tuple[str, dict[str, JSONValue]]] = []

    async def save(self, record: WorkspaceRecord) -> None:
        self.records.append(record)

    async def list(self) -> list[WorkspaceRecord]:
        return self.records

    async def load_by_workspace_id(self, workspace_id: str) -> WorkspaceRecord | None:
        for record in self.records:
            if record.workspace_id == workspace_id:
                return record
        return None

    async def load_for_session_workspace(
        self,
        *,
        session_id: str,
        workspace_id: str,
    ) -> WorkspaceRecord | None:
        for record in self.records:
            if record.session_id == session_id and record.workspace_id == workspace_id:
                return record
        return None

    async def update_status(
        self,
        workspace_record_id: str,
        *,
        status: str,
        cleanup_error: str | None = None,
    ) -> None:
        self.status_updates.append((workspace_record_id, status, cleanup_error))

    async def update_retention(
        self,
        workspace_record_id: str,
        *,
        retention_policy: str,
        expires_at: datetime | None,
        status: str,
    ) -> None:
        self.retention_updates.append(
            (workspace_record_id, retention_policy, expires_at, status)
        )

    async def update_result_refs(
        self,
        workspace_record_id: str,
        *,
        result_refs: dict[str, JSONValue],
    ) -> None:
        self.result_ref_updates.append((workspace_record_id, result_refs))


class TestRemoteWorkspaceRetentionContract:
    async def test_workspace_retain_fails_fast_until_durable_store_exists(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/workspaces/ws-retain/retain",
            json={"retention_policy": "ttl", "ttl_seconds": 3600},
        )

        assert response.status_code == 501
        assert response.json() == {
            "detail": "Durable remote workspace retention is not implemented yet."
        }

    async def test_workspace_pin_fails_fast_until_durable_store_exists(
        self, client: AsyncClient
    ) -> None:
        response = await client.post("/workspaces/ws-retain/pin")

        assert response.status_code == 501
        assert response.json() == {
            "detail": "Durable remote workspace retention is not implemented yet."
        }

    async def test_workspace_unpin_uses_explicit_contract_shape(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/workspaces/ws-retain/unpin",
            json={"retention_policy": "delete_on_close"},
        )

        assert response.status_code == 501
        assert response.json() == {
            "detail": "Durable remote workspace retention is not implemented yet."
        }

    async def test_workspace_retention_rejects_invalid_policy_before_stub(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            "/workspaces/ws-retain/retain",
            json={"retention_policy": "forever"},
        )

        assert response.status_code == 422

    async def test_list_workspaces_uses_durable_records_when_retention_enabled(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider_instance_id": "docker-local"},
        )
        session_manager.configure_workspace_metadata_store(
            FakeWorkspaceMetadataStore(
                [
                    WorkspaceRecord(
                        workspace_record_id="wr-local",
                        workspace_id="ws-local",
                        session_id="session-local",
                        provider="docker",
                        provider_instance_id="docker-local",
                        workspace_root_ref="/workspaces",
                        workspace_host_label="local-host",
                        owner_label="owner:test",
                        source_kind="git",
                        status="retained",
                        retention_policy="pinned",
                        updated_at=datetime(2026, 5, 14, tzinfo=UTC),
                    ),
                    WorkspaceRecord(
                        workspace_record_id="wr-remote",
                        workspace_id="ws-remote",
                        session_id="session-remote",
                        provider="docker",
                        provider_instance_id="docker-remote",
                        workspace_root_ref="/workspaces",
                        workspace_host_label="remote-host",
                        owner_label="owner:test",
                        source_kind="git",
                        status="active",
                        retention_policy="ttl",
                        updated_at=datetime(2026, 5, 14, tzinfo=UTC),
                    ),
                ]
            )
        )

        response = await client.get("/workspaces")

        assert response.status_code == 200
        assert response.json()["workspaces"] == [
            {
                "workspace_id": "ws-local",
                "status": "retained",
                "updated_at": "2026-05-14T00:00:00Z",
                "session_id": "session-local",
                "provider": "docker",
                "provider_instance_id": "docker-local",
                "workspace_host_label": "local-host",
                "source_kind": "git",
                "retention_policy": "pinned",
                "expires_at": None,
                "cleanup_error": None,
                "result_refs": {},
                "is_local": True,
            },
            {
                "workspace_id": "ws-remote",
                "status": "active",
                "updated_at": "2026-05-14T00:00:00Z",
                "session_id": "session-remote",
                "provider": "docker",
                "provider_instance_id": "docker-remote",
                "workspace_host_label": "remote-host",
                "source_kind": "git",
                "retention_policy": "ttl",
                "expires_at": None,
                "cleanup_error": None,
                "result_refs": {},
                "is_local": False,
            },
        ]

    async def test_get_workspace_uses_durable_record_when_retention_enabled(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider_instance_id": "docker-local"},
        )
        session_manager.configure_workspace_metadata_store(
            FakeWorkspaceMetadataStore(
                [
                    WorkspaceRecord(
                        workspace_record_id="wr-1",
                        workspace_id="ws-1",
                        session_id="session-1",
                        provider="docker",
                        provider_instance_id="docker-local",
                        workspace_root_ref="/workspaces",
                        workspace_host_label="local-host",
                        owner_label="owner:test",
                        source_kind="snapshot",
                        status="cleanup_failed",
                        retention_policy="manual",
                        cleanup_error="docker unavailable",
                        updated_at=datetime(2026, 5, 14, tzinfo=UTC),
                    )
                ]
            )
        )

        response = await client.get("/workspaces/ws-1")

        assert response.status_code == 200
        data = response.json()
        assert data["workspace_id"] == "ws-1"
        assert data["status"] == "cleanup_failed"
        assert data["retention_policy"] == "manual"
        assert data["cleanup_error"] == "docker unavailable"
        assert data["is_local"] is True

    async def test_get_workspace_returns_404_for_missing_durable_record(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        session_manager.configure_workspace_metadata_store(
            FakeWorkspaceMetadataStore([])
        )

        response = await client.get("/workspaces/missing")

        assert response.status_code == 404
        assert response.json() == {"detail": "Workspace not found: missing"}

    async def test_delete_workspace_fails_closed_for_foreign_provider_instance(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-remote",
                    workspace_id="ws-remote",
                    session_id="session-remote",
                    provider="docker",
                    provider_instance_id="docker-remote",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="remote-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="manual",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)
        cleanup = MagicMock()
        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_workspace_from_config",
            cleanup,
        )

        response = await client.delete("/workspaces/ws-remote")

        assert response.status_code == 409
        assert response.json() == {
            "detail": (
                "Workspace belongs to a different provider instance and cannot "
                "be operated by this server"
            )
        }
        cleanup.assert_not_called()
        assert store.status_updates == []

    async def test_delete_workspace_updates_local_durable_record(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-local",
                    workspace_id="ws-local",
                    session_id="session-local",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="manual",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        def fake_cleanup(
            config: dict[str, object],
            workspace_id: str,
            *,
            active_workspace_ids: set[str] | None = None,
        ) -> WorkspaceInventoryEntry:
            assert config == {
                "provider": "docker",
                "provider_instance_id": "docker-local",
            }
            assert workspace_id == "ws-local"
            assert active_workspace_ids == set()
            return WorkspaceInventoryEntry(
                workspace_id=workspace_id,
                status="cleaned",
                updated_at=datetime(2026, 5, 14, tzinfo=UTC),
            )

        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_workspace_from_config",
            fake_cleanup,
        )

        response = await client.delete("/workspaces/ws-local")

        assert response.status_code == 200
        assert response.json() == {
            "workspace_id": "ws-local",
            "status": "cleaned",
            "error": None,
        }
        assert store.status_updates == [
            ("wr-local", "cleaning", None),
            ("wr-local", "cleaned", None),
        ]

    async def test_delete_workspace_marks_local_durable_record_lost_on_provider_404(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-local",
                    workspace_id="ws-local",
                    session_id="session-local",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="manual",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        def fake_cleanup(
            config: dict[str, object],
            workspace_id: str,
            *,
            active_workspace_ids: set[str] | None = None,
        ) -> WorkspaceInventoryEntry:
            del config, active_workspace_ids
            raise KeyError(f"workspace not found: {workspace_id}")

        monkeypatch.setattr(
            "coding_agent.server.http_server.cleanup_cloud_workspace_from_config",
            fake_cleanup,
        )

        response = await client.delete("/workspaces/ws-local")

        assert response.status_code == 404
        assert store.status_updates == [
            ("wr-local", "cleaning", None),
            ("wr-local", "lost", "'workspace not found: ws-local'"),
        ]

    async def test_workspace_archive_manifest_fails_closed_for_foreign_provider_instance(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-remote",
                    workspace_id="ws-remote",
                    session_id="session-remote",
                    provider="docker",
                    provider_instance_id="docker-remote",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="remote-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="manual",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)
        manifest = MagicMock()
        monkeypatch.setattr(
            "coding_agent.server.http_server.workspace_archive_manifest_from_config",
            manifest,
        )

        response = await client.get("/workspaces/ws-remote/archive/manifest")

        assert response.status_code == 409
        manifest.assert_not_called()

    async def test_workspace_archive_fails_closed_for_foreign_provider_instance(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-remote",
                    workspace_id="ws-remote",
                    session_id="session-remote",
                    provider="docker",
                    provider_instance_id="docker-remote",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="remote-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="manual",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)
        export_archive = MagicMock()
        monkeypatch.setattr(
            "coding_agent.server.http_server.export_workspace_archive_by_id_from_config",
            export_archive,
        )

        response = await client.get("/workspaces/ws-remote/archive")

        assert response.status_code == 409
        export_archive.assert_not_called()

    async def test_workspace_archive_manifest_uses_local_durable_record(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-local",
                    workspace_id="ws-local",
                    session_id="session-local",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="manual",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        def fake_manifest(
            config: dict[str, object],
            workspace_id: str,
            *,
            session_id: str | None = None,
        ) -> WorkspaceArchiveManifest:
            assert config == {
                "provider": "docker",
                "provider_instance_id": "docker-local",
            }
            assert workspace_id == "ws-local"
            assert session_id is None
            return WorkspaceArchiveManifest(
                workspace_id=workspace_id,
                session_id=None,
                format="tar.gz",
                generated_at=datetime(2026, 5, 14, tzinfo=UTC),
                file_count=1,
                total_bytes=10,
                changed_files=["README.md"],
                deleted_files=[],
                excluded_files=[],
                archive_sha256="abc123",
            )

        monkeypatch.setattr(
            "coding_agent.server.http_server.workspace_archive_manifest_from_config",
            fake_manifest,
        )

        response = await client.get("/workspaces/ws-local/archive/manifest")

        assert response.status_code == 200
        assert response.json()["workspace_id"] == "ws-local"
        assert response.json()["archive_sha256"] == "abc123"

    async def test_workspace_pin_updates_durable_retention_policy(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True, "default_policy": "delete_on_close"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-pin",
                    workspace_id="ws-pin",
                    session_id="session-pin",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="active",
                    retention_policy="delete_on_close",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        response = await client.post("/workspaces/ws-pin/pin")

        assert response.status_code == 200
        assert response.json() == {
            "workspace_id": "ws-pin",
            "retention_policy": "pinned",
            "ttl_seconds": None,
            "status": "retained",
        }
        assert store.retention_updates == [("wr-pin", "pinned", None, "retained")]

    async def test_workspace_retain_ttl_uses_request_ttl(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True, "default_policy": "delete_on_close"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-retain",
                    workspace_id="ws-retain",
                    session_id="session-retain",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="active",
                    retention_policy="delete_on_close",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        response = await client.post(
            "/workspaces/ws-retain/retain",
            json={"retention_policy": "ttl", "ttl_seconds": 3600},
        )

        assert response.status_code == 200
        assert response.json() == {
            "workspace_id": "ws-retain",
            "retention_policy": "ttl",
            "ttl_seconds": 3600,
            "status": "retained",
        }
        assert len(store.retention_updates) == 1
        workspace_record_id, retention_policy, expires_at, status = (
            store.retention_updates[0]
        )
        assert workspace_record_id == "wr-retain"
        assert retention_policy == "ttl"
        assert expires_at is not None
        assert expires_at > datetime.now(UTC)
        assert status == "retained"

    async def test_workspace_unpin_uses_configured_default_policy(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True, "default_policy": "delete_on_close"},
        )
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-unpin",
                    workspace_id="ws-unpin",
                    session_id="session-unpin",
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="pinned",
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        response = await client.post("/workspaces/ws-unpin/unpin")

        assert response.status_code == 200
        assert response.json() == {
            "workspace_id": "ws-unpin",
            "retention_policy": "delete_on_close",
            "ttl_seconds": None,
            "status": "retained",
        }
        assert store.retention_updates == [
            ("wr-unpin", "delete_on_close", None, "retained")
        ]

    async def test_workspace_diff_returns_provider_result(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-diff/workspace",
                    workspace_id="ws-diff",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]

        def fake_workspace_diff(
            config: dict[str, object], workspace_id: str
        ) -> WorkspaceDiff:
            assert config["provider"] == "docker"
            assert workspace_id == "ws-diff"
            return WorkspaceDiff(
                workspace_id=workspace_id,
                files=[
                    WorkspaceDiffFile(
                        path="src/app.py",
                        status="modified",
                        additions=2,
                        deletions=1,
                    )
                ],
                additions=2,
                deletions=1,
            )

        monkeypatch.setattr(
            "coding_agent.server.http_server.workspace_diff_from_config",
            fake_workspace_diff,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker"},
        )

        response = await client.get(f"/sessions/{session_id}/workspace/diff")

        assert response.status_code == 200
        assert response.json() == {
            "session_id": session_id,
            "workspace_id": "ws-diff",
            "files": [
                {
                    "path": "src/app.py",
                    "status": "modified",
                    "old_path": None,
                    "additions": 2,
                    "deletions": 1,
                    "binary": False,
                }
            ],
            "additions": 2,
            "deletions": 1,
        }

    async def test_workspace_diff_returns_local_path_git_changes(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        git_bin = shutil.which("git")
        assert git_bin is not None
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run([git_bin, "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            [git_bin, "config", "user.email", "agent@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [git_bin, "config", "user.name", "Agent"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("old\n", encoding="utf-8")
        subprocess.run([git_bin, "add", "README.md"], cwd=repo, check=True)
        subprocess.run([git_bin, "commit", "-m", "initial"], cwd=repo, check=True)
        workspace = tmp_path / "linked-worktree"
        subprocess.run(
            [git_bin, "worktree", "add", "--detach", str(workspace), "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        assert (workspace / ".git").is_file()
        (workspace / "README.md").write_text("old\nnew\n", encoding="utf-8")
        (workspace / "created.txt").write_text("created\n", encoding="utf-8")
        session = register_session(
            "local-diff-session",
            default_run_target=_local_run_target(workspace),
        )

        response = await client.get(f"/sessions/{session.id}/workspace/diff")

        assert response.status_code == 200
        assert response.json() == {
            "session_id": session.id,
            "workspace_id": str(workspace.resolve()),
            "files": [
                {
                    "path": "README.md",
                    "status": "modified",
                    "old_path": None,
                    "additions": 1,
                    "deletions": 0,
                    "binary": False,
                },
                {
                    "path": "created.txt",
                    "status": "added",
                    "old_path": None,
                    "additions": 1,
                    "deletions": 0,
                    "binary": False,
                },
            ],
            "additions": 2,
            "deletions": 0,
        }

    async def test_workspace_patch_returns_local_path_git_patch(
        self, client: AsyncClient, tmp_path: Path
    ) -> None:
        git_bin = shutil.which("git")
        assert git_bin is not None
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run([git_bin, "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            [git_bin, "config", "user.email", "agent@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [git_bin, "config", "user.name", "Agent"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("old\n", encoding="utf-8")
        subprocess.run([git_bin, "add", "README.md"], cwd=repo, check=True)
        subprocess.run([git_bin, "commit", "-m", "initial"], cwd=repo, check=True)
        workspace = tmp_path / "linked-worktree"
        subprocess.run(
            [git_bin, "worktree", "add", "--detach", str(workspace), "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        assert (workspace / ".git").is_file()
        (workspace / "README.md").write_text("old\nnew\n", encoding="utf-8")
        session = register_session(
            "local-patch-session",
            default_run_target=_local_run_target(workspace),
        )

        response = await client.get(f"/sessions/{session.id}/workspace/patch")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session.id
        assert data["workspace_id"] == str(workspace.resolve())
        assert data["format"] == "unified_diff"
        assert "diff --git a/README.md b/README.md" in data["patch"]
        assert "+new" in data["patch"]

    async def test_workspace_patch_returns_provider_result(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-patch/workspace",
                    workspace_id="ws-patch",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]

        def fake_workspace_patch(
            config: dict[str, object], workspace_id: str
        ) -> WorkspacePatch:
            assert config["provider"] == "docker"
            assert workspace_id == "ws-patch"
            return WorkspacePatch(
                workspace_id=workspace_id,
                format="unified_diff",
                patch="diff --git a/README.md b/README.md\n",
            )

        monkeypatch.setattr(
            "coding_agent.server.http_server.workspace_patch_from_config",
            fake_workspace_patch,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker"},
        )

        response = await client.get(f"/sessions/{session_id}/workspace/patch")

        assert response.status_code == 200
        assert response.json() == {
            "session_id": session_id,
            "workspace_id": "ws-patch",
            "format": "unified_diff",
            "patch": "diff --git a/README.md b/README.md\n",
        }

    async def test_publish_branch_returns_provider_result(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-publish/workspace",
                    workspace_id="ws-publish",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]

        def fake_publish(
            cloud_config: dict[str, object],
            publication_config: dict[str, object],
            workspace_id: str,
            branch_name: str,
            commit_message: str,
        ) -> WorkspaceBranchPublication:
            assert cloud_config["provider"] == "docker"
            assert publication_config["git_author_name"] == "coding-agent"
            assert workspace_id == "ws-publish"
            assert branch_name == "coding-agent/result"
            assert commit_message == (
                f"Apply coding-agent remote session {session_id} changes"
            )
            return WorkspaceBranchPublication(
                workspace_id=workspace_id,
                branch_name=branch_name,
                pushed_ref="refs/heads/coding-agent/result",
                commit_sha="abc123",
                remote_url="https://github.com/org/repo.git",
            )

        mock_publish = MagicMock(side_effect=fake_publish)
        monkeypatch.setattr(
            "coding_agent.server.http_server.publish_workspace_branch_from_config",
            mock_publish,
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker"},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_publication_config",
            lambda: {
                "enabled": True,
                "git_author_name": "coding-agent",
                "git_author_email": "coding-agent@example.com",
                "allowed_git_hosts": ["github.com"],
            },
        )

        response = await client.post(
            f"/sessions/{session_id}/publish",
            json={"mode": "branch", "branch_name": "coding-agent/result"},
        )

        assert response.status_code == 200
        mock_publish.assert_called_once_with(
            {"provider": "docker"},
            {
                "enabled": True,
                "git_author_name": "coding-agent",
                "git_author_email": "coding-agent@example.com",
                "allowed_git_hosts": ["github.com"],
            },
            "ws-publish",
            "coding-agent/result",
            f"Apply coding-agent remote session {session_id} changes",
        )
        assert response.json() == {
            "session_id": session_id,
            "mode": "branch",
            "status": "published",
            "branch_name": "coding-agent/result",
            "pushed_ref": "refs/heads/coding-agent/result",
            "commit_sha": "abc123",
            "remote_url": "https://github.com/org/repo.git",
            "pr_url": None,
            "error": None,
        }

    async def test_publish_branch_persists_workspace_result_refs(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-result-refs/workspace",
                    workspace_id="ws-result-refs",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]
        store = FakeWorkspaceMetadataStore(
            [
                WorkspaceRecord(
                    workspace_record_id="wr-result-refs",
                    workspace_id="ws-result-refs",
                    session_id=session_id,
                    provider="docker",
                    provider_instance_id="docker-local",
                    workspace_root_ref="/workspaces",
                    workspace_host_label="local-host",
                    owner_label="owner:test",
                    source_kind="git",
                    status="retained",
                    retention_policy="manual",
                    result_refs={"existing": "kept"},
                )
            ]
        )
        session_manager.configure_workspace_metadata_store(store)

        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_retention_config",
            lambda: {"enabled": True, "default_policy": "delete_on_close"},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server.publish_workspace_branch_from_config",
            lambda cloud_config, publication_config, workspace_id, branch_name, commit_message: (
                WorkspaceBranchPublication(
                    workspace_id=workspace_id,
                    branch_name=branch_name,
                    pushed_ref="refs/heads/coding-agent/result",
                    commit_sha="abc123",
                    remote_url="https://github.com/org/repo.git",
                )
            ),
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker", "provider_instance_id": "docker-local"},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_publication_config",
            lambda: {
                "enabled": True,
                "git_author_name": "coding-agent",
                "git_author_email": "coding-agent@example.com",
                "allowed_git_hosts": ["github.com"],
            },
        )

        response = await client.post(
            f"/sessions/{session_id}/publish",
            json={"mode": "branch", "branch_name": "coding-agent/result"},
        )

        assert response.status_code == 200
        assert store.result_ref_updates == [
            (
                "wr-result-refs",
                {
                    "existing": "kept",
                    "publication": {
                        "mode": "branch",
                        "status": "published",
                        "branch_name": "coding-agent/result",
                        "pushed_ref": "refs/heads/coding-agent/result",
                        "commit_sha": "abc123",
                        "remote_url": "https://github.com/org/repo.git",
                        "pr_url": None,
                        "error": None,
                        "artifact_ref": {
                            "artifact_id": "workspace:ws-result-refs:publication",
                            "kind": "branch",
                            "title": "Workspace publication",
                            "summary": (
                                "Published branch coding-agent/result at abc123"
                            ),
                            "uri": "https://github.com/org/repo.git",
                            "metadata": {
                                "session_id": session_id,
                                "workspace_id": "ws-result-refs",
                                "mode": "branch",
                                "status": "published",
                                "branch_name": "coding-agent/result",
                                "pushed_ref": "refs/heads/coding-agent/result",
                                "commit_sha": "abc123",
                                "remote_url": "https://github.com/org/repo.git",
                                "pr_url": None,
                                "error": None,
                            },
                            "producer_turn_id": None,
                        },
                    },
                },
            )
        ]
        store.records[0] = replace(
            store.records[0],
            result_refs=store.result_ref_updates[0][1],
            status="cleaned",
        )
        workspace_response = await client.get("/workspaces/ws-result-refs")

        assert workspace_response.status_code == 200
        assert workspace_response.json()["result_refs"] == {
            "existing": "kept",
            "publication": {
                "mode": "branch",
                "status": "published",
                "branch_name": "coding-agent/result",
                "pushed_ref": "refs/heads/coding-agent/result",
                "commit_sha": "abc123",
                "remote_url": "https://github.com/org/repo.git",
                "pr_url": None,
                "error": None,
                "artifact_ref": {
                    "artifact_id": "workspace:ws-result-refs:publication",
                    "kind": "branch",
                    "title": "Workspace publication",
                    "summary": "Published branch coding-agent/result at abc123",
                    "uri": "https://github.com/org/repo.git",
                    "metadata": {
                        "session_id": session_id,
                        "workspace_id": "ws-result-refs",
                        "mode": "branch",
                        "status": "published",
                        "branch_name": "coding-agent/result",
                        "pushed_ref": "refs/heads/coding-agent/result",
                        "commit_sha": "abc123",
                        "remote_url": "https://github.com/org/repo.git",
                        "pr_url": None,
                        "error": None,
                    },
                    "producer_turn_id": None,
                },
            },
        }

    async def test_publish_branch_returns_partial_state_when_push_fails(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-partial/workspace",
                    workspace_id="ws-partial",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]

        monkeypatch.setattr(
            "coding_agent.server.http_server.publish_workspace_branch_from_config",
            lambda cloud_config, publication_config, workspace_id, branch_name, commit_message: (
                WorkspaceBranchPublication(
                    workspace_id=workspace_id,
                    branch_name=branch_name,
                    pushed_ref="refs/heads/coding-agent/result",
                    commit_sha="abc123",
                    remote_url="https://github.com/org/repo.git",
                    status="partial",
                    error="git push failed",
                )
            ),
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker"},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_publication_config",
            lambda: {
                "enabled": True,
                "git_author_name": "coding-agent",
                "git_author_email": "coding-agent@example.com",
                "allowed_git_hosts": ["github.com"],
            },
        )

        response = await client.post(
            f"/sessions/{session_id}/publish",
            json={"mode": "branch", "branch_name": "coding-agent/result"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "session_id": session_id,
            "mode": "branch",
            "status": "partial",
            "branch_name": "coding-agent/result",
            "pushed_ref": "refs/heads/coding-agent/result",
            "commit_sha": "abc123",
            "remote_url": "https://github.com/org/repo.git",
            "pr_url": None,
            "error": "git push failed",
        }

    async def test_publish_pr_creates_github_pr_after_branch_publication(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-publish/workspace",
                    workspace_id="ws-publish",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]
        monkeypatch.setenv("CODING_AGENT_GITHUB_TOKEN", "github-token")
        github_calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

        def fake_publish(
            cloud_config: dict[str, object],
            publication_config: dict[str, object],
            workspace_id: str,
            branch_name: str,
            commit_message: str,
        ) -> WorkspaceBranchPublication:
            assert cloud_config["provider"] == "docker"
            assert publication_config["git_author_name"] == "coding-agent"
            assert workspace_id == "ws-publish"
            assert branch_name == "coding-agent/result"
            assert commit_message == (
                f"Apply coding-agent remote session {session_id} changes"
            )
            return WorkspaceBranchPublication(
                workspace_id=workspace_id,
                branch_name=branch_name,
                pushed_ref="refs/heads/coding-agent/result",
                commit_sha="abc123",
                remote_url="https://github.com/org/repo.git",
            )

        class FakeGitHubResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"html_url": "https://github.com/org/repo/pull/12"}

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                assert timeout == 30.0

            async def __aenter__(self) -> FakeAsyncClient:
                return self

            async def __aexit__(
                self, exc_type: object, exc: object, tb: object
            ) -> None:
                return None

            async def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
            ) -> FakeGitHubResponse:
                github_calls.append((url, headers, json))
                return FakeGitHubResponse()

        monkeypatch.setattr(
            "coding_agent.server.http_server.publish_workspace_branch_from_config",
            MagicMock(side_effect=fake_publish),
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker"},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_publication_config",
            lambda: {
                "enabled": True,
                "git_author_name": "coding-agent",
                "git_author_email": "coding-agent@example.com",
                "allowed_git_hosts": ["github.com"],
                "github": {
                    "enabled": True,
                    "token_env": "CODING_AGENT_GITHUB_TOKEN",
                    "base_branch": "main",
                },
            },
        )
        monkeypatch.setattr(
            http_server,
            "httpx",
            SimpleNamespace(AsyncClient=FakeAsyncClient),
            raising=False,
        )

        response = await client.post(
            f"/sessions/{session_id}/publish",
            json={"mode": "pr", "branch_name": "coding-agent/result"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "session_id": session_id,
            "mode": "pr",
            "status": "published",
            "branch_name": "coding-agent/result",
            "pushed_ref": "refs/heads/coding-agent/result",
            "commit_sha": "abc123",
            "remote_url": "https://github.com/org/repo.git",
            "pr_url": "https://github.com/org/repo/pull/12",
            "error": None,
        }
        assert github_calls == [
            (
                "https://api.github.com/repos/org/repo/pulls",
                {
                    "Accept": "application/vnd.github+json",
                    "Authorization": "Bearer github-token",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                {
                    "title": f"Coding agent remote session {session_id}",
                    "head": "coding-agent/result",
                    "base": "main",
                    "body": (
                        f"Remote coding-agent session `{session_id}` published "
                        "commit `abc123`."
                    ),
                },
            )
        ]

    async def test_publish_pr_returns_branch_metadata_when_github_not_configured(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-publish/workspace",
                    workspace_id="ws-publish",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]

        def fake_publish(
            cloud_config: dict[str, object],
            publication_config: dict[str, object],
            workspace_id: str,
            branch_name: str,
            commit_message: str,
        ) -> WorkspaceBranchPublication:
            del cloud_config, publication_config, commit_message
            assert workspace_id == "ws-publish"
            assert branch_name == f"coding-agent/session-{session_id}"
            return WorkspaceBranchPublication(
                workspace_id=workspace_id,
                branch_name=branch_name,
                pushed_ref=f"refs/heads/coding-agent/session-{session_id}",
                commit_sha="abc123",
                remote_url="https://github.com/org/repo.git",
            )

        monkeypatch.setattr(
            "coding_agent.server.http_server.publish_workspace_branch_from_config",
            MagicMock(side_effect=fake_publish),
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker"},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_publication_config",
            lambda: {
                "enabled": True,
                "git_author_name": "coding-agent",
                "git_author_email": "coding-agent@example.com",
                "allowed_git_hosts": ["github.com"],
            },
        )

        response = await client.post(
            f"/sessions/{session_id}/publish",
            json={"mode": "pr"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload == {
            "session_id": session_id,
            "mode": "pr",
            "status": "unsupported",
            "branch_name": f"coding-agent/session-{session_id}",
            "pushed_ref": f"refs/heads/coding-agent/session-{session_id}",
            "commit_sha": "abc123",
            "remote_url": "https://github.com/org/repo.git",
            "pr_url": None,
            "error": (
                "remote_publication.github.enabled=true is required for "
                "GitHub PR publication; branch was published and can be opened "
                "manually"
            ),
        }

    async def test_publish_pr_returns_branch_metadata_when_github_api_fails(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-publish/workspace",
                    workspace_id="ws-publish",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]
        monkeypatch.setenv("CODING_AGENT_GITHUB_TOKEN", "github-token")

        def fake_publish(
            cloud_config: dict[str, object],
            publication_config: dict[str, object],
            workspace_id: str,
            branch_name: str,
            commit_message: str,
        ) -> WorkspaceBranchPublication:
            del cloud_config, publication_config, commit_message
            assert workspace_id == "ws-publish"
            assert branch_name == "coding-agent/result"
            return WorkspaceBranchPublication(
                workspace_id=workspace_id,
                branch_name=branch_name,
                pushed_ref="refs/heads/coding-agent/result",
                commit_sha="abc123",
                remote_url="https://github.com/org/repo.git",
            )

        class FakeGitHubResponse:
            def raise_for_status(self) -> None:
                raise RuntimeError("github unavailable")

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                assert timeout == 30.0

            async def __aenter__(self) -> FakeAsyncClient:
                return self

            async def __aexit__(
                self, exc_type: object, exc: object, tb: object
            ) -> None:
                return None

            async def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, object],
            ) -> FakeGitHubResponse:
                del url, headers, json
                return FakeGitHubResponse()

        monkeypatch.setattr(
            "coding_agent.server.http_server.publish_workspace_branch_from_config",
            MagicMock(side_effect=fake_publish),
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker"},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_publication_config",
            lambda: {
                "enabled": True,
                "git_author_name": "coding-agent",
                "git_author_email": "coding-agent@example.com",
                "allowed_git_hosts": ["github.com"],
                "github": {
                    "enabled": True,
                    "token_env": "CODING_AGENT_GITHUB_TOKEN",
                    "base_branch": "main",
                },
            },
        )
        monkeypatch.setattr(
            http_server,
            "httpx",
            SimpleNamespace(AsyncClient=FakeAsyncClient),
            raising=False,
        )

        response = await client.post(
            f"/sessions/{session_id}/publish",
            json={"mode": "pr", "branch_name": "coding-agent/result"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "session_id": session_id,
            "mode": "pr",
            "status": "failed",
            "branch_name": "coding-agent/result",
            "pushed_ref": "refs/heads/coding-agent/result",
            "commit_sha": "abc123",
            "remote_url": "https://github.com/org/repo.git",
            "pr_url": None,
            "error": "GitHub PR publication failed: github unavailable",
        }

    def test_github_repo_from_remote_url_accepts_scp_style_remote(self) -> None:
        assert http_server._github_repo_from_remote_url(
            "git@github.com:org/repo.git"
        ) == ("org", "repo")

    async def test_publish_branch_requires_publication_config(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        create_resp = await client.post(
            "/sessions",
            json={
                "run_target": _cloud_run_target_payload(
                    workspace_url="docker://agent-ws-publish/workspace",
                    workspace_id="ws-publish",
                ),
            },
        )
        session_id = create_resp.json()["session_id"]
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_cloud_workspace_config",
            lambda: {"provider": "docker"},
        )
        monkeypatch.setattr(
            "coding_agent.server.http_server._load_remote_publication_config",
            lambda: {},
        )

        response = await client.post(
            f"/sessions/{session_id}/publish",
            json={"mode": "branch", "branch_name": "coding-agent/result"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "remote_publication.enabled must be true"

    async def test_publish_hides_other_user_session(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        created = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )
        session_id = created.json()["session_id"]

        response = await client.post(
            f"/sessions/{session_id}/publish",
            headers={"Authorization": "Bearer user-token-a"},
            json={"mode": "branch", "branch_name": "test"},
        )

        assert response.status_code == 404

    async def test_remote_result_publication_operations_return_404_for_missing_session(
        self, client: AsyncClient
    ) -> None:
        result = await client.get("/sessions/missing/result")
        diff = await client.get("/sessions/missing/workspace/diff")
        patch = await client.get("/sessions/missing/workspace/patch")
        publish = await client.post("/sessions/missing/publish", json={"mode": "pr"})

        assert result.status_code == 404
        assert diff.status_code == 404
        assert patch.status_code == 404
        assert publish.status_code == 404


class TestCancelSession:
    async def test_cancel_session_turn_returns_cancelling_for_active_turn(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]
        session = session_manager.get_session(session_id)
        task = asyncio.create_task(asyncio.sleep(60))
        session.task = task
        session.turn_in_progress = True
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        session.event_queues = [queue]

        response = await client.post(f"/sessions/{session_id}/cancel")

        assert response.status_code == 202
        data = response.json()
        assert data["session_id"] == session_id
        assert data["status"] == "cancelling"
        event = await asyncio.wait_for(queue.get(), timeout=1)
        assert event["event"] == "TurnCancelling"

        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_cancel_session_turn_is_idempotent_for_idle_session(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        response = await client.post(f"/sessions/{session_id}/cancel")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["status"] == "idle"

    async def test_cancel_session_turn_rejects_non_owner(
        self, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(
            "CODING_AGENT_SERVER_CONFIG", str(_write_auth_config(tmp_path))
        )
        monkeypatch.setattr(settings, "http_api_key", None)

        created = await client.post(
            "/sessions",
            headers={"Authorization": "Bearer admin-token"},
            json={},
        )

        response = await client.post(
            f"/sessions/{created.json()['session_id']}/cancel",
            headers={"Authorization": "Bearer user-token-a"},
        )

        assert response.status_code == 404


class TestCloseSession:
    """Tests for close session endpoint."""

    async def test_close_session_not_found(self, client):
        """Test 404 when session doesn't exist."""
        response = await client.delete("/sessions/nonexistent")
        assert response.status_code == 404

    async def test_close_session_success(self, client):
        """Test closing a session."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "closed"
        assert data["session_id"] == session_id
        assert not session_manager.has_session(session_id)

    async def test_close_session_broadcasts_event(self, client):
        """Test that closing session broadcasts to event queues."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Add a queue to receive events
        queue = asyncio.Queue()
        session_manager.get_session(session_id).event_queues = [queue]

        # Close the session
        await client.delete(f"/sessions/{session_id}")

        # The queue should have received SessionClosed event
        received_events = []
        while not queue.empty():
            received_events.append(await queue.get())

        assert any(e["event"] == "SessionClosed" for e in received_events)

    async def test_close_session_returns_error_when_manager_close_fails(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_close_session(current_session_id: str) -> None:
            assert current_session_id == session_id
            raise RuntimeError("close exploded")

        monkeypatch.setattr(session_manager, "close_session", failing_close_session)

        response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"

    async def test_close_session_returns_404_when_session_disappears_during_close(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def fake_has_session_async(current_session_id: str) -> bool:
            assert current_session_id == session_id
            return True

        async def fake_get_session_async(current_session_id: str):
            assert current_session_id == session_id
            return session_manager.get_session(current_session_id)

        async def disappearing_close_session(current_session_id: str) -> None:
            assert current_session_id == session_id
            raise KeyError(f"Session not found: {session_id}")

        monkeypatch.setattr(
            session_manager, "has_session_async", fake_has_session_async
        )
        monkeypatch.setattr(
            session_manager, "get_session_async", fake_get_session_async
        )
        monkeypatch.setattr(
            session_manager, "close_session", disappearing_close_session
        )

        response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == f"Session not found: {session_id}"

    async def test_close_session_returns_409_for_stale_owner_conflict(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def conflicting_close_session(current_session_id: str) -> None:
            assert current_session_id == session_id
            raise SessionOwnershipConflictError("stale owner or fencing token rejected")

        monkeypatch.setattr(session_manager, "close_session", conflicting_close_session)

        response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 409
        assert response.json()["detail"] == "stale owner or fencing token rejected"

    async def test_close_session_returns_404_when_session_disappears_before_load(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def fake_has_session_async(current_session_id: str) -> bool:
            assert current_session_id == session_id
            return True

        async def disappearing_get_session_async(current_session_id: str):
            assert current_session_id == session_id
            raise KeyError(f"Session not found: {session_id}")

        monkeypatch.setattr(
            session_manager, "has_session_async", fake_has_session_async
        )
        monkeypatch.setattr(
            session_manager, "get_session_async", disappearing_get_session_async
        )

        response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == f"Session not found: {session_id}"

    async def test_close_session_hides_unexpected_internal_error_detail(
        self, client, monkeypatch, caplog
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_close_session(current_session_id: str) -> None:
            assert current_session_id == session_id
            raise RuntimeError("dsn=postgresql://user:secret@example/db")

        monkeypatch.setattr(session_manager, "close_session", failing_close_session)

        with caplog.at_level("ERROR"):
            response = await client.delete(f"/sessions/{session_id}")

        assert response.status_code == 500
        assert response.json()["detail"] == "Internal server error"
        assert "dsn=postgresql://user:secret@example/db" not in response.text
        assert "Unexpected error while closing session" in caplog.text


class TestSessionTimeout:
    """Tests for session idle timeout."""

    async def test_session_marked_expired_after_timeout(self):
        """Test that old sessions are marked for cleanup."""
        session_id = "test_session"
        old_time = datetime.now() - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES + 1)
        session = register_session(
            session_id,
            created_at=old_time,
            last_activity=old_time,
        )

        # Check that session is old enough to expire
        now = datetime.now()
        idle_time = now - session.last_activity
        assert idle_time > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)

    async def test_session_not_expired_if_active(self):
        """Test that active sessions are not expired."""
        session_id = "test_session"
        session = register_session(session_id)

        now = datetime.now()
        idle_time = now - session.last_activity
        assert idle_time < timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES)


class TestWireMessageConversion:
    """Tests for wire message to SSE event conversion."""

    def test_turn_end_conversion(self):
        """Test TurnEnd message conversion."""
        msg = TurnEnd(
            session_id="test123",
            turn_id="turn456",
            completion_status=CompletionStatus.COMPLETED,
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "TurnEnd"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["turn_id"] == "turn456"
        assert data["completion_status"] == "completed"

    def test_stream_delta_conversion(self):
        """Test StreamDelta message conversion."""
        msg = StreamDelta(
            session_id="test123",
            agent_id="child-1",
            content="Hello world",
            role="assistant",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "StreamDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-1"
        assert data["content"] == "Hello world"
        assert data["role"] == "assistant"

    def test_tool_call_delta_conversion(self):
        """Test ToolCallDelta message conversion."""
        msg = ToolCallDelta(
            session_id="test123",
            agent_id="child-2",
            tool_name="bash",
            arguments={"command": "ls"},
            call_id="call1",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ToolCallDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-2"
        assert data["tool_name"] == "bash"
        assert data["call_id"] == "call1"
        assert data["arguments"]["command"] == "ls"

    def test_tool_result_delta_conversion_redacts_raw_result_payload(self):
        msg = ToolResultDelta(
            session_id="test123",
            agent_id="child-3",
            call_id="call1",
            tool_name="bash_run",
            result={"stdout": "SECRET=abc123", "stderr": "", "exit_code": 0},
            display_result="command succeeded",
        )

        event = _wire_message_to_event(msg)

        assert event["event"] == "ToolResultDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-3"
        assert data["call_id"] == "call1"
        assert data["tool_name"] == "bash_run"
        assert data["display_result"] == "command succeeded"
        assert data["is_error"] is False
        assert data["result"] is None

    def test_approval_request_conversion(self):
        """Test ApprovalRequest message conversion."""
        tool_call = ToolCallDelta(
            session_id="test123",
            agent_id="child-4",
            tool_name="bash",
            arguments={"command": "rm -rf /"},
            call_id="call1",
        )
        msg = ApprovalRequest(
            session_id="test123",
            agent_id="child-4",
            request_id="req1",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ApprovalRequest"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-4"
        assert data["request_id"] == "req1"
        assert data["timeout_seconds"] == 120
        assert data["tool_call"]["tool_name"] == "bash"


class TestCheckpointErrorMapping:
    async def test_capture_checkpoint_returns_409_for_active_turn(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_capture(*args, **kwargs):
            raise RuntimeError("turn already in progress")

        monkeypatch.setattr(session_manager, "capture_checkpoint", failing_capture)

        response = await client.post(f"/sessions/{session_id}/checkpoints", json={})

        assert response.status_code == 409
        assert response.json()["detail"] == "turn already in progress"

    async def test_restore_checkpoint_returns_unquoted_keyerror_detail(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_restore(*args, **kwargs):
            raise KeyError("Checkpoint cp-missing not found")

        monkeypatch.setattr(session_manager, "restore_checkpoint", failing_restore)

        response = await client.post(
            f"/sessions/{session_id}/checkpoints/cp-missing/restore"
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Checkpoint cp-missing not found"

    async def test_restore_checkpoint_maps_typeerror_to_bad_request(
        self, client, monkeypatch
    ):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        async def failing_restore(*args, **kwargs):
            raise TypeError("checkpoint session config is missing model_name")

        monkeypatch.setattr(session_manager, "restore_checkpoint", failing_restore)

        response = await client.post(
            f"/sessions/{session_id}/checkpoints/cp-invalid/restore"
        )

        assert response.status_code == 400
        assert (
            response.json()["detail"]
            == "checkpoint session config is missing model_name"
        )

    def test_approval_response_conversion(self):
        """Test ApprovalResponse conversion."""
        msg = ApprovalResponse(
            session_id="test123",
            agent_id="child-5",
            request_id="req1",
            approved=True,
            feedback="Looks good",
        )
        event = _wire_message_to_event(msg)
        assert event["event"] == "ApprovalResponse"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-5"
        assert data["request_id"] == "req1"
        assert data["approved"] is True
        assert data["feedback"] == "Looks good"

    def test_thinking_delta_conversion(self):
        msg = ThinkingDelta(
            session_id="test123",
            agent_id="child-6",
            text="reasoning about the next step",
        )

        event = _wire_message_to_event(msg)

        assert event["event"] == "ThinkingDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-6"
        assert data["text"] == "reasoning about the next step"

    def test_turn_status_delta_conversion(self):
        msg = TurnStatusDelta(
            session_id="test123",
            agent_id="child-7",
            phase="idle",
            elapsed_seconds=1.5,
            tokens_in=123,
            tokens_out=45,
            model_name="kimi-for-coding",
            context_percent=12.5,
        )

        event = _wire_message_to_event(msg)

        assert event["event"] == "TurnStatusDelta"
        data = json.loads(event["data"])
        assert data["session_id"] == "test123"
        assert data["agent_id"] == "child-7"
        assert data["phase"] == "idle"
        assert data["elapsed_seconds"] == 1.5
        assert data["tokens_in"] == 123
        assert data["tokens_out"] == 45
        assert data["model_name"] == "kimi-for-coding"
        assert data["context_percent"] == 12.5


class TestWireStreamingBehavior:
    async def test_stream_wire_messages_does_not_stop_on_child_turn_end(self):
        wire = LocalWire("parent-session")

        async def produce() -> None:
            await wire.send(
                TurnEnd(
                    session_id="parent-session",
                    agent_id="child-1",
                    turn_id="child-turn",
                    completion_status=CompletionStatus.COMPLETED,
                )
            )
            await wire.send(
                ToolResultDelta(
                    session_id="parent-session",
                    tool_name="subagent",
                    call_id="tc-subagent",
                    result="Subagent completed: Child finished summary",
                    display_result="Subagent completed: Child finished summary",
                )
            )
            await wire.send(
                TurnEnd(
                    session_id="parent-session",
                    agent_id="",
                    turn_id="parent-turn",
                    completion_status=CompletionStatus.COMPLETED,
                )
            )

        producer = asyncio.create_task(produce())
        events = []
        async for event in stream_wire_messages(wire):
            events.append(event)
        await producer

        assert [event["event"] for event in events] == [
            "TurnEnd",
            "ToolResultDelta",
            "TurnEnd",
        ]

    async def test_stream_wire_messages_reports_task_failure_before_wire_output(self):
        wire = LocalWire("parent-session")

        async def fail_before_output() -> None:
            raise RuntimeError("owner rejected")

        producer = asyncio.create_task(fail_before_output())
        events = []
        async for event in stream_wire_messages(wire, producer):
            events.append(event)

        assert [event["event"] for event in events] == ["Error"]
        assert "owner rejected" in json.loads(events[0]["data"])["error"]


class TestSessionToDict:
    """Tests for session serialization."""

    def test_session_to_dict(self):
        """Test session state to dictionary conversion."""
        session = Session(
            id="test123",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            last_activity=datetime(2024, 1, 1, 12, 30, 0),
            turn_in_progress=True,
            pending_approval={"call_id": "req1"},
        )
        data = _session_to_dict(session)
        assert data["id"] == "test123"
        assert data["turn_in_progress"] is True
        assert data["pending_approval"] is True
        assert "2024-01-01" in data["created_at"]


class TestBroadcastEvent:
    """Tests for event broadcasting."""

    async def test_broadcast_to_multiple_queues(self):
        """Test that events are broadcast to all queues."""
        session = register_session("test")
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        session.event_queues = [queue1, queue2]

        event = {"event": "Test", "data": "{}"}
        await _broadcast_event(session, event)

        assert await queue1.get() == event
        assert await queue2.get() == event

    async def test_broadcast_uses_provided_session_without_manager_lookup(self):
        session = register_session("broadcast-without-lookup")
        queue = asyncio.Queue()
        session.event_queues = [queue]
        event = {"event": "Test", "data": "{}"}

        with patch.object(
            session_manager,
            "broadcast_event",
            side_effect=AssertionError("manager lookup should be skipped"),
        ):
            await _broadcast_event(session, event)

        assert await queue.get() == event

    async def test_broadcast_prunes_full_queue_without_blocking(self):
        session = register_session("broadcast-full-queue")
        full_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)
        await full_queue.put({"event": "Old", "data": "{}"})
        healthy_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)
        session.event_queues = [full_queue, healthy_queue]
        event = {"event": "Test", "data": "{}"}

        await _broadcast_event(session, event)

        assert session.event_queues == [healthy_queue]
        assert full_queue.qsize() == 1
        assert await healthy_queue.get() == event

    async def test_session_broadcast_result_counts_pruned_queues(self):
        session = register_session("broadcast-result-counts")
        full_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)
        await full_queue.put({"event": "Old", "data": "{}"})

        class BrokenQueue:
            def put_nowait(self, item: object) -> None:
                _ = item
                raise RuntimeError("queue closed")

        healthy_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)
        broken_queue = cast(asyncio.Queue[dict[str, str]], cast(object, BrokenQueue()))
        session.event_queues = [full_queue, broken_queue, healthy_queue]
        event = {"event": "Test", "data": "{}"}

        result = session.broadcast_event_nowait(event)

        assert result.delivered_count == 1
        assert result.full_pruned_count == 1
        assert result.failed_pruned_count == 1
        assert session.event_queues == [healthy_queue]
        assert full_queue.qsize() == 1
        assert await healthy_queue.get() == event

    async def test_broadcast_prunes_failed_queue(self):
        session = register_session("broadcast-failed-queue")

        class BrokenQueue:
            def put_nowait(self, item: object) -> None:
                _ = item
                raise RuntimeError("queue closed")

        healthy_queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=1)
        broken_queue = cast(asyncio.Queue[dict[str, str]], cast(object, BrokenQueue()))
        session.event_queues = [broken_queue, healthy_queue]
        event = {"event": "Test", "data": "{}"}

        await _broadcast_event(session, event)

        assert session.event_queues == [healthy_queue]
        assert await healthy_queue.get() == event


class TestWaitForApproval:
    """Tests for the approval wait function."""

    async def test_wait_for_approval_session_not_found(self):
        """Test handling when session doesn't exist."""
        tool_call = ToolCallDelta(
            session_id="nonexistent",
            tool_name="bash",
            arguments={},
            call_id="call1",
        )
        req = ApprovalRequest(
            session_id="nonexistent",
            request_id="req1",
            tool_call=tool_call,
        )
        response = await wait_for_approval("nonexistent", req)
        assert isinstance(response, ApprovalResponse)
        assert response.approved is False
        assert response.feedback == "Session not found"

    async def test_wait_for_approval_timeout(self):
        """Test that approval times out correctly."""
        session_id = "test_session"
        register_session(session_id, turn_in_progress=True)

        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={},
            call_id="call1",
        )
        req = ApprovalRequest(
            session_id=session_id,
            request_id="req1",
            tool_call=tool_call,
        )

        # Use a very short timeout for testing
        import coding_agent.server.http_server as http_server

        original_timeout = http_server.APPROVAL_TIMEOUT_SECONDS
        http_server.APPROVAL_TIMEOUT_SECONDS = 0.1

        try:
            response = await wait_for_approval(session_id, req)
            assert response.approved is False
            assert response.feedback is not None
            assert "timeout" in response.feedback.lower()
        finally:
            http_server.APPROVAL_TIMEOUT_SECONDS = original_timeout

    async def test_wait_for_approval_request_can_be_approved_via_http_endpoint(
        self, client
    ):
        import coding_agent.server.http_server as http_server

        session_id = "http-wait-approval"
        register_session(session_id, turn_in_progress=True)

        req = ApprovalRequest(
            session_id=session_id,
            request_id="req-http-wait",
            tool_call=ToolCallDelta(
                session_id=session_id,
                tool_name="bash",
                arguments={"command": "pwd"},
                call_id="call-http-wait",
            ),
            timeout_seconds=1,
        )

        original_timeout = http_server.APPROVAL_TIMEOUT_SECONDS
        http_server.APPROVAL_TIMEOUT_SECONDS = 0.2

        try:
            wait_task = asyncio.create_task(wait_for_approval(session_id, req))
            for _ in range(20):
                if (
                    session_manager.get_session(
                        session_id
                    ).approval_coordinator.get_request("req-http-wait")
                    is not None
                ):
                    break
                await asyncio.sleep(0)
            else:
                pytest.fail("approval request was not registered")

            response = await client.post(
                f"/sessions/{session_id}/approve",
                json={
                    "request_id": "req-http-wait",
                    "approved": True,
                    "feedback": "approved over http",
                },
            )

            approval_response = await wait_task
        finally:
            http_server.APPROVAL_TIMEOUT_SECONDS = original_timeout

        assert response.status_code == 200, response.text
        assert approval_response.approved is True
        assert approval_response.feedback == "approved over http"


def test_http_server_import_falls_back_when_agent_toml_is_unreadable(
    monkeypatch,
) -> None:
    original_module = sys.modules.get("coding_agent.server.http_server")
    server_module = sys.modules.get("coding_agent.server")
    original_parent_attr = (
        getattr(server_module, "http_server", None)
        if server_module is not None
        else None
    )
    monkeypatch.delitem(sys.modules, "coding_agent.server.http_server", raising=False)
    if server_module is not None:
        monkeypatch.delattr(server_module, "http_server", raising=False)

    try:
        with patch("agentkit.config.loader.load_config") as load_config:
            load_config.side_effect = ConfigError(
                "config file not found: /tmp/missing-agent.toml"
            )
            http_server = importlib.import_module("coding_agent.server.http_server")

        assert http_server._load_storage_config() == {}
        assert (
            http_server.session_manager._storage_config == local_sqlite_storage_config()
        )
    finally:
        if original_module is None:
            monkeypatch.delitem(
                sys.modules, "coding_agent.server.http_server", raising=False
            )
        else:
            sys.modules["coding_agent.server.http_server"] = original_module
        if server_module is not None and original_parent_attr is not None:
            setattr(server_module, "http_server", original_parent_attr)


def test_agent_toml_storage_paths_local_drives_sqlite_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_path = tmp_path / "custom-local.sqlite3"
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        _minimal_agent_toml(
            f"""

[storage]
http_session_backend = "sqlite"
tape_backend = "sqlite"
checkpoint_backend = "sqlite"
runtime_backend = "sqlite"

[storage.paths]
local = "{local_path}"
"""
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(config_path))

    storage_config = http_server._load_storage_config()
    manager = SessionManager(storage_config=storage_config)

    try:
        assert storage_config["paths"]["local"] == str(local_path)
        assert manager._store._path == local_path
        assert manager._tape_store._path == local_path
        assert manager._checkpoint_service._store._path == local_path
        assert manager._runtime_store._path == local_path
    finally:
        asyncio.run(manager.close())


def test_build_session_manager_partial_sqlite_storage_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_path = tmp_path / "local.sqlite3"
    runtime_path = tmp_path / "runtime"
    config_path = tmp_path / "agent.toml"
    config_path.write_text(
        _minimal_agent_toml(
            f"""

[storage]
http_session_backend = "sqlite"
http_session_path = "{local_path}"
tape_backend = "sqlite"
tape_path = "{local_path}"
checkpoint_backend = "sqlite"
checkpoint_path = "{local_path}"
runtime_backend = "jsonl"
runtime_path = "{runtime_path}"
"""
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODING_AGENT_SERVER_CONFIG", str(config_path))

    with pytest.raises(ConfigError) as exc_info:
        http_server._build_session_manager()

    message = str(exc_info.value)
    assert "durable fencing requires all local sqlite backends" in message
    assert "runtime_backend='jsonl'" in message


def test_http_server_import_raises_on_invalid_agent_toml(monkeypatch) -> None:
    original_module = sys.modules.get("coding_agent.server.http_server")
    server_module = sys.modules.get("coding_agent.server")
    original_parent_attr = (
        getattr(server_module, "http_server", None)
        if server_module is not None
        else None
    )
    monkeypatch.delitem(sys.modules, "coding_agent.server.http_server", raising=False)
    if server_module is not None:
        monkeypatch.delattr(server_module, "http_server", raising=False)

    try:
        with patch("agentkit.config.loader.load_config") as load_config:
            load_config.side_effect = ConfigError("missing [agent] section")
            with pytest.raises(ConfigError, match=r"missing \[agent\] section"):
                importlib.import_module("coding_agent.server.http_server")
    finally:
        if original_module is None:
            monkeypatch.delitem(
                sys.modules, "coding_agent.server.http_server", raising=False
            )
        else:
            sys.modules["coding_agent.server.http_server"] = original_module
        if server_module is not None and original_parent_attr is not None:
            setattr(server_module, "http_server", original_parent_attr)


def test_cli_module_import_does_not_eagerly_import_http_server(monkeypatch) -> None:
    del monkeypatch
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import coding_agent.__main__; "
            "print('coding_agent.server.http_server' in sys.modules)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


class TestIntegration:
    """Integration tests for the full flow."""

    async def test_full_session_lifecycle(self, client):
        """Test full session lifecycle: create -> prompt -> get -> close."""
        # Create session
        response = await client.post("/sessions", json={})
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        # Get session info
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["id"] == session_id

        # Send prompt
        events = []
        async with aconnect_sse(
            client,
            "POST",
            f"/sessions/{session_id}/prompt",
            json={"prompt": "Hello"},
        ) as event_source:
            async for sse in event_source.aiter_sse():
                events.append(sse.event)
                if sse.event == "TurnEnd":
                    break

        assert "TurnEnd" in events

        # Close session
        response = await client.delete(f"/sessions/{session_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "closed"

        # Verify session is gone
        response = await client.get(f"/sessions/{session_id}")
        assert response.status_code == 404


class TestCheckpointEndpoints:
    async def test_capture_checkpoint_returns_session_scoped_metadata(self, client):
        session_id = "capture-http-session"
        register_session(session_id)

        expected = CheckpointMeta(
            checkpoint_id="cp-http-capture",
            tape_id="stable-tape",
            session_id=session_id,
            entry_count=4,
            window_start=1,
            created_at=datetime(2026, 4, 16, 9, 30, 0),
            label="before-http-save",
        )

        async def fake_capture_checkpoint(
            requested_session_id: str,
            *,
            label: str | None = None,
            extra=None,
        ):
            assert requested_session_id == session_id
            assert label == "before-http-save"
            assert extra is None
            return expected

        with patch.object(
            session_manager,
            "capture_checkpoint",
            side_effect=fake_capture_checkpoint,
        ):
            response = await client.post(
                f"/sessions/{session_id}/checkpoints",
                json={"label": "before-http-save"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "checkpoint_id": "cp-http-capture",
            "tape_id": "stable-tape",
            "session_id": session_id,
            "entry_count": 4,
            "window_start": 1,
            "created_at": "2026-04-16T09:30:00",
            "label": "before-http-save",
        }

    async def test_capture_checkpoint_returns_404_for_unknown_session(self, client):
        response = await client.post(
            "/sessions/missing-session/checkpoints",
            json={"label": "before-http-save"},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"

    async def test_list_checkpoints_returns_session_scoped_metadata(self, client):
        session_id = "checkpoint-http-session"
        register_session(session_id)

        expected = CheckpointMeta(
            checkpoint_id="cp-http-1",
            tape_id="stable-tape",
            session_id=session_id,
            entry_count=4,
            window_start=1,
            created_at=datetime(2026, 4, 16, 10, 0, 0),
            label="before-http-restore",
        )

        async def fake_list_checkpoints(requested_session_id: str):
            assert requested_session_id == session_id
            return [expected]

        with patch.object(
            session_manager,
            "list_checkpoints",
            side_effect=fake_list_checkpoints,
        ):
            response = await client.get(f"/sessions/{session_id}/checkpoints")

        assert response.status_code == 200
        assert response.json() == {
            "checkpoints": [
                {
                    "checkpoint_id": "cp-http-1",
                    "tape_id": "stable-tape",
                    "session_id": session_id,
                    "entry_count": 4,
                    "window_start": 1,
                    "created_at": "2026-04-16T10:00:00",
                    "label": "before-http-restore",
                }
            ]
        }

    async def test_list_checkpoints_returns_404_for_unknown_session(self, client):
        response = await client.get("/sessions/missing-session/checkpoints")

        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"

    async def test_restore_checkpoint_returns_ok_payload(self, client):
        session_id = "restore-http-session"
        register_session(session_id)

        async def fake_restore_checkpoint(
            requested_session_id: str, checkpoint_id: str
        ):
            assert requested_session_id == session_id
            assert checkpoint_id == "cp-http-restore"

        with patch.object(
            session_manager,
            "restore_checkpoint",
            side_effect=fake_restore_checkpoint,
        ):
            response = await client.post(
                f"/sessions/{session_id}/checkpoints/cp-http-restore/restore"
            )

        assert response.status_code == 200
        assert response.json() == {
            "status": "restored",
            "session_id": session_id,
            "checkpoint_id": "cp-http-restore",
        }

    async def test_restore_checkpoint_returns_409_for_active_turn(self, client):
        session_id = "restore-busy-session"
        register_session(session_id)

        async def fake_restore_checkpoint(
            requested_session_id: str, checkpoint_id: str
        ):
            del requested_session_id, checkpoint_id
            raise RuntimeError("turn already in progress")

        with patch.object(
            session_manager,
            "restore_checkpoint",
            side_effect=fake_restore_checkpoint,
        ):
            response = await client.post(
                f"/sessions/{session_id}/checkpoints/cp-busy/restore"
            )

        assert response.status_code == 409
        assert response.json()["detail"] == "turn already in progress"


class FakeRuntimeReplayStore:
    def __init__(
        self,
        *,
        run: AgentRunRecord,
        snapshot: RunMessageSnapshotRecord | None = None,
        events: list[RuntimeEventRecord] | None = None,
    ) -> None:
        self.run = run
        self.snapshot = snapshot
        self.events = events or []

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        if run_id == self.run.run_id:
            return self.run
        return None

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        if session_id == self.run.session_id:
            return [self.run]
        return []

    async def load_message_snapshot(
        self,
        snapshot_id: str,
    ) -> RunMessageSnapshotRecord | None:
        if self.snapshot is not None and snapshot_id == self.snapshot.snapshot_id:
            return self.snapshot
        return None

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        for event in self.events:
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
        return [
            event
            for event in self.events
            if event.run_id == run_id
            and event.sequence is not None
            and event.sequence > after_sequence
        ][:limit]


class FakeExternalWorkerRuntimeStore:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRunRecord] = {}
        self.events: list[RuntimeEventRecord] = []
        self.snapshots: dict[str, RunMessageSnapshotRecord] = {}
        self.interactions: dict[str, AgentInteractionRecord] = {}

    async def create_agent_run(self, record: AgentRunRecord) -> AgentRunRecord:
        self.runs[record.run_id] = record
        return record

    async def load_agent_run(self, run_id: str) -> AgentRunRecord | None:
        return self.runs.get(run_id)

    async def list_agent_runs(self, session_id: str) -> list[AgentRunRecord]:
        return [run for run in self.runs.values() if run.session_id == session_id]

    async def claim_attached_executor_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, object],
    ) -> AgentRunRecord | None:
        for run in self.runs.values():
            if session_id is not None and run.session_id != session_id:
                continue
            if run.status not in {"requested", "expired"}:
                continue
            if run.metadata.get("executor_kind") != executor_kind:
                continue
            claimed = replace(
                run,
                status="claimed",
                metadata={**run.metadata, **claim_metadata},
            )
            self.runs[run.run_id] = claimed
            return claimed
        return None

    async def claim_external_worker_run(
        self,
        *,
        session_id: str | None,
        executor_kind: str,
        claim_metadata: dict[str, object],
    ) -> AgentRunRecord | None:
        return await self.claim_attached_executor_run(
            session_id=session_id,
            executor_kind=executor_kind,
            claim_metadata=claim_metadata,
        )

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
        run = self.runs[run_id]
        updated = replace(
            run,
            status=status,
            ended_at=ended_at,
            metadata=metadata,
            result=result,
            error=error,
        )
        self.runs[run_id] = updated
        return updated

    async def append_runtime_event(
        self,
        record: RuntimeEventRecord,
    ) -> RuntimeEventRecord:
        stored = replace(record, sequence=len(self.events) + 1)
        self.events.append(stored)
        return stored

    async def load_runtime_event(
        self,
        event_id: str,
    ) -> RuntimeEventRecord | None:
        for event in self.events:
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
        return [
            event
            for event in self.events
            if event.run_id == run_id
            and event.sequence is not None
            and event.sequence > after_sequence
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
        stored = self.interactions.setdefault(record.interaction_id, record)
        return stored

    async def load_agent_interaction(
        self,
        interaction_id: str,
    ) -> AgentInteractionRecord | None:
        return self.interactions.get(interaction_id)

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
        interaction = self.interactions[interaction_id]
        if interaction.resolved_at is not None:
            return interaction
        resolved = replace(
            interaction,
            status=status,
            response_payload=response_payload,
            resolved_at=resolved_at,
        )
        self.interactions[interaction_id] = resolved
        return resolved


async def _wait_for_fake_interaction(
    store: FakeExternalWorkerRuntimeStore,
    approval_task: asyncio.Task[ApprovalResponse],
) -> None:
    async def wait_until_registered() -> None:
        while not store.interactions:
            if approval_task.done():
                _ = approval_task.result()
                raise AssertionError(
                    "approval task finished before interaction was registered"
                )
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_registered(), timeout=1)


class TestRuntimeReplayEndpoints:
    async def test_runtime_replay_endpoints_return_run_snapshot_and_events(
        self,
        client: AsyncClient,
    ) -> None:
        session_id = "runtime-replay-session"
        run_id = "run-replay-1"
        register_session(session_id)
        started_at = datetime(2026, 1, 2, 3, 4, 5)
        snapshot_at = datetime(2026, 1, 2, 3, 5, 0)
        event_at = datetime(2026, 1, 2, 3, 5, 1)
        run = AgentRunRecord(
            run_id=run_id,
            session_id=session_id,
            tape_id="tape-replay",
            parent_run_id=None,
            agent_id=None,
            status="completed",
            started_at=started_at,
            ended_at=datetime(2026, 1, 2, 3, 6, 0),
            metadata={"provider_name": "test-provider"},
            result={"stop_reason": "no_tool_calls"},
            error=None,
        )
        snapshot = RunMessageSnapshotRecord(
            snapshot_id=f"{run_id}:latest",
            run_id=run_id,
            messages=[{"role": "user", "content": "hello"}],
            metadata={"snapshot_kind": "latest_context"},
            created_at=snapshot_at,
        )
        first_event = RuntimeEventRecord(
            sequence=1,
            event_id="event-1",
            run_id=run_id,
            event_kind="wire.StreamDelta",
            payload={"message_type": "StreamDelta"},
            created_at=event_at,
        )
        second_event = RuntimeEventRecord(
            sequence=2,
            event_id="event-2",
            run_id=run_id,
            event_kind="wire.TurnEnd",
            payload={"message_type": "TurnEnd"},
            created_at=datetime(2026, 1, 2, 3, 5, 2),
        )
        session_manager.configure_runtime_store(
            FakeRuntimeReplayStore(
                run=run,
                snapshot=snapshot,
                events=[first_event, second_event],
            )
        )

        run_response = await client.get(f"/runs/{run_id}")
        snapshot_response = await client.get(f"/runs/{run_id}/message-snapshot")
        events_response = await client.get(
            f"/runs/{run_id}/events",
            params={"last_event_id": "event-1"},
        )

        assert run_response.status_code == 200
        assert run_response.json() == {
            "run_id": run_id,
            "session_id": session_id,
            "tape_id": "tape-replay",
            "parent_run_id": None,
            "agent_id": None,
            "status": "completed",
            "started_at": "2026-01-02T03:04:05",
            "ended_at": "2026-01-02T03:06:00",
            "metadata": {"provider_name": "test-provider"},
            "result": {"stop_reason": "no_tool_calls"},
            "error": None,
        }
        assert snapshot_response.status_code == 200
        assert snapshot_response.json() == {
            "snapshot_id": f"{run_id}:latest",
            "run_id": run_id,
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"snapshot_kind": "latest_context"},
            "created_at": "2026-01-02T03:05:00",
        }
        assert events_response.status_code == 200
        assert events_response.json() == {
            "run_id": run_id,
            "events": [
                {
                    "sequence": 2,
                    "event_id": "event-2",
                    "run_id": run_id,
                    "event_kind": "wire.TurnEnd",
                    "payload": {"message_type": "TurnEnd"},
                    "created_at": "2026-01-02T03:05:02",
                }
            ],
        }

    async def test_display_events_endpoint_projects_runtime_events(
        self,
        client: AsyncClient,
    ) -> None:
        session_id = "display-events-session"
        run_id = "display-run"
        register_session(session_id)
        run = AgentRunRecord(
            run_id=run_id,
            session_id=session_id,
            tape_id="tape-display",
            parent_run_id=None,
            agent_id=None,
            status="completed",
            started_at=datetime(2026, 1, 2, 3, 4, 5),
            ended_at=datetime(2026, 1, 2, 3, 6, 0),
            metadata={"provider_name": "test-provider"},
            result={"stop_reason": "no_tool_calls"},
            error=None,
        )
        first_event = RuntimeEventRecord(
            sequence=1,
            event_id="event-1",
            run_id=run_id,
            event_kind="wire.StreamDelta",
            payload={
                "message_type": "StreamDelta",
                "message": {
                    "agent_id": "agent-1",
                    "content": "hello",
                    "role": "assistant",
                },
            },
            created_at=datetime(2026, 1, 2, 3, 5, 1),
        )
        second_event = RuntimeEventRecord(
            sequence=2,
            event_id="event-2",
            run_id=run_id,
            event_kind="wire.ToolResultDelta",
            payload={
                "message_type": "ToolResultDelta",
                "message": {
                    "agent_id": "agent-1",
                    "call_id": "call-1",
                    "tool_name": "bash_run",
                    "result": {"stdout": "SECRET=abc123"},
                    "display_result": "command succeeded",
                    "is_error": False,
                },
            },
            created_at=datetime(2026, 1, 2, 3, 5, 2),
        )
        third_event = RuntimeEventRecord(
            sequence=3,
            event_id="event-3",
            run_id=run_id,
            event_kind="model_request_started",
            payload={"request_id": "model-1"},
            created_at=datetime(2026, 1, 2, 3, 5, 3),
        )
        session_manager.configure_runtime_store(
            FakeRuntimeReplayStore(
                run=run,
                events=[first_event, second_event, third_event],
            )
        )

        display_response = await client.get(
            f"/runs/{run_id}/display-events",
            params={"last_event_id": "event-1"},
        )
        runtime_response = await client.get(f"/runs/{run_id}/events")

        assert display_response.status_code == 200
        assert display_response.json() == {
            "run_id": run_id,
            "events": [
                {
                    "source_event_id": "event-2",
                    "run_id": run_id,
                    "sequence": 2,
                    "display_kind": "tool_result",
                    "payload": {
                        "agent_id": "agent-1",
                        "call_id": "call-1",
                        "tool_name": "bash_run",
                        "display_result": "command succeeded",
                        "is_error": False,
                    },
                    "created_at": "2026-01-02T03:05:02",
                }
            ],
        }
        assert runtime_response.status_code == 200
        assert runtime_response.json()["events"][1]["payload"]["message"]["result"] == {
            "stdout": "SECRET=abc123"
        }

    async def test_get_runtime_run_returns_404_when_store_is_not_configured(
        self,
        client: AsyncClient,
    ) -> None:
        response = await client.get("/runs/missing-run")

        assert response.status_code == 404
        assert response.json()["detail"] == "Runtime run not found"


class TestApprovalStoreIntegration:
    """Tests for ApprovalStore integration in SessionManager and HTTP server."""

    async def test_session_has_approval_store(self, client):
        """Test that newly created sessions have an ApprovalStore."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)
        assert hasattr(session, "approval_store")
        assert isinstance(session.approval_store, ApprovalStore)

    async def test_approval_store_request_response(self, client):
        """Test that ApprovalStore can handle request/response cycle."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        session = session_manager.get_session(session_id)

        # Add a request
        tool_call = ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "echo test"},
            call_id="call1",
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="req-test",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        # Verify request was stored
        retrieved = session.approval_store.get_request("req-test")
        assert retrieved is not None
        assert retrieved.request_id == "req-test"

        # Respond to the request
        approval_resp = ApprovalResponse(
            session_id=session_id,
            request_id="req-test",
            approved=True,
            feedback="Approved",
        )
        success = session.approval_store.respond(approval_resp)
        assert success is True

    async def test_submit_approval_returns_bool(self, client):
        """Test that submit_approval returns boolean success status."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Try to approve non-existent request
        result = await session_manager.submit_approval(
            session_id=session_id,
            request_id="nonexistent",
            approved=True,
            feedback=None,
        )
        # Should return False since request wasn't added to store
        assert result is False

        # Now add the request and try again
        session = session_manager.get_session(session_id)
        tool_call = ToolCallDelta(
            session_id=session_id, tool_name="bash", arguments={}, call_id="call1"
        )
        approval_req = ApprovalRequest(
            session_id=session_id,
            request_id="real-req",
            tool_call=tool_call,
            timeout_seconds=120,
        )
        session.approval_store.add_request(approval_req)

        result = await session_manager.submit_approval(
            session_id=session_id, request_id="real-req", approved=True, feedback="Good"
        )
        assert result is True

    async def test_close_session_cleans_up_approval_store(self, client):
        """Test that closing session removes approval store from manager."""
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        # Verify store exists
        assert session_id in session_manager._approval_stores

        # Close session
        await session_manager.close_session(session_id)

        # Store should be cleaned up
        assert session_id not in session_manager._approval_stores


@pytest.mark.asyncio
async def test_prompt_stream_disconnect_does_not_leave_turn_in_progress(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    adapter_started = asyncio.Event()
    adapter_cancelled = asyncio.Event()

    class FakeEventSourceResponse:
        def __init__(self, body_iterator, **kwargs: object) -> None:
            del kwargs
            self.body_iterator = body_iterator

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer) -> None:
            del pipeline, consumer
            self.ctx = ctx

        async def run_turn(self, prompt: str) -> None:
            del prompt
            adapter_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                adapter_cancelled.set()
                raise

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(
            get=lambda _: types.SimpleNamespace(_instance=None)
        )
    )

    def fake_create_agent(**kwargs: object) -> tuple[object, object]:
        tape = kwargs.get("tape") if isinstance(kwargs, dict) else None
        return fake_pipeline, types.SimpleNamespace(
            config={},
            tape=tape or Tape(tape_id="http-disconnect-tape"),
            plugin_states={},
        )

    monkeypatch.setattr(
        "coding_agent.server.http_server.EventSourceResponse",
        FakeEventSourceResponse,
    )
    monkeypatch.setattr("coding_agent.__main__.create_agent", fake_create_agent)
    monkeypatch.setattr(
        "coding_agent.server.session_manager.PipelineAdapter", FakeAdapter
    )

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/sessions/{session_id}/prompt",
            "headers": [],
        }
    )
    response = await http_server.send_prompt(
        request,
        session_id,
        body=http_server.PromptRequest(prompt="Hello"),
        prompt=None,
        event_format="wire",
        api_key=None,
    )
    event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)
    consumer = asyncio.create_task(anext(event_generator))

    await asyncio.wait_for(adapter_started.wait(), timeout=1)
    session = session_manager.get_session(session_id)
    assert session.turn_in_progress is True
    assert session.task is not None

    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(consumer, timeout=1)
    await asyncio.wait_for(adapter_cancelled.wait(), timeout=1)

    session = session_manager.get_session(session_id)
    assert session.turn_in_progress is False
    assert session.task is None
    assert session.turn_status == "cancelled"


@pytest.mark.asyncio
async def test_prompt_stream_aclose_cancels_running_turn(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_resp = await client.post("/sessions", json={})
    session_id = create_resp.json()["session_id"]
    run_started = asyncio.Event()
    task_cancelled = asyncio.Event()

    class FakeEventSourceResponse:
        def __init__(self, body_iterator, **kwargs: object) -> None:
            del kwargs
            self.body_iterator = body_iterator

    async def fake_run_agent(_session_id: str, _prompt: str) -> None:
        session = session_manager.get_session(_session_id)
        session.turn_status = "running"
        run_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            session.turn_status = "cancelled"
            task_cancelled.set()
            raise

    async def fake_stream_wire_messages(
        wire: object,
        task: asyncio.Task[object] | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        del wire, task
        await run_started.wait()
        yield {
            "event": "StreamDelta",
            "data": json.dumps(
                {
                    "session_id": session_id,
                    "agent_id": "",
                    "content": "hello",
                    "role": "assistant",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            ),
        }
        await asyncio.Event().wait()

    monkeypatch.setattr(
        "coding_agent.server.http_server.EventSourceResponse",
        FakeEventSourceResponse,
    )
    monkeypatch.setattr(session_manager, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        "coding_agent.server.http_server.stream_wire_messages",
        fake_stream_wire_messages,
    )

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/sessions/{session_id}/prompt",
            "headers": [],
        }
    )
    response = await http_server.send_prompt(
        request,
        session_id,
        body=http_server.PromptRequest(prompt="Hello"),
        prompt=None,
        event_format="wire",
        api_key=None,
    )
    event_generator = cast(AsyncIterator[dict[str, str]], response.body_iterator)

    first_event = await asyncio.wait_for(anext(event_generator), timeout=1)
    assert first_event["event"] == "StreamDelta"
    session = session_manager.get_session(session_id)
    assert session.turn_in_progress is True
    assert session.task is not None

    await asyncio.wait_for(event_generator.aclose(), timeout=1)
    await asyncio.wait_for(task_cancelled.wait(), timeout=1)

    session = session_manager.get_session(session_id)
    assert session.turn_in_progress is False
    assert session.task is None
    assert session.turn_status == "cancelled"
