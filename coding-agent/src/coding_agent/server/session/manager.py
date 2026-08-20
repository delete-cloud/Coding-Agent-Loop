"""SessionManager facade composing session package responsibilities."""

from __future__ import annotations

import logging
import asyncio
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import (
    Any,
    cast,
)
from agentkit.checkpoint import CheckpointService
from agentkit.storage.protocols import (
    CheckpointStore,
    TapeStore,
)
from coding_agent.observability.agent import (
    AgentObservationStore,
    JsonlAgentObservationStore,
)
from coding_agent.approval.store import ApprovalStore
from coding_agent.stores.local import (
    local_sqlite_storage_config,
    local_sqlite_path_from_storage_config,
    normalize_storage_path,
    with_local_sqlite_bundle_paths,
)
from coding_agent.stores.durable_local import (
    FencedSQLiteCheckpointStore,
    FencedSQLiteRuntimeStore,
    FencedSQLiteTapeStore,
)
from coding_agent.stores.durable_pg import PGDurableStore
from coding_agent.stores import RuntimeStore
from coding_agent.executors import LocalDaemonExecutor
from coding_agent.runs import (
    DefaultRunCoordinator,
    RunCoordinator,
    RuntimeAgentFactoryService,
    RuntimeAttachedExecutorClaimService,
    RuntimeAttachedExecutorFinalizeService,
    RuntimeAttachedExecutorRequestService,
    RuntimeCancelObservationFinalizer,
    RuntimeCancelOrchestrationService,
    RuntimeCheckpointCaptureService,
    RuntimeCheckpointQueryService,
    RuntimeControlServices,
    RuntimeCloser,
    RuntimeContextBindingService,
    RuntimeEnsureOrchestrationService,
    RuntimeEnsureService,
    RuntimeMaintenanceAdmissionService,
    RuntimeRunMetadataService,
    RuntimeObservationService,
    RuntimePreparationRequestService,
    RuntimeReplacementService,
    RuntimeResumeOrchestrationService,
    RuntimeResumeSessionOrchestrationService,
    RuntimeTurnAdmissionService,
    RuntimeWorkspaceExportService,
    CloudWorkspaceRef,
)
from coding_agent.runs.environment import RuntimeEnvironmentResolverService
from coding_agent.runs.runtime_preparation import LocalDaemonRuntimePreparationService
from coding_agent.runs.runtime_checkpoint_restore import (
    RuntimeCheckpointRestoreOrchestrationService,
)
from coding_agent.runs.runtime_checkpoint_restore import RuntimeCheckpointRestoreService
from coding_agent.runs.turn_service_factory import RuntimeTurnServiceFactory
from coding_agent.server.stores.session_store import SessionStore
from coding_agent.server.stores.session_owner_store import SessionOwnerStoreProtocol
from coding_agent.environment.cloud import CloudWorkspaceClient
from coding_agent.server.session import _bindings
from coding_agent.server.session.durable import _custom_store_names
from coding_agent.server.session.models import ExternalWorkerClaim
from coding_agent.server.session.models import Session
from coding_agent.server.session.models import WorkspaceMetadataStoreProtocol
from coding_agent.server.session.models import _session_is_attached
from coding_agent.server.session.runtime import _ACTIVE_RESUME_BLOCKING_RUN_STATUSES

from coding_agent.server.session.durable import DurableOps
from coding_agent.server.session.semantic import SemanticOps
from coding_agent.server.session.queries import QueryOps
from coding_agent.server.session.remote_loop import RemoteLoopOps
from coding_agent.server.session.restore import RestoreOps
from coding_agent.server.session.workspace import WorkspaceOps
from coding_agent.server.session.persist import PersistOps
from coding_agent.server.session.owner import OwnerOps
from coding_agent.server.session.approval import ApprovalOps
from coding_agent.server.session.turn import TurnOps
from coding_agent.server.session.registry import RegistryOps
from coding_agent.server.session.lifecycle import LifecycleOps
from coding_agent.server.session.runtime import RuntimeOps

logger = logging.getLogger("coding_agent.server.session_manager")


