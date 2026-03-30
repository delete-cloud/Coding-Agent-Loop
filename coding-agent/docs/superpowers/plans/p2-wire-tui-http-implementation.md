# P2: Wire + TUI + HTTP API Implementation Plan

## Overview
Complete the P2 phase by implementing the missing components: Wire protocol, HTTP API server, and Approval system. Rich-based TUI is already partially implemented.

## Architecture

```
coding-agent/
  src/coding_agent/
    wire/
      __init__.py
      protocol.py          # Typed message contracts (WireMessage, StreamDelta, ApprovalRequest, TurnEnd)
      local.py             # Async implementation for local session communication
    ui/
      rich_tui.py          # EXISTING - Rich-based TUI with streaming (keep)
      http_server.py       # NEW - FastAPI-based REST + SSE API
    approval/
      __init__.py
      policy.py            # Approval policies (yolo, interactive, auto)
      store.py             # Approval request storage and state management
  tests/
    wire/
      test_protocol.py
      test_local.py
    ui/
      test_http_server.py
    approval/
      test_policy.py
      test_store.py
```

## Task 1: Wire Protocol

**Files:**
- Create: `src/coding_agent/wire/__init__.py`
- Create: `src/coding_agent/wire/protocol.py`
- Create: `src/coding_agent/wire/local.py`

**Requirements:**

### protocol.py
Define typed message classes:

```python
@dataclass
class WireMessage:
    """Base class for all wire messages."""
    session_id: str
    timestamp: datetime

@dataclass  
class StreamDelta(WireMessage):
    """Streaming content delta from agent."""
    content: str
    role: str = "assistant"

@dataclass
class ToolCallDelta(WireMessage):
    """Tool call being streamed."""
    tool_name: str
    arguments: dict
    call_id: str

@dataclass
class ApprovalRequest(WireMessage):
    """Request for user approval."""
    request_id: str
    tool_call: ToolCallDelta
    timeout_seconds: int = 120

@dataclass
class ApprovalResponse(WireMessage):
    """User response to approval request."""
    request_id: str
    approved: bool
    feedback: str | None = None

@dataclass
class TurnEnd(WireMessage):
    """End of current turn."""
    turn_id: str
    completion_status: str  # "completed", "blocked", "error"
```

### local.py
Async implementation for local session:

```python
class LocalWire:
    """Async queue-based wire for local sessions."""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._outgoing: asyncio.Queue[WireMessage] = asyncio.Queue()
        self._incoming: asyncio.Queue[WireMessage] = asyncio.Queue()
    
    async def send(self, message: WireMessage) -> None:
        """Send message to consumer."""
        await self._outgoing.put(message)
    
    async def receive(self) -> WireMessage:
        """Receive message from producer."""
        return await self._incoming.get()
    
    async def request_approval(
        self, 
        tool_call: ToolCallDelta,
        timeout: int = 120
    ) -> ApprovalResponse:
        """Send approval request and wait for response."""
        request = ApprovalRequest(...)
        await self.send(request)
        # Wait for ApprovalResponse with timeout
```

**Tests:**
- Message serialization/deserialization
- LocalWire queue operations
- Approval timeout handling

**Exit Criteria:**
- All WireMessage types defined and tested
- LocalWire can send/receive messages
- Approval request/response flow works

---

## Task 2: HTTP API Server

**Files:**
- Create: `src/coding_agent/ui/http_server.py`

**Requirements:**

FastAPI-based server with SSE support:

```python
app = FastAPI()

@app.post("/sessions")
async def create_session() -> dict:
    """Create new session."""
    
@app.post("/sessions/{session_id}/prompt")
async def send_prompt(session_id: str, prompt: str) -> StreamingResponse:
    """Send message, returns SSE stream.
    
    Returns 409 if a turn is already in progress.
    """
    
@app.post("/sessions/{session_id}/approve")
async def approve_request(
    session_id: str, 
    request_id: str,
    approved: bool,
    feedback: str | None = None
) -> dict:
    """Respond to approval request."""
    
@app.get("/sessions/{session_id}/events")
async def get_events(session_id: str) -> EventSourceResponse:
    """Persistent SSE event stream."""
    
@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """Get session state."""
    
@app.delete("/sessions/{session_id}")
async def close_session(session_id: str) -> dict:
    """Close session and release resources."""
```

