"""Local async wire implementation for in-process sessions.

This module provides LocalWire, an async queue-based wire implementation
for local CLI sessions where the agent and UI run in the same process.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coding_agent.wire.protocol import (
        ApprovalRequest,
        ApprovalResponse,
        ToolCallDelta,
        WireMessage,
    )


class LocalWire:
    """Async queue-based wire for local sessions.
    
    This implementation uses asyncio.Queue for bidirectional communication
    between the agent loop and UI components. It's designed for local CLI
    sessions where both components run in the same process.
    
    Attributes:
        session_id: Unique identifier for this wire session
    """
    
    def __init__(self, session_id: str) -> None:
        """Initialize LocalWire with given session ID.
        
        Args:
            session_id: Unique identifier for this session
        """
        self.session_id = session_id
        self._outgoing: asyncio.Queue[WireMessage] = asyncio.Queue()
        self._incoming: asyncio.Queue[WireMessage] = asyncio.Queue()
    
    async def send(self, message: WireMessage) -> None:
        """Send message to consumer (UI).
        
        Args:
            message: The wire message to send
        """
        await self._outgoing.put(message)
    
    async def receive(self) -> WireMessage:
        """Receive message from producer (agent).
        
        Returns:
            The received wire message
        """
        return await self._incoming.get()
    
    async def request_approval(
        self, 
        tool_call: ToolCallDelta,
        timeout: int = 120,
    ) -> ApprovalResponse:
        """Send approval request and wait for response with timeout.
        
        This method sends an ApprovalRequest message and waits for an
        ApprovalResponse from the consumer. If the timeout is exceeded,
        returns an ApprovalResponse with approved=False.
        
        The flow is:
        1. Agent calls request_approval() -> sends ApprovalRequest to _outgoing
        2. UI consumes from _outgoing and sends ApprovalResponse to _incoming
        3. Agent receives ApprovalResponse from _incoming
        
        Args:
            tool_call: The tool call requiring approval
            timeout: Maximum seconds to wait for response (default: 120)
            
        Returns:
            The user's approval response, or a denial response if timeout
        """
        from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse
        
        request = ApprovalRequest(
            session_id=self.session_id,
            request_id=tool_call.call_id,
            tool_call=tool_call,
            timeout_seconds=timeout,
        )
        
        # Send the approval request to outgoing queue (for UI to consume)
        await self.send(request)
        
        # Wait for response on incoming queue (from UI)
        try:
            response = await asyncio.wait_for(
                self._incoming.get(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Return denial response on timeout for consistency with HTTP server
            return ApprovalResponse(
                session_id=self.session_id,
                request_id=request.request_id,
                approved=False,
                feedback=f"Approval timeout after {timeout} seconds",
            )
        
        # Validate response type
        if not isinstance(response, ApprovalResponse):
            raise ValueError(
                f"Expected ApprovalResponse, got {type(response).__name__}"
            )
        
        # Validate request ID matches
        if response.request_id != request.request_id:
            raise ValueError(
                f"Response request_id mismatch: expected {request.request_id}, "
                f"got {response.request_id}"
            )
        
        return response
    
    def inject_incoming(self, message: WireMessage) -> None:
        """Inject a message into the incoming queue (for testing/UI).
        
        This method allows external components (like tests or UI) to
        inject messages that the agent will receive via receive().
        
        Args:
            message: Message to inject into the incoming queue
        """
        self._incoming.put_nowait(message)
    
    def consume_outgoing(self) -> WireMessage | None:
        """Consume a message from the outgoing queue without blocking.
        
        This method allows external components (like tests or UI) to
        retrieve messages sent by the agent without async waiting.
        
        Returns:
            The message if available, None if queue is empty
        """
        try:
            return self._outgoing.get_nowait()
        except asyncio.QueueEmpty:
            return None
    
    async def get_next_outgoing(self) -> WireMessage:
        """Get the next outgoing message (blocking).
        
        This method is useful for UI components that need to wait for
        messages from the agent.
        
        Returns:
            The next message from the outgoing queue
        """
        return await self._outgoing.get()
