# HTTP Session Hardening Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four blocking review findings in the HTTP/Kimi session flow so `/sessions/{id}/prompt` cannot hang on bootstrap failure, persisted session state remains internally consistent, approval timeout cleanup is correct on the real runtime path, and SSE no longer exposes raw tool results unsafely.

**Architecture:** Keep `coding_agent.app.create_agent()` as the credential-resolution source of truth and harden the HTTP boundary around it. The plan makes `SessionManager.run_agent()` responsible for always producing a terminal turn outcome, narrows persisted session metadata to restart-safe fields, aligns approval cleanup between the HTTP helper path and the real wire-consumer path, and introduces a single SSE-safe tool-result serializer instead of streaming raw tool output.

**Tech Stack:** Python 3.12, FastAPI, asyncio, pytest, httpx + httpx-sse, existing `ApprovalStore`, existing `SessionStore` protocol

**Scope:** Only the review-blocking areas in HTTP session management, HTTP SSE serialization, and their targeted tests.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/coding_agent/ui/session_manager.py` | Modify | Make bootstrap failures terminate cleanly, stop persisting non-restart-safe runtime state, and unify approval lifecycle cleanup |
| `src/coding_agent/ui/http_server.py` | Modify | Stop exposing raw tool results over SSE and keep endpoint behavior aligned with the hardened session manager |
| `src/coding_agent/approval/store.py` | Modify | Add explicit cleanup/removal support for timed-out approval requests so pending state does not leak |
| `tests/ui/test_session_manager_public_api.py` | Modify | Add regression tests for bootstrap failure and hydration/reset semantics |
| `tests/ui/test_http_server.py` | Modify | Add regression tests for parent-turn terminal errors, approval cleanup, and SSE-safe tool result payloads |
| `tests/approval/test_store.py` | Modify | Add coverage for removing pending approval requests after timeout/cancellation |

---

## Task 1: Make bootstrap failures produce a terminal parent turn

**Files:**
- Modify: `src/coding_agent/ui/session_manager.py:368-464`
- Test: `tests/ui/test_session_manager_public_api.py`
- Test: `tests/ui/test_http_server.py`

**Goal:** If `importlib.import_module("coding_agent.app")`, `create_agent()`, plugin lookup, or `PipelineAdapter(...)` raises before `adapter.run_turn()`, the HTTP prompt stream must still end with a parent-level error and `TurnEnd(completion_status=error)` instead of hanging forever.

- [x] **Step 1: Write the failing session-manager bootstrap regression test**

Add this test to `tests/ui/test_session_manager_public_api.py` after `test_run_agent_does_not_hardcode_api_key`:

```python
@pytest.mark.asyncio
async def test_run_agent_emits_error_turn_end_when_bootstrap_fails() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session()
    session = manager.get_session(session_id)

    with patch("importlib.import_module") as import_module:
        import_module.return_value = types.SimpleNamespace(
            create_agent=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bootstrap exploded"))
        )

        await manager.run_agent(session_id, "hello")

    first = await session.wire.get_next_outgoing()
    second = await session.wire.get_next_outgoing()

    assert isinstance(first, StreamDelta)
    assert first.session_id == session_id
    assert first.agent_id == ""
    assert "bootstrap exploded" in first.content

    assert isinstance(second, TurnEnd)
    assert second.session_id == session_id
    assert second.agent_id == ""
    assert second.completion_status is CompletionStatus.ERROR
    assert session.turn_in_progress is False
