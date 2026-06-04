from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any

import pytest

from coding_agent.acp.server import AcpServer, run_stdio
from coding_agent.events import DisplayEvent
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)


class AcpCompatClient:
    def __init__(self, server: AcpServer) -> None:
        self._stdin = _BlockingLineInput()
        self._stdout: list[str] = []
        self._next_id = 1
        self._cursor = 0
        self._task = asyncio.create_task(
            run_stdio(server, stdin=self._stdin, stdout=self._stdout.append)
        )

    async def close(self) -> None:
        self._stdin.close()
        await asyncio.wait_for(self._task, timeout=1)

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.send_request(method, params)
        return await self.wait_for_response(request_id)

    def send_request(self, method: str, params: dict[str, Any]) -> int:
        request_id = self._next_id
        self._next_id += 1
        self._stdin.put(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        return request_id

    async def wait_for_response(self, request_id: object) -> dict[str, Any]:
        while True:
            message = await self.next_message()
            if message.get("id") == request_id and "method" not in message:
                return message

    async def next_message(self, *, timeout: float = 1.0) -> dict[str, Any]:
        return await asyncio.wait_for(self._next_message(), timeout=timeout)

    async def _next_message(self) -> dict[str, Any]:
        while self._cursor >= len(self._stdout):
            await asyncio.sleep(0)
        raw_line = self._stdout[self._cursor]
        self._cursor += 1
        message = json.loads(raw_line)
        if not isinstance(message, dict):
            raise AssertionError(f"ACP message must be an object: {message!r}")
        return message

    def respond(self, request_id: object, result: dict[str, Any]) -> None:
        self._stdin.put(
            json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "result": result},
                separators=(",", ":"),
            )
            + "\n"
        )


class _BlockingLineInput:
    def __init__(self) -> None:
        self._lines: Queue[str] = Queue()

    def readline(self) -> str:
        return self._lines.get()

    def put(self, line: str) -> None:
        self._lines.put(line)

    def close(self) -> None:
        self._lines.put("")


