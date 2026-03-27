"""SessionManager for managing agent sessions with AgentLoop integration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from coding_agent.approval import ApprovalPolicy
from coding_agent.core.loop import AgentLoop
from coding_agent.core.tape import Tape
from coding_agent.core.context import Context
from coding_agent.core.planner import PlanManager
from coding_agent.providers.base import ChatProvider, StreamingResponse, ToolSchema
from coding_agent.tools.registry import ToolRegistry
from coding_agent.tools.file import register_file_tools
from coding_agent.tools.shell import register_shell_tools
from coding_agent.tools.search import register_search_tools
from coding_agent.tools.planner import register_planner_tools
from coding_agent.tools.subagent import register_subagent_tool
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    WireMessage,
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
        messages: list[dict],
        tools: list[ToolSchema] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Stream mock response."""
        from coding_agent.providers.base import StreamEvent
        
        # Simple mock response
        response_text = "I'll help you with that request. Let me analyze the task... Done!"
        
        for word in response_text.split():
            yield StreamEvent(type="delta", text=word + " ", tool_call=None, error=None)
            await asyncio.sleep(0.01)  # Small delay for streaming effect
        
        yield StreamEvent(type="done", text=None, tool_call=None, error=None)
    
    async def complete(self, messages: list[dict]) -> str:
        """Return complete mock response."""
        return "Mock response"


@dataclass
class Session:
    """A managed agent session.
    
    Attributes:
        id: Unique session identifier
        loop: The AgentLoop instance for this session
        wire: LocalWire for communication between agent and UI
        created_at: When the session was created
        last_activity: Last activity timestamp
        task: Currently running agent task, if any
    """
    id: str
    loop: AgentLoop
    wire: LocalWire
    created_at: datetime
    last_activity: datetime
    task: asyncio.Task | None = None


class SessionManager:
    """Manages agent sessions with lifecycle and resource management."""
    
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
    
    async def create_session(
        self,
        repo_path: Path | None = None,
        approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        provider: Any | None = None,
        max_steps: int = 30,
        enable_parallel: bool = True,
        max_parallel: int = 5,
    ) -> str:
        """Create a new agent session.
        
        Args:
            repo_path: Path to the repository root (default: current directory)
            approval_policy: Policy for tool execution approval
            provider: LLM provider (if None, uses mock/test provider)
            max_steps: Maximum steps per turn
            enable_parallel: Enable parallel tool execution
            max_parallel: Maximum number of parallel tool executions
            
        Returns:
            The session ID
        """
        session_id = str(uuid.uuid4())
        now = datetime.now()
        
        # Use mock provider if none provided
        if provider is None:
            provider = MockProvider()
        
        # Create LocalWire for this session
        wire = LocalWire(session_id)
        
        # Create tape (in-memory for HTTP sessions)
        tape = Tape(path=None)
        
        # Create planner
        planner = PlanManager()
        
        # Create tool registry
        repo_root = repo_path or Path(".")
        registry = ToolRegistry(repo_root=repo_root, enable_cache=True)
        register_file_tools(registry, repo_root=repo_root)
        register_shell_tools(registry, cwd=repo_root)
        register_search_tools(registry, repo_root=repo_root)
        register_planner_tools(registry, planner)
        
        # Create context
        system_prompt = (
            "You are a coding agent. You can read files, edit files, "
            "run shell commands, search the codebase, create task plans, "
            "and dispatch sub-agents for independent sub-tasks.\n\n"
            "Always create a plan (todo_write) before starting complex work. "
            "Update task status as you progress."
        )
        
        # Get provider max context size, default to 8k if not available
        max_context = 8192
        if provider is not None:
            max_context = getattr(provider, 'max_context_size', 8192)
        
        context = Context(max_context, system_prompt, planner=planner)
        
        # Create AgentLoop
        loop = AgentLoop(
            provider=provider,
            tools=registry,
            tape=tape,
            context=context,
            wire=wire,
            max_steps=max_steps,
            enable_parallel=enable_parallel,
            max_parallel=max_parallel,
            approval_policy=approval_policy,
        )
        
        # Register subagent tool (needs provider, tape, wire as consumer)
        if provider is not None:
            register_subagent_tool(
                registry=registry,
                provider=provider,
                tape=tape,
                consumer=wire,  # Wire acts as the consumer
                max_steps=max_steps,
                max_depth=2,
                enable_parallel=enable_parallel,
                max_parallel=max_parallel,
            )
        
        session = Session(
            id=session_id,
            loop=loop,
            wire=wire,
            created_at=now,
            last_activity=now,
            task=None,
        )
        
        async with self._lock:
            self._sessions[session_id] = session
        
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
        if session_id not in self._sessions:
            raise KeyError(f"Session not found: {session_id}")
        return self._sessions[session_id]
    
    def has_session(self, session_id: str) -> bool:
        """Check if a session exists.
        
        Args:
            session_id: The session ID
            
        Returns:
            True if session exists, False otherwise
        """
        return session_id in self._sessions
    
    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up resources.
        
        Args:
            session_id: The session ID to close
            
        Raises:
            KeyError: If session not found
        """
        async with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Session not found: {session_id}")
            
            session = self._sessions[session_id]
            
            # Cancel any running task
            if session.task and not session.task.done():
                session.task.cancel()
                try:
                    await asyncio.wait_for(session.task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            
            # Remove from store
            del self._sessions[session_id]
        
        logger.info(f"Closed session: {session_id}")
    
    async def run_agent(
        self,
        session_id: str,
        prompt: str,
    ) -> None:
        """Run the agent loop with the given prompt.
        
        This method runs loop.run_turn() and handles the wire communication.
        Messages flow through the wire's _outgoing queue which the HTTP server
        consumes for SSE streaming.
        
        Args:
            session_id: The session ID
            prompt: The user's prompt
        """
        session = self.get_session(session_id)
        session.last_activity = datetime.now()
        
        try:
            # Run the turn - wire messages are automatically sent via wire.send()
            result = await session.loop.run_turn(prompt)
            logger.info(f"Session {session_id} turn completed: {result.stop_reason}")
        except asyncio.CancelledError:
            logger.info(f"Session {session_id} turn cancelled")
            raise
        except Exception as e:
            logger.exception(f"Error in session {session_id} turn")
            raise
        finally:
            session.last_activity = datetime.now()
            session.task = None
    
    async def submit_approval(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
    ) -> None:
        """Submit an approval response for a pending request.
        
        Args:
            session_id: The session ID
            request_id: The approval request ID
            approved: Whether the request is approved
            feedback: Optional feedback message
            
        Raises:
            KeyError: If session not found
            ValueError: If no pending approval or request ID mismatch
        """
        session = self.get_session(session_id)
        
        # Create approval response and inject into wire's incoming queue
        response = ApprovalResponse(
            session_id=session_id,
            request_id=request_id,
            approved=approved,
            feedback=feedback,
        )
        session.wire.inject_incoming(response)
        session.last_activity = datetime.now()
        logger.info(f"Approval submitted for session {session_id}: {approved}")
    
    def list_sessions(self) -> list[str]:
        """List all active session IDs.
        
        Returns:
            List of session IDs
        """
        return list(self._sessions.keys())
    
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
        return {
            "id": session.id,
            "created_at": session.created_at.isoformat(),
            "last_activity": session.last_activity.isoformat(),
            "turn_in_progress": session.task is not None and not session.task.done(),
        }
    
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
            session_ids = list(self._sessions.keys())
        
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
