"""SessionManager for managing agent sessions."""

from __future__ import annotations

import asyncio
import importlib

from coding_agent.adapter import PipelineAdapter
from coding_agent.runs import (
    RuntimeMaintenanceAdmissionService,
    RuntimeReplacementService,
)
from coding_agent.server.session.durable import _load_pg_storage_types
from coding_agent.server.session.manager import SessionManager
from coding_agent.server.session.models import (
    AttachedExecutorClaim,
    CancelTurnResult,
    ExternalWorkerClaim,
    MockProvider,
    Session,
    WorkspaceMetadataStoreProtocol,
)
from coding_agent.server.session.records import SessionRecord
from coding_agent.server.session.semantic import SemanticDogfoodTopicSeedResult
from coding_agent.server.stores.session_store import create_session_store
from coding_agent.stores.runtime_store import PGRuntimeStore
from coding_agent.topics.memory import propose_memory_candidate_from_topic

__all__ = [
    "AttachedExecutorClaim",
    "CancelTurnResult",
    "ExternalWorkerClaim",
    "MockProvider",
    "PGRuntimeStore",
    "PipelineAdapter",
    "RuntimeMaintenanceAdmissionService",
    "RuntimeReplacementService",
    "SemanticDogfoodTopicSeedResult",
    "Session",
    "SessionManager",
    "SessionRecord",
    "WorkspaceMetadataStoreProtocol",
    "_load_pg_storage_types",
    "asyncio",
    "create_session_store",
    "importlib",
    "propose_memory_candidate_from_topic",
]