class HarnessManager:
    def __init__(self, repo_path: Path) -> None:
        self.wire = LocalWire("sess-compat")
        self.repo_path = repo_path
        self.calls: list[tuple[str, Any]] = []
        self.display_events = [
            DisplayEvent(
                source_event_id="event-1",
                run_id="run-1",
                sequence=1,
                display_kind="assistant_text_delta",
                payload={"content": "loaded", "role": "assistant"},
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]

    async def create_session(self, **kwargs: Any) -> str:
        self.calls.append(("create_session", kwargs))
        return "sess-compat"

    async def get_session_async(self, session_id: str) -> Any:
        self.calls.append(("get_session_async", session_id))
        return SimpleNamespace(
            id=session_id,
            wire=self.wire,
            repo_path=self.repo_path,
            last_activity=datetime(2026, 1, 2, tzinfo=UTC),
            default_run_target=None,
        )

    async def update_session_mcp_servers(
        self,
        session_id: str,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> None:
        self.calls.append(
            (
                "update_session_mcp_servers",
                {"session_id": session_id, "mcp_servers": mcp_servers},
            )
        )

    async def update_session_additional_directories(
        self,
        session_id: str,
        additional_directories: list[str],
    ) -> None:
        self.calls.append(
            (
                "update_session_additional_directories",
                {
                    "session_id": session_id,
                    "additional_directories": additional_directories,
                },
            )
        )

    async def run_agent(self, session_id: str, prompt: str) -> None:
        self.calls.append(("run_agent", {"session_id": session_id, "prompt": prompt}))
        await self.wire.send(StreamDelta(session_id=session_id, content="compat"))
        await self.wire.send(
            TurnEnd(
                session_id=session_id,
                turn_id="turn-1",
                completion_status=CompletionStatus.COMPLETED,
            )
        )

    async def cancel_session_turn(self, session_id: str) -> Any:
        self.calls.append(("cancel_session_turn", session_id))
        return None

    async def submit_approval_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: str = "once",
    ) -> Any:
        self.calls.append(
            (
                "submit_approval_response",
                {
                    "session_id": session_id,
                    "request_id": request_id,
                    "approved": approved,
                    "feedback": feedback,
                    "scope": scope,
                },
            )
        )
        return SimpleNamespace(request_id=request_id, approved=approved)

    async def replay_display_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[DisplayEvent]:
        self.calls.append(
            (
                "replay_display_events",
                {"run_id": run_id, "last_event_id": last_event_id, "limit": limit},
            )
        )
        return list(self.display_events)

    async def list_runtime_runs(self, session_id: str) -> list[Any]:
        self.calls.append(("list_runtime_runs", session_id))
        return [
            SimpleNamespace(run_id="run-1", started_at=datetime(2026, 1, 1, tzinfo=UTC))
        ]

    async def list_sessions_async(self) -> list[str]:
        self.calls.append(("list_sessions_async", None))
        return ["sess-compat"]

    async def close_session(self, session_id: str) -> None:
        self.calls.append(("close_session", session_id))


@pytest.mark.asyncio
async def test_external_client_harness_exercises_session_lifecycle(
    tmp_path: Path,
) -> None:
    manager = HarnessManager(tmp_path)
    client = AcpCompatClient(AcpServer(manager))
    try:
        initialize = await client.request("initialize", {"protocolVersion": 1})
        assert initialize["result"]["agentCapabilities"]["mcpCapabilities"] == {
            "stdio": True,
            "http": False,
            "sse": False,
        }

        session_new = await client.request(
            "session/new",
            {
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "toolbox",
                        "command": "python",
                        "args": ["server.py"],
                        "env": [{"name": "EXPLICIT_OK", "value": "yes"}],
                    }
                ],
            },
        )
        assert session_new["result"] == {"sessionId": "sess-compat"}

        prompt_id = client.send_request(
            "session/prompt",
            {
                "sessionId": "sess-compat",
                "prompt": [{"type": "text", "text": "hello"}],
            },
        )
        update = await client.next_message()
        assert update["method"] == "session/update"
        assert update["params"]["update"]["content"] == {
            "type": "text",
            "text": "compat",
        }
        assert await client.wait_for_response(prompt_id) == {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {"stopReason": "end_turn"},
        }

        listed = await client.request("session/list", {"cwd": str(tmp_path)})
        assert listed["result"]["sessions"][0]["sessionId"] == "sess-compat"

        load_id = client.send_request(
            "session/load",
            {"sessionId": "sess-compat", "cwd": str(tmp_path), "mcpServers": []},
        )
        replay = await client.next_message()
        assert replay["method"] == "session/update"
        assert replay["params"]["update"]["content"] == {
            "type": "text",
            "text": "loaded",
        }
        assert await client.wait_for_response(load_id) == {
            "jsonrpc": "2.0",
            "id": 5,
            "result": None,
        }

        closed = await client.request("session/close", {"sessionId": "sess-compat"})
        assert closed == {"jsonrpc": "2.0", "id": 6, "result": {}}
    finally:
        await client.close()

    assert (
        "create_session",
        {
            "repo_path": tmp_path,
            "origin": {"entrypoint": "acp", "mode": "stdio"},
            "approval_policy": manager.calls[0][1]["approval_policy"],
            "provider_name": None,
            "model_name": None,
            "base_url": None,
            "max_steps": 30,
            "additional_directories": [],
            "mcp_servers": {
                "toolbox": {
                    "command": "python",
                    "args": ["server.py"],
                    "env": {"EXPLICIT_OK": "yes"},
                    "inherit_env": False,
                }
            },
        },
    ) in manager.calls
    assert ("close_session", "sess-compat") in manager.calls


@pytest.mark.asyncio
async def test_external_client_harness_handles_permission_request(
    tmp_path: Path,
) -> None:
    manager = HarnessManager(tmp_path)

    async def run_agent(session_id: str, prompt: str) -> None:
        await manager.wire.send(
            ApprovalRequest(
                session_id=session_id,
                request_id="approval-1",
                tool_call=ToolCallDelta(
                    session_id=session_id,
                    tool_name="bash_run",
                    arguments={"cmd": "pwd"},
                    call_id="tool-1",
                ),
            )
        )
        while not any(call[0] == "submit_approval_response" for call in manager.calls):
            await asyncio.sleep(0)
        await manager.wire.send(
            TurnEnd(
                session_id=session_id,
                turn_id="turn-1",
                completion_status=CompletionStatus.COMPLETED,
            )
        )

    manager.run_agent = run_agent  # type: ignore[method-assign]
    client = AcpCompatClient(AcpServer(manager))
    try:
        prompt_id = client.send_request(
            "session/prompt",
            {
                "sessionId": "sess-compat",
                "prompt": [{"type": "text", "text": "approve"}],
            },
        )
        permission = await client.next_message()
        assert permission["method"] == "session/request_permission"
        assert permission["params"]["toolCall"]["toolCallId"] == "tool-1"
        client.respond(
            permission["id"],
            {"outcome": {"outcome": "selected", "optionId": "allow-session"}},
        )

        assert await client.wait_for_response(prompt_id) == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"stopReason": "end_turn"},
        }
    finally:
        await client.close()

    assert (
        "submit_approval_response",
        {
            "session_id": "sess-compat",
            "request_id": "approval-1",
            "approved": True,
            "feedback": None,
            "scope": "session",
        },
    ) in manager.calls