**Key Design Points:**
- Per-session one-turn-at-a-time: 409 Conflict if turn in progress
- SSE events match WireMessage class names: `event: StreamDelta`
- Auto-reject on approval timeout (120s default)
- Session idle timeout: 30 minutes
- Multiple SSE clients can connect (fan-out)

**CLI Entry:**
```bash
python -m coding_agent serve --port 8080
```

**Tests:**
- Session creation
- Prompt sending with SSE streaming
- Approval flow
- Concurrent turn rejection (409)
- Session timeout

**Exit Criteria:**
- All 6 endpoints working
- SSE streaming functional
- Approval timeout works
- Tests passing

---

## Task 3: Approval System

**Files:**
- Create: `src/coding_agent/approval/__init__.py`
- Create: `src/coding_agent/approval/policy.py`
- Create: `src/coding_agent/approval/store.py`

**Requirements:**

### policy.py
Approval policies and configuration:

```python
class ApprovalPolicy(Enum):
    YOLO = "yolo"           # Auto-approve all
    INTERACTIVE = "interactive"  # Ask for approval
    AUTO = "auto"           # Auto-approve safe tools only

@dataclass
class PolicyConfig:
    policy: ApprovalPolicy
    safe_tools: set[str]  # For AUTO mode
    timeout_seconds: int = 120
```

### store.py
Approval request storage:

```python
class ApprovalStore:
    """In-memory store for pending approval requests."""
    
    def __init__(self):
        self._pending: dict[str, ApprovalRequest] = {}
        self._responses: dict[str, ApprovalResponse] = {}
    
    def add_request(self, request: ApprovalRequest) -> None:
        """Add new approval request."""
        
    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Get pending request."""
        
    def respond(self, response: ApprovalResponse) -> bool:
        """Record response to request."""
        
    async def wait_for_response(
        self, 
        request_id: str,
        timeout: int
    ) -> ApprovalResponse | None:
        """Wait for response with timeout."""
```

**Integration with Core Loop:**
- Hook into tool execution to check policy
- If interactive: send ApprovalRequest via wire
- Wait for ApprovalResponse
- If timeout: auto-reject with "Approval timeout"

**Tests:**
- Policy configuration
- Request/response storage
- Timeout handling
- Integration with mock wire

**Exit Criteria:**
- All approval policies work
- Store correctly manages pending requests
- Timeout auto-rejection works

---

## Task 4: Integration & CLI

**Files:**
- Modify: `src/coding_agent/__main__.py`
- Modify: `src/coding_agent/core/loop.py`

**Requirements:**

### __main__.py
Add serve command:

```python
@cli.command()
@click.option("--port", default=8080)
@click.option("--host", default="127.0.0.1")
def serve(port: int, host: str):
    """Start HTTP API server."""
    import uvicorn
    from coding_agent.ui.http_server import app
    uvicorn.run(app, host=host, port=port)
```

### loop.py Integration
- Accept Wire instance in constructor
- Stream deltas via wire instead of direct print
- Check approval policy before tool execution
- Send ApprovalRequest for interactive mode

**Exit Criteria:**
- `python -m coding_agent serve` works
- Core loop can use wire for streaming
- Approval system integrated

---

## Task 5: Testing & Validation

**Files:**
- Create all test files

**Test Coverage:**
- Unit tests for each module
- Integration test: full HTTP API flow
- Integration test: approval workflow

**Exit Criteria:**
- 90%+ test coverage for new code
- All tests passing
- Manual verification of HTTP endpoints

---

## Dependencies to Add

```toml
[project.dependencies]
fastapi = ">=0.100.0"
uvicorn = {extras = ["standard"], version = ">=0.23.0"}
sse-starlette = ">=1.6.0"
```

---

## Execution Order

1. **Task 1**: Wire protocol (foundation)
2. **Task 2**: HTTP server (can parallel with Task 3)
3. **Task 3**: Approval system (can parallel with Task 2)
4. **Task 4**: Integration & CLI
5. **Task 5**: Testing & validation

**Estimated Time:** 1.5-2 days
**Recommended:** Use subagents for Tasks 1-3 in parallel, then integrate.
