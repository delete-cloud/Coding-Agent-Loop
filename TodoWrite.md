# P2: Wire + TUI + HTTP Implementation

## Tasks

- [ ] **Task 1**: Wire Protocol (`wire/protocol.py`, `wire/local.py`)
  - Define WireMessage, StreamDelta, ToolCallDelta, ApprovalRequest, ApprovalResponse, TurnEnd
  - Implement LocalWire with async queues
  - Tests for serialization and queue operations
  
- [ ] **Task 2**: HTTP API Server (`ui/http_server.py`)
  - FastAPI app with 6 endpoints
  - SSE streaming support
  - Session management with idle timeout
  - Tests for all endpoints
  
- [ ] **Task 3**: Approval System (`approval/policy.py`, `approval/store.py`)
  - ApprovalPolicy enum (yolo, interactive, auto)
  - ApprovalStore with timeout handling
  - Integration hooks for core loop
  - Tests for policies and store
  
- [ ] **Task 4**: Integration & CLI
  - Add `serve` command to __main__.py
  - Integrate wire with core loop
  - Connect approval system
  
- [ ] **Task 5**: Testing & Validation
  - Unit tests for all new modules
  - Integration tests
  - Manual verification

## Dependencies to Add
- fastapi >=0.100.0
- uvicorn[standard] >=0.23.0  
- sse-starlette >=1.6.0