class SessionManager(
    DurableOps,
    SemanticOps,
    QueryOps,
    RemoteLoopOps,
    RestoreOps,
    WorkspaceOps,
    PersistOps,
    OwnerOps,
    ApprovalOps,
    TurnOps,
    RegistryOps,
    LifecycleOps,
    RuntimeOps,
):
    """Manages agent sessions with lifecycle and resource management."""

    def __init__(
        self,
        store: SessionStore | None = None,
        *,
        storage_config: dict[str, Any] | None = None,
        pg_pool: object | None = None,
        tape_store: TapeStore | None = None,
        checkpoint_store: CheckpointStore | None = None,
        checkpoint_service: CheckpointService | None = None,
        create_agent_fn: Callable[..., tuple[Any, Any]] | None = None,
        cloud_workspace_client_factory: Callable[
            [CloudWorkspaceRef], CloudWorkspaceClient
        ]
        | None = None,
        provisioned_cloud_binding_cleanup: (
            Callable[[CloudWorkspaceRef], None] | None
        ) = None,
        workspace_metadata_store: WorkspaceMetadataStoreProtocol | None = None,
        runtime_store: RuntimeStore | None = None,
        observation_store: AgentObservationStore | None = None,
        owner_store: SessionOwnerStoreProtocol | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
        owner_lease_seconds: float = 30.0,
        run_coordinator: RunCoordinator | None = None,
        local_daemon_executor: LocalDaemonExecutor | None = None,
    ):
        data_dir = Path(os.environ.get("AGENT_DATA_DIR", "./data"))
        self._storage_config = (
            with_local_sqlite_bundle_paths(dict(storage_config), data_dir)
            if storage_config
            else local_sqlite_storage_config(data_dir)
        )
        self._local_sqlite_bundle_path = normalize_storage_path(
            str(local_sqlite_path_from_storage_config(self._storage_config, data_dir))
        )
        self._pg_pool = pg_pool
        self._owns_pg_pool = False
        self._custom_store_names = _custom_store_names(
            store=store,
            tape_store=tape_store,
            checkpoint_store=checkpoint_store,
            checkpoint_service=checkpoint_service,
            runtime_store=runtime_store,
        )
        self._local_durable_store = self._create_local_durable_store(
            owner_store=owner_store,
        )
        self._pg_durable_store: PGDurableStore | None = None
        self._store = store or self._create_http_session_store()
        self._session_cache: dict[str, Session] = {}
        self._approval_stores: dict[str, ApprovalStore] = {}
        self._lock = asyncio.Lock()
        self._store_io_guard = threading.Lock()
        self._session_turn_locks: dict[str, asyncio.Lock] = {}
        self._session_workspace_export_counts: dict[str, int] = {}
        self._tape_store = tape_store or self._create_tape_store(data_dir)
        if self._local_durable_store is not None and tape_store is None:
            self._tape_store = FencedSQLiteTapeStore(
                durable_store=self._local_durable_store,
                path=self._local_sqlite_bundle_path,
                authority_for_session=self._owner_authority_for_session,
            )
        self._agent_observation_store = observation_store or JsonlAgentObservationStore(
            data_dir / "observability"
        )
        self._runtime_observation_service = RuntimeObservationService(
            self._agent_observation_store
        )
        self._runtime_metadata_service = RuntimeRunMetadataService()
        resolved_checkpoint_store = checkpoint_store or self._create_checkpoint_store(
            data_dir
        )
        if (
            self._local_durable_store is not None
            and checkpoint_store is None
            and checkpoint_service is None
        ):
            resolved_checkpoint_store = FencedSQLiteCheckpointStore(
                durable_store=self._local_durable_store,
                path=self._local_sqlite_bundle_path,
                authority_for_session=self._owner_authority_for_session,
            )
        self._checkpoint_service = checkpoint_service or CheckpointService(
            resolved_checkpoint_store
        )
        self._create_agent = create_agent_fn
        self._local_daemon_executor = (
            LocalDaemonExecutor()
            if local_daemon_executor is None
            else local_daemon_executor
        )
        self._run_coordinator = (
            DefaultRunCoordinator(local_daemon_executor=self._local_daemon_executor)
            if run_coordinator is None
            else run_coordinator
        )
        self._provisioned_cloud_binding_cleanup = provisioned_cloud_binding_cleanup
        self._workspace_metadata_store = workspace_metadata_store
        self._runtime_store = (
            runtime_store if runtime_store is not None else self._create_runtime_store()
        )
        if self._local_durable_store is not None and runtime_store is None:
            self._runtime_store = FencedSQLiteRuntimeStore(
                durable_store=self._local_durable_store,
                path=self._local_sqlite_bundle_path,
                authority_for_session=self._owner_authority_for_session,
                authorities=lambda: dict(self._owner_authorities),
            )

        async def runtime_session_is_recoverable(session_id: str) -> bool:
            if self._owner_store is None:
                return True
            return await self._holds_active_owner_lease(session_id)

        self._runtime_control_services = RuntimeControlServices(
            store=lambda: self._runtime_store,
            metadata_for_session=self._runtime_metadata_service.metadata_for_session,
            list_session_ids=self.list_sessions_async,
            session_is_recoverable=runtime_session_is_recoverable,
            owner_id=lambda: self._owner_id,
            active_resume_blocking_statuses=frozenset(
                _ACTIVE_RESUME_BLOCKING_RUN_STATUSES
            ),
        )
        self._runtime_cancel_orchestration = RuntimeCancelOrchestrationService(
            cancel_service=self._runtime_control_services.cancel,
            persist_session=self._persist_session_async,
            session_is_attached=lambda session: _session_is_attached(
                cast(Session, session)
            ),
            schedule_cancel_observation=self._schedule_cancel_observation,
            turn_id_factory=lambda: uuid.uuid4().hex,
        )
        self._runtime_cancel_observation_finalizer = RuntimeCancelObservationFinalizer(
            cancel_service=self._runtime_control_services.cancel,
            load_session=self.get_session_async,
            persist_session=self._persist_session_async,
            session_has_task=lambda session, task: session.task is task,
            lock=lambda: self._lock,
        )
        self._runtime_turn_admission = RuntimeTurnAdmissionService(
            turn_lock_for=self._turn_lock_for,
            workspace_export_in_progress=self._workspace_export_in_progress,
            assert_owner=self._assert_owner,
            load_session=self.get_session_async,
        )
        self._runtime_workspace_export_service = RuntimeWorkspaceExportService(
            turn_lock_for=self._turn_lock_for,
            assert_owner=self._assert_owner,
            load_session=self.get_session_async,
            begin_export=self._begin_workspace_export,
            end_export=self._end_workspace_export,
        )
        self._runtime_maintenance_admission = RuntimeMaintenanceAdmissionService(
            turn_lock_for=self._turn_lock_for,
            assert_owner=self._assert_owner,
            load_session=self.get_session_async,
        )
        self._runtime_closer = RuntimeCloser()
        self._runtime_agent_factory_service = RuntimeAgentFactoryService(
            create_agent=self._create_agent,
        )
        self._runtime_preparation_request_service = RuntimePreparationRequestService()
        self._runtime_replacement_service = RuntimeReplacementService(
            close_runtime_adapter=self._runtime_closer.close_adapter,
        )
        self._runtime_ensure_service = RuntimeEnsureService()
        self._runtime_ensure_orchestration = RuntimeEnsureOrchestrationService(
            ensure_service=self._runtime_ensure_service,
            assert_owner=self._assert_owner,
            load_session=self.get_session_async,
            build_runtime=lambda session: self._build_session_runtime(
                cast(Session, session)
            ),
            persist_session=lambda session: self._persist_session_async(
                cast(Session, session)
            ),
        )
        self._runtime_environment_resolver_service = RuntimeEnvironmentResolverService(
            cloud_client_factory=cloud_workspace_client_factory
        )
        self._runtime_context_binding_service = RuntimeContextBindingService(
            publish_subagent_message=self.publish_subagent_message,
        )
        self._local_daemon_runtime_preparation = LocalDaemonRuntimePreparationService(
            environment_resolver=self._runtime_environment_resolver_service,
            local_daemon_executor=self._local_daemon_executor,
            close_runtime=self._runtime_closer.close,
            close_runtime_adapter=self._runtime_closer.close_adapter,
            create_agent_for_session=(
                self._runtime_agent_factory_service.create_agent_for_session
            ),
            restore_tape=self._restore_tape,
            persist_session=self._persist_session_async,
            make_consumer=self._make_session_consumer,
            bind_subagent_message_publisher=(
                self._runtime_context_binding_service.bind_subagent_message_publisher
            ),
            runtime_preparation_request=(
                self._runtime_preparation_request_service.request_for_session
            ),
            semantic_topic_store_factory=self.selected_topic_store,
            adapter_factory=lambda pipeline, ctx, consumer: (
                _bindings.pipeline_adapter()(
                    pipeline=pipeline,
                    ctx=ctx,
                    consumer=consumer,
                )
            ),
        )
        self._runtime_checkpoint_restore_service = RuntimeCheckpointRestoreService(
            checkpoint_service=lambda: self._checkpoint_service,
            tape_store=lambda: self._tape_store,
            local_daemon_executor=self._local_daemon_executor,
            resolve_environment_for_run_target=(
                self._runtime_environment_resolver_service.resolve_environment_for_run_target
            ),
            workspace_root_for_environment=(
                self._runtime_environment_resolver_service.workspace_root_for_environment
            ),
            create_agent_for_session=(
                self._runtime_agent_factory_service.create_agent_for_session
            ),
            bind_subagent_message_publisher=(
                self._runtime_context_binding_service.bind_subagent_message_publisher
            ),
            restore_consumer_factory=self._make_restore_consumer,
            adapter_factory=lambda pipeline, ctx, consumer: (
                _bindings.pipeline_adapter()(
                    pipeline=pipeline,
                    ctx=ctx,
                    consumer=consumer,
                )
            ),
            runtime_preparation_request=(
                self._runtime_preparation_request_service.request_for_session
            ),
            close_runtime=self._runtime_closer.close,
            persist_session=self._persist_session_async,
            semantic_topic_store_factory=self.selected_topic_store,
            restore_durable_state=self._restore_checkpoint_durable_state,
        )
        self._runtime_checkpoint_restore_orchestration = (
            RuntimeCheckpointRestoreOrchestrationService(
                admission=self._runtime_maintenance_admission,
                restore=self._restore_checkpoint,
            )
        )
        self._runtime_checkpoint_query_service = RuntimeCheckpointQueryService(
            checkpoint_service=lambda: self._checkpoint_service,
        )
        self._runtime_checkpoint_capture_service = RuntimeCheckpointCaptureService(
            checkpoint_service=lambda: self._checkpoint_service,
            ensure_runtime=lambda session_id: self.ensure_session_runtime(session_id),
            persist_session=lambda session: self._persist_session_async(session),
        )
        self._runtime_resume_orchestration = RuntimeResumeOrchestrationService(
            resume_service=self._runtime_control_services.resume(),
            latest_runtime_run=lambda session_id: (
                self._runtime_control_services.queries().latest_runtime_run(session_id)
            ),
            latest_runtime_event_id=lambda run: (
                self._runtime_control_services.queries().latest_runtime_event_id(run)
            ),
            load_runtime_run=lambda run_id: (
                self._require_runtime_store().load_agent_run(run_id)
            ),
            persist_session=lambda session: self._persist_session_async(session),
            list_checkpoints=self.list_checkpoints,
            load_tape_entries=self._tape_store.load,
            save_tape_entries=self._tape_store.save,
            load_message_snapshot=lambda snapshot_id: (
                self._require_runtime_store().load_message_snapshot(snapshot_id)
            ),
            run_local=self._run_resumed_local_session,
            request_attached=self._request_resumed_attached_executor_run,
            session_is_attached=lambda session: _session_is_attached(
                cast(Session, session)
            ),
            append_live_boundary_anchor=self._append_live_resume_boundary_anchor,
            active_resume_blocking_statuses=frozenset(
                _ACTIVE_RESUME_BLOCKING_RUN_STATUSES
            ),
        )
        self._runtime_resume_session_orchestration = (
            RuntimeResumeSessionOrchestrationService(
                require_runtime_store=self._require_runtime_store,
                assert_owner=self._assert_owner,
                load_session=self.get_session_async,
                resume_orchestration=self._runtime_resume_orchestration,
            )
        )
        self._runtime_attached_executor_request_service = (
            RuntimeAttachedExecutorRequestService(
                lock=self._lock,
                assert_owner=self._assert_owner,
                load_session=self.get_session_async,
                attached_executor=self._runtime_control_services.attached_executor,
                persist_session=self._persist_session_async,
                session_is_attached=lambda session: _session_is_attached(
                    cast(Session, session)
                ),
            )
        )
        self._runtime_attached_executor_claim_service = (
            RuntimeAttachedExecutorClaimService(
                attached_executor=self._runtime_control_services.attached_executor,
                load_session=self.get_session_async,
                claim_factory=lambda claim, session: ExternalWorkerClaim(
                    run=claim.run,
                    claim_token=claim.claim_token,
                    prompt=claim.prompt,
                    session=cast(Session, session),
                ),
            )
        )
        self._runtime_attached_executor_finalize_service = (
            RuntimeAttachedExecutorFinalizeService(
                lock=self._lock,
                load_session=self.get_session_async,
                attached_executor=self._runtime_control_services.attached_executor,
                save_tape_entries=lambda tape_id, entries: self._tape_store.save(
                    tape_id,
                    entries,
                ),
                persist_session=self._persist_session_async,
            )
        )
        self._runtime_turn_service_factory = RuntimeTurnServiceFactory(
            runtime_control_services=self._runtime_control_services,
            persist_session=self._persist_session_async,
            persist_turn_started=self._persist_turn_started,
            persist_turn_settled=self._persist_turn_settled,
            make_consumer=self._make_session_consumer,
            prepare_runtime=self._local_daemon_runtime_preparation.prepare_runtime,
            close_runtime=self._runtime_closer.close,
            emit_message=self._send_session_wire_message,
            bind_root_run_identity=(
                self._runtime_context_binding_service.bind_root_run_identity
            ),
            bind_subagent_message_publisher=(
                self._runtime_context_binding_service.bind_subagent_message_publisher
            ),
            start_observation=self._runtime_observation_service.start,
            complete_observation=self._runtime_observation_service.complete,
            log_turn_exception=lambda message: logger.exception(message),
        )
        self._runtime_turn_service = self._build_runtime_turn_service()
        self.configure_owner_leases(
            owner_store=owner_store,
            owner_id=owner_id,
            fencing_token=fencing_token,
            owner_lease_seconds=owner_lease_seconds,
        )
