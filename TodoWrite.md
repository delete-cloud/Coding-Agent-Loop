# P2 Cleanup and Hardening

## Phase 1: Foundation
- [ ] **Task 1**: Consolidate Wire Protocol
  - Delete wire.py, update imports, fix tests
  
- [ ] **Task 2**: HTTP Server Integration with AgentLoop
  - Create session_manager.py, integrate with loop, streaming
  
- [ ] **Task 3**: FastAPI Modernization
  - Replace on_event with lifespan, add health endpoint

## Phase 2: Core Features
- [ ] **Task 4**: Input Validation & Security
  - Pydantic models, API key auth, rate limiting
  
- [ ] **Task 5**: ApprovalStore Integration
  - Use ApprovalStore in HTTP server, remove duplication
  
- [ ] **Task 6**: Session Persistence
  - Persistence interface, SQLite implementation

## Phase 3: Polish & Testing
- [ ] **Task 7**: RichConsumer Interactive Approval
  - prompt-toolkit approval UI, timeout handling
  
- [ ] **Task 8**: CORS and Production Config
  - CORS middleware, production settings
  
- [ ] **Task 9**: Testing & Documentation
  - Security tests, integration tests, API docs
