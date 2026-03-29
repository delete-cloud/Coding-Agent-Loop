# P2 Cleanup and Hardening Plan

Complete fix of all P1 and P2 issues identified in the code review.

## Task 1: Consolidate Wire Protocol

**Problem**: Two conflicting protocol definitions (wire.py vs wire/protocol.py)

**Files:**
- Delete: `src/coding_agent/wire.py`
- Modify: `src/coding_agent/wire/__init__.py` - Remove legacy re-exports
- Modify: All files importing from old wire.py

**Steps:**
1. Find all imports of old wire module
2. Update to use new wire.protocol types
3. Remove wire.py
4. Update tests

**Exit Criteria:**
- Only one wire protocol definition exists
- All imports use wire.protocol
- All tests pass

---

## Task 2: HTTP Server Integration with AgentLoop

**Problem**: send_prompt endpoint is mocked, not connected to real AgentLoop

**Files:**
- Modify: `src/coding_agent/ui/http_server.py`
- Create: `src/coding_agent/ui/session_manager.py` - Session lifecycle management

**Implementation:**
```python
class SessionManager:
    """Manages agent sessions for HTTP API."""
    
    def create_session(self) -> str:
        # Create session, AgentLoop, Wire connection
        
    def get_session(self, session_id: str) -> Session:
        # Get or raise 404
        
    def close_session(self, session_id: str):
        # Cleanup resources

# In send_prompt endpoint:
# - Get or create session
# - Create LocalWire for session
# - Run AgentLoop with wire in background
# - Stream wire messages via SSE
# - Handle approval requests via HTTP endpoint
```

**Exit Criteria:**
- HTTP API can run actual agent tasks
- StreamDelta messages flow through SSE
- Approval requests work end-to-end
- Session lifecycle managed properly

---

## Task 3: FastAPI Modernization

**Problem**: Using deprecated @app.on_event, missing lifespan management

**Files:**
- Modify: `src/coding_agent/ui/http_server.py`

**Changes:**
1. Replace @app.on_event with lifespan context manager
2. Add proper startup/shutdown hooks
3. Add health check endpoint

**Exit Criteria:**
- No deprecation warnings
- Clean startup/shutdown
- Health endpoint works

---

## Task 4: Input Validation & Security

**Problem**: No input validation, no auth, no rate limiting

**Files:**
- Modify: `src/coding_agent/ui/http_server.py`
- Create: `src/coding_agent/ui/auth.py` - Authentication
- Create: `src/coding_agent/ui/rate_limit.py` - Rate limiting middleware

**Implementation:**
```python
# Pydantic models for validation
class PromptRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=10000)

class CreateSessionRequest(BaseModel):
    repo_path: Path | None = None
    approval_policy: str = "auto"

# API Key auth
async def verify_api_key(x_api_key: str = Header(...)):
    # Validate against config
    
# Rate limiting
@app.middleware("http")
async def rate_limit(request: Request, call_next):
    # Check rate limit per API key
```

**Exit Criteria:**
- All inputs validated
- API key auth working
- Rate limiting enforced
- Tests for security features

---

## Task 5: ApprovalStore Integration

**Problem**: HTTP server implements own approval logic instead of using ApprovalStore

**Files:**
- Modify: `src/coding_agent/ui/http_server.py`
- Modify: `src/coding_agent/ui/session_manager.py`

**Changes:**
1. SessionManager creates ApprovalStore per session
2. HTTP server uses ApprovalStore for pending approvals
3. Remove duplicated approval state from SessionState

**Exit Criteria:**
- ApprovalStore used consistently
- No duplicated approval logic
- Approval flow works via HTTP

---

## Task 6: Session Persistence

**Problem**: Sessions lost on server restart

**Files:**
- Create: `src/coding_agent/ui/persistence.py` - Session persistence interface
- Create: `src/coding_agent/ui/persistence_sqlite.py` - SQLite implementation
- Modify: `src/coding_agent/ui/session_manager.py`

**Implementation:**
```python
class SessionPersistence(Protocol):
    async def save_session(self, session: Session) -> None: ...
    async def load_session(self, session_id: str) -> Session | None: ...
    async def delete_session(self, session_id: str) -> None: ...

class SQLiteSessionPersistence:
    # SQLite implementation
```

**Exit Criteria:**
- Sessions survive server restart
- Persistence interface defined
- SQLite implementation working

---

## Task 7: RichConsumer Interactive Approval

**Problem**: TODO comment, always auto-approves

**Files:**
- Modify: `src/coding_agent/ui/rich_consumer.py`

**Implementation:**
- Use prompt-toolkit for interactive approval
- Show tool call details
- Timeout handling
- Configurable auto-approve for safe tools

**Exit Criteria:**
- Interactive approval UI working
- Timeout handled gracefully
- Tests for approval flow

---

## Task 8: CORS and Production Config

**Problem**: No CORS, no production configuration

**Files:**
- Modify: `src/coding_agent/ui/http_server.py`
- Create: `src/coding_agent/ui/config.py` - Server configuration

**Changes:**
- Add CORS middleware
- Configure allowed origins
- Add production settings (workers, timeouts)

**Exit Criteria:**
- CORS working for web clients
- Production config available

---

## Task 9: Testing & Documentation

**Files:**
- Create: `tests/ui/test_security.py` - Auth and rate limit tests
- Create: `tests/ui/test_session_manager.py` - Session lifecycle tests
- Create: `tests/integration/test_http_agent_loop.py` - Full integration
- Modify: `docs/` - API documentation

**Exit Criteria:**
- Security features tested
- Integration tests pass
- API documented

---

## Dependencies to Add

```toml
slowapi = ">=0.1.9"  # Rate limiting
python-jose = {extras = ["cryptography"], version = ">=3.3.0"}  # JWT
passlib = {extras = ["bcrypt"], version = ">=1.7.4"}  # Password hashing
```

---

## Execution Order

1. Task 1: Wire consolidation (foundation)
2. Task 2: HTTP integration (core feature)
3. Task 3: FastAPI modernization (cleanup)
4. Task 4: Security (auth, validation, rate limiting) - parallel with 5
5. Task 5: ApprovalStore integration - parallel with 4
6. Task 6: Session persistence (optional but recommended)
7. Task 7: RichConsumer approval (UI polish)
8. Task 8: CORS and config (production ready)
9. Task 9: Testing and docs (final verification)

---

## Success Criteria

- [ ] All P1 issues from review fixed
- [ ] All P2 issues from review fixed or documented
- [ ] 500+ tests passing
- [ ] Security audit passed
- [ ] Production deployment ready