```

- [x] **Step 2: Write the failing HTTP-level non-hanging SSE regression test**

Add this test to `tests/ui/test_http_server.py` inside `class TestPromptStreaming`:

```python
    async def test_prompt_returns_parent_turn_end_when_agent_bootstrap_fails(self, client):
        create_resp = await client.post("/sessions", json={})
        session_id = create_resp.json()["session_id"]

        with patch("coding_agent.ui.session_manager.importlib.import_module") as import_module:
            import_module.return_value = types.SimpleNamespace(
                create_agent=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bootstrap exploded"))
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
```

- [x] **Step 3: Run the new tests to verify they fail first**

Run:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -k bootstrap_fails -v
uv run pytest tests/ui/test_http_server.py -k bootstrap_fails -v
```

Expected: at least one test fails because `run_agent()` raises before emitting terminal wire messages, or the SSE stream hangs / terminates incorrectly.

- [x] **Step 4: Move all bootstrap/setup logic inside one outer `try/except/finally`**

In `src/coding_agent/ui/session_manager.py`, refactor `run_agent()` so the entire sequence below is inside the same `try` block:

- importing `create_agent`
- calling `create_agent(...)`
- mutating `ctx.config`
- plugin lookup/injection
- constructing `_WireConsumer`
- constructing `PipelineAdapter`
- calling `adapter.run_turn(prompt)`

Use this structure:

```python
    async def run_agent(self, session_id: str, prompt: str) -> None:
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

            create_agent = importlib.import_module("coding_agent.app").create_agent
            pipeline, ctx = create_agent(
                workspace_root=session.repo_path,
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

            ... build consumer ...
            adapter = PipelineAdapter(...)
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
            session.turn_in_progress = False
            session.last_activity = datetime.now()
            self._persist_session(session)
```

**Important:** Keep the parent error `agent_id` empty so `stream_wire_messages()` stops on the terminal parent `TurnEnd`.

- [x] **Step 5: Re-run the same tests and confirm they now pass**

Run:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -k bootstrap_fails -v
uv run pytest tests/ui/test_http_server.py -k bootstrap_fails -v
```

Expected: both tests PASS.

---

## Task 2: Make persisted session metadata restart-safe

**Files:**
- Modify: `src/coding_agent/ui/session_manager.py:104-156`
- Test: `tests/ui/test_session_manager_public_api.py`

**Goal:** The store should persist only metadata that survives restart safely. On hydration, a session must never come back looking mid-turn or mid-approval when there is no live task / queue / waiter attached.

- [x] **Step 1: Write the failing hydration-reset regression test**

Add this test to `tests/ui/test_session_manager_public_api.py`:

```python
def test_hydrated_session_clears_non_restart_safe_runtime_state() -> None:
    store = InMemorySessionStore()
    manager = SessionManager(store=store)
    session = Session(
        id="rehydrate-me",
        created_at=datetime.now(),
        last_activity=datetime.now(),
        approval_store=ApprovalStore(),
        turn_in_progress=True,
        pending_approval={"request_id": "req-123", "tool_name": "bash"},
        approval_response={"decision": "approve", "feedback": "ok"},
    )
    manager.register_session(session)

    reloaded = SessionManager(store=store).get_session("rehydrate-me")

    assert reloaded.turn_in_progress is False
    assert reloaded.pending_approval is None
    assert reloaded.approval_response is None
```

- [x] **Step 2: Run the hydration test to verify it fails first**

Run:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -k rehydrate -v
```

Expected: FAIL because the current persisted payload restores `turn_in_progress`, `pending_approval`, and `approval_response` verbatim.

- [x] **Step 3: Narrow persisted session payload to restart-safe fields**

In `src/coding_agent/ui/session_manager.py`, change `Session.to_store_data()` and `Session.from_store_data()` so they **do not persist** or **do not restore** these runtime-only fields:

- `turn_in_progress`
- `pending_approval`
- `approval_response`

Use this target shape:

```python
    def to_store_data(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "repo_path": None if self.repo_path is None else str(self.repo_path),
            "approval_policy": self.approval_policy.value,
            "max_steps": self.max_steps,
        }
```

And in `from_store_data()` explicitly reset transient state after constructing `Session`:

```python
        session.turn_in_progress = False
        session.pending_approval = None
        session.approval_response = None
```

Also update `Session.as_dict()` if needed so `pending_approval` continues reflecting the live in-memory state only.

- [x] **Step 4: Re-run the hydration test and session-store tests**

Run:

```bash
uv run pytest tests/ui/test_session_manager_public_api.py -k "rehydrate or persists_to_store_backing or redis_session_store_can_rehydrate" -v
```

Expected: PASS.

---

## Task 3: Make approval timeout/error cleanup consistent on the real runtime path

**Files:**
- Modify: `src/coding_agent/approval/store.py`
- Modify: `src/coding_agent/ui/session_manager.py:406-434, 519-578`
- Test: `tests/approval/test_store.py`
- Test: `tests/ui/test_session_manager_public_api.py`

**Goal:** Whether approval is driven through `wait_for_http_approval()` or the actual `_WireConsumer.request_approval()` path, timeout/error must clear both persisted session flags and the underlying `ApprovalStore` pending request.

**Lifecycle invariant (explicit acceptance rule):** Here, “consistency” means consistency **after the approval request lifecycle has ended**, not that every transient intermediate state must be empty at all times.

- During the short window after `/approve` has been submitted but before the waiter consumes the response, `approval_response` may temporarily exist by design.
- Once the request lifecycle ends — including approve, deny, timeout, runtime error, or turn cleanup — all of the following must be true:
  - `session.pending_approval is None`
  - `session.approval_store.get_request(request_id) is None`
  - `session.approval_response` no longer retains an unconsumed terminal state for that request (it has either been cleared or fully consumed, per the implementation contract)

**Why this is explicit:** The review finding is not just about one bad field; it is about `SessionManager` state and `ApprovalStore` state drifting apart. Acceptance must therefore check that both layers are clean when the request lifecycle is over, not just one of them.

- [x] **Step 1: Write the failing ApprovalStore cleanup test**

Add this test to `tests/approval/test_store.py`:

```python
def test_remove_request_deletes_pending_entry(store: ApprovalStore, sample_approval_request: ApprovalRequest) -> None:
    store.add_request(sample_approval_request)

    store.remove_request(sample_approval_request.request_id)

    assert store.get_request(sample_approval_request.request_id) is None
```

If `ApprovalStore` has no such method yet, that is the expected initial failure.

- [x] **Step 2: Write the failing real-path approval timeout regression test**

Add this test to `tests/ui/test_session_manager_public_api.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_clears_pending_approval_after_runtime_timeout() -> None:
    manager = SessionManager(store=InMemorySessionStore())
    session_id = await manager.create_session(provider=MockProvider())
    session = manager.get_session(session_id)

    class TimeoutApprovalStore(ApprovalStore):
        async def wait_for_response(self, request_id: str, timeout: int):
            await asyncio.sleep(0)
            return None

    session.approval_store = TimeoutApprovalStore()
    manager.register_session(session)

    req = ApprovalRequest(
        session_id=session_id,
        request_id="req-timeout",
        tool_call=ToolCallDelta(
            session_id=session_id,
            tool_name="bash",
            arguments={"command": "pwd"},
            call_id="call-timeout",
        ),
        timeout_seconds=0,
    )

    consumer = None

    class FakeAdapter:
        def __init__(self, pipeline, ctx, consumer_obj) -> None:
            nonlocal consumer
            consumer = consumer_obj

        async def run_turn(self, prompt: str) -> None:
            del prompt
            assert consumer is not None
            await consumer.request_approval(req)

    fake_pipeline = types.SimpleNamespace(
        _registry=types.SimpleNamespace(get=lambda _: types.SimpleNamespace(_instance=None)),
        _directive_executor=None,
    )
    fake_ctx = types.SimpleNamespace(config={})

    with (
        patch("importlib.import_module") as import_module,
        patch("coding_agent.ui.session_manager.PipelineAdapter", FakeAdapter),
    ):
        import_module.return_value = types.SimpleNamespace(
            create_agent=lambda **kwargs: (fake_pipeline, fake_ctx)
        )
        await manager.run_agent(session_id, "needs approval")

    reloaded = manager.get_session(session_id)
    assert reloaded.pending_approval is None
    assert reloaded.approval_response is None
    assert reloaded.approval_store.get_request("req-timeout") is None
```

- [x] **Step 3: Run both new tests to verify they fail first**

Run:

```bash
uv run pytest tests/approval/test_store.py -k remove_request -v
uv run pytest tests/ui/test_session_manager_public_api.py -k runtime_timeout -v
```

Expected: FAIL because no store cleanup method exists yet and the real `_WireConsumer.request_approval()` path leaves request/session state behind.

- [x] **Step 4: Add explicit pending-request cleanup in `ApprovalStore`**

In `src/coding_agent/approval/store.py`, add:

```python
    def remove_request(self, request_id: str) -> None:
        _ = self._pending.pop(request_id, None)
```

And modify `wait_for_response()` so it cleans up on both success and timeout:

```python
    async def wait_for_response(self, request_id: str, timeout: int) -> ApprovalResponse | None:
        if request_id not in self._pending:
            logger.warning(f"Wait for unknown request: {request_id}")
            return None

        pending = self._pending[request_id]
        if pending.response is not None:
            try:
                return pending.response
            finally:
                self.remove_request(request_id)

        try:
            await asyncio.wait_for(pending.response_event.wait(), timeout=timeout)
            return pending.response
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.debug(f"Timeout waiting for response to {request_id}")
            return None
        finally:
            self.remove_request(request_id)
```

- [x] **Step 5: Make `_WireConsumer.request_approval()` mirror the HTTP helper cleanup semantics**

In `src/coding_agent/ui/session_manager.py`, update `_WireConsumer.request_approval()` so it:

1. sets `session.pending_approval`
2. clears `session.approval_event`
3. clears `session.approval_response`
4. persists that state **before** waiting
5. clears `pending_approval` / `approval_response` in a `finally` block
6. persists cleanup in that `finally`

Target shape:

```python
            async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
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
```

Also remove any fallback logic in `submit_approval()` that treats a stale `pending_approval` dict as success when the store no longer knows about the request.

- [x] **Step 6: Re-run approval tests**

Run:

```bash
uv run pytest tests/approval/test_store.py -v
uv run pytest tests/ui/test_session_manager_public_api.py -k "runtime_timeout or hardcode_api_key" -v
uv run pytest tests/ui/test_http_server.py -k "approve or wait_for_approval_timeout" -v
```

Expected: PASS.

---

## Task 4: Stop streaming raw tool results directly over HTTP SSE

**Files:**
- Modify: `src/coding_agent/ui/http_server.py:110-208`
- Test: `tests/ui/test_http_server.py`

**Goal:** HTTP SSE must not expose raw `ToolResultDelta.result` payloads to browsers by default. It should emit a safe summary payload that preserves UX utility without leaking arbitrary tool output.

**HTTP contract boundary (explicit scope limit):** This hardening task only constrains the public HTTP SSE schema for `ToolResultDelta`. It does not broaden the scope to every tool-related wire message.

- `ToolResultDelta` over HTTP SSE must use an explicit allowlist payload.
- `ToolResultDelta.result` must not be streamed to browsers raw by default.
- UX-critical fields such as `display_result`, `tool_name`, `call_id`, and `is_error` may remain exposed.
- This task does **not** require changing `ToolCallEnd.result`, TUI rendering, or the underlying wire protocol objects.

**Why this is explicit:** The blocking review finding is specifically about `ToolResultDelta` being serialized raw. If the plan says “all tool results are now safe,” it would silently expand the scope beyond what this hardening pass is actually changing.

- [x] **Step 1: Write the failing SSE sanitization regression test**

Replace or split `test_tool_result_delta_conversion_preserves_result_and_display` in `tests/ui/test_http_server.py` with this stricter test:

```python
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
```

- [x] **Step 2: Run the sanitization test to verify it fails first**

Run:

```bash
uv run pytest tests/ui/test_http_server.py -k redacts_raw_result_payload -v
```

Expected: FAIL because current SSE output includes the full `result` field.

- [x] **Step 3: Add a dedicated SSE-safe tool result serializer**

In `src/coding_agent/ui/http_server.py`, add a helper above `_wire_message_to_event()`:

```python
def _http_safe_tool_result_payload(msg: ToolResultDelta) -> dict[str, Any]:
    return {
        "session_id": msg.session_id,
        "agent_id": msg.agent_id,
        "tool_name": msg.tool_name,
        "call_id": msg.call_id,
        "result": None,
        "display_result": msg.display_result,
        "is_error": msg.is_error,
        "timestamp": msg.timestamp.isoformat(),
    }
```

Then change the `case ToolResultDelta():` branch to:

```python
        case ToolResultDelta():
            return {
                "event": "ToolResultDelta",
                "data": json.dumps(_http_safe_tool_result_payload(msg)),
            }
```

**Boundary rule:** This change is HTTP-SSE-specific only. Do not change the underlying wire protocol object or TUI rendering in this task.

- [x] **Step 4: Re-run the tool-result SSE tests and child-stream regression test**

Run:

```bash
uv run pytest tests/ui/test_http_server.py -k "redacts_raw_result_payload or prompt_streams_subagent_child_events_from_real_http_session" -v
```

Expected: PASS, but update the child-stream test assertions if it previously depended on raw `result` being echoed back over HTTP.

---

## Task 5: Run the focused verification set and confirm no scoped regressions

**Files:**
- Verify only; no code changes expected

- [x] **Step 1: Run the focused scoped suites**

Run:

```bash
uv run pytest tests/approval/test_store.py -v
uv run pytest tests/ui/test_session_manager_public_api.py -v
uv run pytest tests/ui/test_http_server.py -v
```

Expected: all tests PASS.

- [x] **Step 2: Run LSP diagnostics on modified source files**

Check:

- `src/coding_agent/ui/session_manager.py`
- `src/coding_agent/ui/http_server.py`
- `src/coding_agent/approval/store.py`

Expected: no errors.

- [x] **Step 3: Re-run the live local K8s smoke verification if the deployment still exists**

Commands:

```bash
kubectl get deployment coding-agent-dev-coding-agent
kubectl rollout status deployment/coding-agent-dev-coding-agent
kubectl port-forward deployment/coding-agent-dev-coding-agent 18085:8080
```

In a second shell:

```bash
curl -sS http://127.0.0.1:18085/healthz
curl -sS http://127.0.0.1:18085/readyz
curl -sS -X POST http://127.0.0.1:18085/sessions -H "Content-Type: application/json" -d '{}'
curl -sS http://127.0.0.1:18085/sessions/<SESSION_ID>
curl -i -N -sS -X POST http://127.0.0.1:18085/sessions/<SESSION_ID>/prompt -H "Content-Type: application/json" -d '{"prompt":"Reply with exactly: review fixes ok"}'
```

Expected:
- `/healthz` → 200
- `/readyz` → 200
- session creation succeeds
- `GET /sessions/<SESSION_ID>` returns 200 and shows a sane externally observable session shape from `Session.as_dict()` via the HTTP `get_session` endpoint, including `id`, `created_at`, `last_activity`, `turn_in_progress`, and a boolean `pending_approval`
- prompt stream ends with parent `TurnEnd(completion_status=completed)`
- `ToolResultDelta` events, if any, no longer expose raw `result`

**Evidence note:** `GET /sessions/{id}` is part of the required live smoke because Task 2 changes `to_store_data()` / `from_store_data()`, while the externally observable surface remains `Session.as_dict()` through the HTTP `get_session` endpoint. Without re-checking that endpoint after the hardening changes, the restart-safe session-state evidence is incomplete.

---

## Self-Review Checklist

- [x] Task 1 covers the bootstrap-hang review finding with both unit-level and HTTP-level regression tests
- [x] Task 2 covers the persisted-runtime-state review finding by narrowing or resetting stored fields
- [x] Task 3 covers both session-level and ApprovalStore-level cleanup so timeout state cannot linger in only one layer
- [x] Task 4 covers the security review finding by changing only the HTTP SSE boundary, not unrelated UI protocols
- [x] Task 5 verifies the full scoped suites plus live smoke validation
- [x] No step depends on undocumented placeholder behavior

---

Plan complete and saved to `docs/superpowers/plans/2026-04-09-http-session-hardening-review-fixes.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

## Closure Note — 2026-04-10

- Review-fixes closure re-check outcome: Task 2 found a `live minimal gap`, so Task 3 executed before closure.
- Task 3 removed the `legacy_pending_matches` false-success fallback from `submit_approval()`.
- `/sessions/{id}/approve` now requires a live `ApprovalStore`-backed request; `session.pending_approval` is no longer allowed to create a false success path.
- `wait_for_http_approval()` seeds `ApprovalStore`, keeping the helper path compatible with the tightened contract.
- Final truth-source model: `ApprovalStore` is the truth source; `session.pending_approval` is transient projection/UI state.
- Focused closure verification passed for:
  - `tests/approval/test_store.py`
  - `tests/ui/test_session_manager_public_api.py`
  - `tests/ui/test_http_server.py`
- The first fresh full-suite verification exposed 2 stale legacy `/approve` tests that still assumed the old session-only contract; those tests were updated to use live store-backed approval requests.
- Final fresh full-suite verification (`uv run pytest tests/ -v`) is green: `1626 passed, 31 warnings`.
- Closure evidence: `.sisyphus/evidence/review-fixes-closure-2026-04-10.txt`
- Final bucket verdict: `minimal-tail-then-closure`
