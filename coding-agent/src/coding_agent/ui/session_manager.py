"""SessionManager for managing agent sessions.

NOTE: This module previously used AgentLoop which has been removed.
The create_session/run_agent methods now raise NotImplementedError.
The HTTP serve command needs to be migrated to Pipeline.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from coding_agent.adapter import PipelineAdapter
from coding_agent.approval import ApprovalPolicy
from coding_agent.approval.store import ApprovalStore
from coding_agent.providers.base import ChatProvider, ToolSchema
from agentkit.providers.models import DoneEvent, TextEvent
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
    WireMessage,
)
from coding_agent.ui.session_store import (
    SessionStore,
    create_session_store,
)

logger = logging.getLogger(__name__)


class MockProvider:
    """Mock provider for testing that simulates LLM responses."""

    def __init__(self):
        self._max_context_size = 8192
        self._model_name = "mock"

    @property
    def max_context_size(self) -> int:
        """Maximum context size in tokens."""
        return self._max_context_size

    @property
    def model_name(self) -> str:
        """Name of the model being used."""
        return self._model_name

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        response_text = (
            "I'll help you with that request. Let me analyze the task... Done!"
        )

        for word in response_text.split():
            yield TextEvent(text=word + " ")
            await asyncio.sleep(0.01)

        yield DoneEvent()

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        """Return complete mock response."""
        return "Mock response"


@dataclass
class Session:
    """A managed agent session."""

    id: str
    created_at: datetime
    last_activity: datetime
    wire: LocalWire = field(init=False)
    approval_store: ApprovalStore = field(default_factory=ApprovalStore)
    repo_path: Path | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO
    provider: Any | None = None
    provider_name: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    max_steps: int = 30
    task: asyncio.Task[Any] | None = None
    turn_in_progress: bool = False
    pending_approval: dict[str, Any] | None = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: dict[str, Any] | None = None
    event_queues: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.wire = LocalWire(self.id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "turn_in_progress": self.turn_in_progress,
            "pending_approval": self.pending_approval is not None,
        }

    def to_store_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "repo_path": None if self.repo_path is None else str(self.repo_path),
            "approval_policy": self.approval_policy.value,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "max_steps": self.max_steps,
        }

    @classmethod
    def from_store_data(cls, data: dict[str, Any]) -> Session:
        repo_path_raw = data.get("repo_path")
        if repo_path_raw is not None and not isinstance(repo_path_raw, str):
            raise TypeError("session metadata has invalid repo_path")
        approval_policy_raw = data.get("approval_policy")
        if not isinstance(approval_policy_raw, str):
            raise TypeError("session metadata is missing approval_policy")
        provider_name_raw = data.get("provider_name")
        if provider_name_raw is not None and not isinstance(provider_name_raw, str):
            raise TypeError("session metadata has invalid provider_name")
        model_name_raw = data.get("model_name")
        if model_name_raw is not None and not isinstance(model_name_raw, str):
            raise TypeError("session metadata has invalid model_name")
        base_url_raw = data.get("base_url")
        if base_url_raw is not None and not isinstance(base_url_raw, str):
            raise TypeError("session metadata has invalid base_url")
        session = cls(
            id=_required_session_str(data, "id"),
            created_at=datetime.fromisoformat(
                _required_session_str(data, "created_at")
            ),
            last_activity=datetime.fromisoformat(
                _required_session_str(data, "last_activity")
            ),
            approval_store=ApprovalStore(),
            repo_path=None if repo_path_raw is None else Path(repo_path_raw),
            approval_policy=ApprovalPolicy(approval_policy_raw),
            provider_name=provider_name_raw,
            model_name=model_name_raw,
            base_url=base_url_raw,
            max_steps=_required_session_int(data, "max_steps"),
        )
        session.turn_in_progress = False
        session.pending_approval = None
        session.approval_response = None
        return session


def _required_session_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"session metadata is missing {key}")
    return value


def _required_session_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise TypeError(f"session metadata is missing {key}")
    return value


class SessionManager:
    """Manages agent sessions with lifecycle and resource management."""

    def __init__(self, store: SessionStore | None = None):
        self._store = store or create_session_store()
        self._session_cache: dict[str, Session] = {}
        self._approval_stores: dict[str, ApprovalStore] = {}
        self._lock = asyncio.Lock()

    def _persist_session(self, session: Session) -> None:
        self._session_cache[session.id] = session
        self._store.save(session.id, cast(dict[str, Any], session.to_store_data()))

    def _hydrate_session(self, session: Session) -> Session:
        approval_store = self._approval_stores.get(session.id)
        if approval_store is None:
            approval_store = session.approval_store
            self._approval_stores[session.id] = approval_store
        session.approval_store = approval_store
        self._session_cache[session.id] = session
        return session

    async def create_session(
        self,
        repo_path: Path | None = None,
        approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        provider: Any | None = None,
        provider_name: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        max_steps: int = 30,
        enable_parallel: bool = True,
        max_parallel: int = 5,
    ) -> str:
        """Create a new agent session.

        Args:
            repo_path: Path to the repository root (default: current directory)
            approval_policy: Policy for tool execution approval
            provider: Explicit LLM provider override for tests or custom sessions
            provider_name: Restart-safe provider identifier for later rehydration
            model_name: Restart-safe model identifier for later rehydration
            base_url: Restart-safe provider base URL for later rehydration
            max_steps: Maximum steps per turn
            enable_parallel: Enable parallel tool execution
            max_parallel: Maximum number of parallel tool executions

        Returns:
            The session ID
        """
        session_id = str(uuid.uuid4())
        now = datetime.now()

        approval_store = ApprovalStore()
        self._approval_stores[session_id] = approval_store

        session = Session(
            id=session_id,
            approval_store=approval_store,
            created_at=now,
            last_activity=now,
            repo_path=repo_path,
            approval_policy=approval_policy,
            provider=provider,
            provider_name=provider_name,
            model_name=model_name,
            base_url=base_url,
            max_steps=max_steps,
            task=None,
        )

        async with self._lock:
            self._persist_session(session)

        logger.info(f"Created session: {session_id}")
        return session_id

    def get_session(self, session_id: str) -> Session:
        """Get a session by ID.

        Args:
            session_id: The session ID

        Returns:
            The Session object

        Raises:
            KeyError: If session not found
        """
        session = self._session_cache.get(session_id)
        if session is not None:
            return session
        loaded = self._store.load(session_id)
        if loaded is None:
            raise KeyError(f"Session not found: {session_id}")
        return self._hydrate_session(
            Session.from_store_data(cast(dict[str, Any], loaded))
        )

    def has_session(self, session_id: str) -> bool:
        """Check if a session exists.

        Args:
            session_id: The session ID

        Returns:
            True if session exists, False otherwise
        """
        if session_id in self._session_cache:
            return True
        return self._store.load(session_id) is not None

    def register_session(self, session: Session) -> None:
        self._approval_stores[session.id] = session.approval_store
        self._persist_session(session)

    def remove_session(self, session_id: str) -> None:
        if not self.has_session(session_id):
            raise KeyError(f"Session not found: {session_id}")
        self._session_cache.pop(session_id, None)
        self._store.delete(session_id)
        self._approval_stores.pop(session_id, None)

    def clear_sessions(self) -> None:
        for session_id in list(self._store.list_sessions()):
            self._store.delete(session_id)
        self._session_cache.clear()
        self._approval_stores.clear()

    def add_event_queue(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        session = self.get_session(session_id)
        session.event_queues.append(queue)
        self._persist_session(session)

    def remove_event_queue(
        self,
        session_id: str,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        session = self.get_session(session_id)
        if queue in session.event_queues:
            session.event_queues.remove(queue)
            self._persist_session(session)

    async def broadcast_event(
        self,
        session_id: str,
        event: dict[str, str],
    ) -> None:
        session = self.get_session(session_id)
        before_count = len(session.event_queues)
        session.event_queues = [
            queue for queue in session.event_queues if not queue.full()
        ]
        pruned_count = before_count - len(session.event_queues)
        if pruned_count:
            logger.info(
                "Pruned %d full event queue(s) for session %s",
                pruned_count,
                session_id,
            )
        for queue in session.event_queues:
            try:
                await queue.put(event)
            except Exception:
                logger.debug("Dropping closed event queue", exc_info=True)

    def has_approval_request(self, session_id: str) -> bool:
        return self.get_session(session_id).pending_approval is not None

    def matches_approval_request(self, session_id: str, request_id: str) -> bool:
        session = self.get_session(session_id)
        if session.pending_approval is None:
            return False
        return session.pending_approval.get("request_id") == request_id

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up resources.

        Args:
            session_id: The session ID to close

        Raises:
            KeyError: If session not found
        """
        async with self._lock:
            session = self.get_session(session_id)

            # Cancel any running task
            if session.task and not session.task.done():
                session.task.cancel()
                try:
                    await asyncio.wait_for(session.task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            self.remove_session(session_id)

        logger.info(f"Closed session: {session_id}")

    async def run_agent(
        self,
        session_id: str,
        prompt: str,
    ) -> None:
        session = self.get_session(session_id)
        session.last_activity = datetime.now()
        session.turn_in_progress = True
        self._persist_session(session)

        try:
            approval_mode_map = {
                ApprovalPolicy.YOLO: "yolo",
                ApprovalPolicy.INTERACTIVE: "interactive",
                ApprovalPolicy.AUTO: "auto",
            }

            create_agent = importlib.import_module("coding_agent.__main__").create_agent

            pipeline, ctx = create_agent(
                workspace_root=session.repo_path,
                model_override=session.model_name,
                provider_override=session.provider_name,
                base_url_override=session.base_url,
                max_steps_override=session.max_steps,
                approval_mode_override=approval_mode_map[session.approval_policy],
                session_id_override=session_id,
                api_key=None,
            )
            ctx.config["wire_consumer"] = None
            ctx.config["agent_id"] = ""

            llm_plugin = pipeline._registry.get("llm_provider")
            if session.provider is not None:
                llm_plugin._instance = session.provider

            class _WireConsumer:
                def __init__(self, wire: LocalWire) -> None:
                    self._wire = wire

                async def emit(self, msg: WireMessage) -> None:
                    await self._wire.send(msg)

                async def request_approval(
                    self, req: ApprovalRequest
                ) -> ApprovalResponse:
                    session.pending_approval = {
                        "request_id": req.request_id,
                        "tool_name": req.tool_call.tool_name if req.tool_call else "",
                        "arguments": req.tool_call.arguments if req.tool_call else {},
                    }
                    session.approval_event.clear()
                    session.approval_response = None
                    outer._persist_session(session)
                    session.approval_store.add_request(req)
                    await self._wire.send(req)
                    try:
                        response = await session.approval_store.wait_for_response(
                            req.request_id,
                            req.timeout_seconds,
                        )
                        if response is None:
                            return ApprovalResponse(
                                session_id=req.session_id,
                                request_id=req.request_id,
                                approved=False,
                                feedback="Approval timeout or error",
                            )

                        session.approval_response = {
                            "decision": "approve" if response.approved else "deny",
                            "feedback": response.feedback,
                        }
                        session.approval_event.set()
                        outer._persist_session(session)
                        return response
                    finally:
                        session.pending_approval = None
                        session.approval_response = None
                        outer._persist_session(session)

            outer = self
            consumer = _WireConsumer(session.wire)
            ctx.config["wire_consumer"] = consumer
            adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
            await adapter.run_turn(prompt)
        except Exception as exc:
            logger.exception("HTTP session turn failed")
            await session.wire.send(
                StreamDelta(
                    session_id=session_id,
                    agent_id="",
                    content=f"Error: {exc}",
                )
            )
            await session.wire.send(
                TurnEnd(
                    session_id=session_id,
                    agent_id="",
                    turn_id=uuid.uuid4().hex,
                    completion_status=CompletionStatus.ERROR,
                )
            )
        finally:
            current_task = asyncio.current_task()
            if session.task is None or session.task is not current_task:
                session.turn_in_progress = False
            session.last_activity = datetime.now()
            self._persist_session(session)

    async def submit_approval(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
    ) -> bool:
        """Submit an approval response for a pending request.

        Uses the session's ApprovalStore to record the response.

        Args:
            session_id: The session ID
            request_id: The approval request ID
            approved: Whether the request is approved
            feedback: Optional feedback message

        Returns:
            True if the response was recorded successfully, False otherwise

        Raises:
            KeyError: If session not found
        """
        session = self.get_session(session_id)

        # Create approval response and submit to ApprovalStore
        response = ApprovalResponse(
            session_id=session_id,
            request_id=request_id,
            approved=approved,
            feedback=feedback,
        )
        success = session.approval_store.respond(response)
        session.last_activity = datetime.now()

        if success:
            session.pending_approval = None
            session.approval_response = {
                "decision": "approve" if approved else "deny",
                "feedback": feedback,
            }
            session.approval_event.set()
            self._persist_session(session)
            logger.info(f"Approval submitted for session {session_id}: {approved}")
        else:
            logger.warning(
                f"Approval submission failed for session {session_id}: request {request_id} not found"
            )

        return success

    async def wait_for_http_approval(
        self,
        session_id: str,
        approval_req: ApprovalRequest,
        timeout_seconds: float,
    ) -> ApprovalResponse:
        if not self.has_session(session_id):
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=False,
                feedback="Session not found",
            )

        session = self.get_session(session_id)
        if not session.turn_in_progress:
            return ApprovalResponse(
                session_id=session_id,
                request_id=approval_req.request_id,
                approved=False,
                feedback="Approval timeout or error",
            )

        session.pending_approval = {
            "request_id": approval_req.request_id,
            "tool_name": approval_req.tool_call.tool_name
            if approval_req.tool_call
            else "",
            "arguments": approval_req.tool_call.arguments
            if approval_req.tool_call
            else {},
        }
        session.approval_event.clear()
        session.approval_response = None
        session.approval_store.add_request(approval_req)
        self._persist_session(session)

        try:
            await asyncio.wait_for(
                session.approval_event.wait(), timeout=timeout_seconds
            )
            if session.approval_response is not None:
                return ApprovalResponse(
                    session_id=session_id,
                    request_id=approval_req.request_id,
                    approved=session.approval_response["decision"] == "approve",
                    feedback=session.approval_response.get("feedback"),
                )
        except asyncio.TimeoutError:
            logger.warning("Approval timeout for session %s", session_id)
        finally:
            session.pending_approval = None
            session.approval_response = None
            _ = session.approval_event.set()
            self._persist_session(session)

        return ApprovalResponse(
            session_id=session_id,
            request_id=approval_req.request_id,
            approved=False,
            feedback="Approval timeout or error",
        )

    def list_sessions(self) -> list[str]:
        """List all active session IDs.

        Returns:
            List of session IDs
        """
        return self._store.list_sessions()

    def get_session_info(self, session_id: str) -> dict[str, Any]:
        """Get session information.

        Args:
            session_id: The session ID

        Returns:
            Dictionary with session info

        Raises:
            KeyError: If session not found
        """
        session = self.get_session(session_id)
        return session.as_dict()

    async def cleanup_idle_sessions(self, max_idle_minutes: int = 30) -> list[str]:
        """Clean up sessions that have been idle for too long.

        Args:
            max_idle_minutes: Maximum idle time in minutes

        Returns:
            List of closed session IDs
        """
        now = datetime.now()
        closed = []

        async with self._lock:
            session_ids = list(self._store.list_sessions())

        for session_id in session_ids:
            try:
                session = self.get_session(session_id)
                idle_time = now - session.last_activity
                if idle_time.total_seconds() > max_idle_minutes * 60:
                    await self.close_session(session_id)
                    closed.append(session_id)
            except KeyError:
                # Session already closed
                pass

        if closed:
            logger.info(f"Cleaned up {len(closed)} idle sessions: {closed}")

        return closed
