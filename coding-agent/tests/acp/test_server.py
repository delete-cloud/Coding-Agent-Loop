from __future__ import annotations

import asyncio
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any

import pytest

from coding_agent.acp.server import AcpServer, run_stdio
from coding_agent.approval import ApprovalPolicy
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import CompletionStatus, StreamDelta, TurnEnd


class FakeManager:
    def __init__(self, wire: LocalWire | None = None) -> None:
        self.wire = wire or LocalWire("sess-1")
        self.calls: list[tuple[str, Any]] = []
        self.raise_active_turn = False

    async def create_session(self, **kwargs: Any) -> str:
        self.calls.append(("create_session", kwargs))
        return "sess-1"

    async def get_session_async(self, session_id: str) -> Any:
        self.calls.append(("get_session_async", session_id))
        return SimpleNamespace(wire=self.wire)

    async def run_agent(self, session_id: str, prompt: str) -> None:
        self.calls.append(("run_agent", {"session_id": session_id, "prompt": prompt}))
        if self.raise_active_turn:
            raise RuntimeError("turn already in progress")
        await self.wire.send(StreamDelta(session_id=session_id, content="answer"))
        await self.wire.send(
            TurnEnd(
                session_id=session_id,
                turn_id="turn-1",
                completion_status=CompletionStatus.COMPLETED,
            )
        )

    async def cancel_session_turn(self, session_id: str) -> Any:
        self.calls.append(("cancel_session_turn", session_id))
        return SimpleNamespace(
            session_id=session_id, turn_id="turn-1", status="cancelling"
        )

    async def close(self) -> None:
        self.calls.append(("close", None))


@pytest.mark.asyncio
async def test_initialize_returns_protocol_version_and_minimal_capabilities() -> None:
    server = AcpServer(FakeManager())

    response = await server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": 1,
            "agentCapabilities": {
                "promptCapabilities": {},
                "sessionCapabilities": {},
            },
            "agentInfo": {
                "name": "coding-agent",
                "title": "Coding Agent",
                "version": "0.1.0",
            },
            "authMethods": [],
        },
    }


@pytest.mark.asyncio
async def test_session_new_creates_local_session_from_absolute_cwd(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    server = AcpServer(
        manager,
        approval_policy=ApprovalPolicy.YOLO,
        provider_name="codex",
        model_name="gpt-5.5",
        base_url="https://example.invalid/v1",
        max_steps=7,
    )

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": str(tmp_path), "mcpServers": []},
        }
    )

    assert response == {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "sess-1"}}
    assert manager.calls == [
        (
            "create_session",
            {
                "repo_path": tmp_path,
                "origin": {"entrypoint": "acp", "mode": "stdio"},
                "approval_policy": ApprovalPolicy.YOLO,
                "provider_name": "codex",
                "model_name": "gpt-5.5",
                "base_url": "https://example.invalid/v1",
                "max_steps": 7,
            },
        )
    ]


@pytest.mark.asyncio
async def test_session_prompt_streams_agent_message_chunk_and_returns_end_turn() -> (
    None
):
    manager = FakeManager()
    notifications: list[dict[str, Any]] = []
    server = AcpServer(manager, emit=notifications.append)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {
                "sessionId": "sess-1",
                "prompt": [{"type": "text", "text": "hello"}],
            },
        }
    )

    assert notifications == [
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "answer"},
                },
            },
        }
    ]
    assert response == {"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}


@pytest.mark.asyncio
async def test_session_prompt_rejects_active_turn() -> None:
    manager = FakeManager()
    manager.raise_active_turn = True
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "session/prompt",
            "params": {
                "sessionId": "sess-1",
                "prompt": [{"type": "text", "text": "hello"}],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32000, "message": "Turn already in progress"},
    }


@pytest.mark.asyncio
async def test_session_cancel_calls_session_manager_cancel() -> None:
    manager = FakeManager()
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "session/cancel",
            "params": {"sessionId": "sess-1"},
        }
    )

    assert response == {"jsonrpc": "2.0", "id": 5, "result": None}
    assert ("cancel_session_turn", "sess-1") in manager.calls


@pytest.mark.asyncio
async def test_stdio_server_writes_jsonrpc_to_stdout_only() -> None:
    manager = FakeManager()
    stdout: list[str] = []
    stderr: list[str] = []
    stdin = [
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
        '{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"/tmp","mcpServers":[]}}\n',
    ]

    await run_stdio(
        AcpServer(manager),
        stdin=stdin,
        stdout=stdout.append,
        stderr=stderr.append,
    )

    assert len(stdout) == 2
    assert all(line.startswith('{"jsonrpc":"2.0"') for line in stdout)
    assert all(line.endswith("\n") for line in stdout)
    assert stderr == []


@pytest.mark.asyncio
async def test_stdio_prompt_writes_update_before_prompt_response() -> None:
    manager = FakeManager()
    stdout: list[str] = []
    stdin = [
        '{"jsonrpc":"2.0","id":1,"method":"session/prompt","params":{"sessionId":"sess-1","prompt":[{"type":"text","text":"hello"}]}}\n',
    ]

    await run_stdio(AcpServer(manager), stdin=stdin, stdout=stdout.append)

    assert stdout == [
        '{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"sess-1","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"answer"}}}}\n',
        '{"jsonrpc":"2.0","id":1,"result":{"stopReason":"end_turn"}}\n',
    ]


@pytest.mark.asyncio
async def test_stdio_processes_cancel_while_prompt_is_running() -> None:
    wire = LocalWire("sess-1")
    manager = FakeManager(wire)
    prompt_started = asyncio.Event()
    cancel_seen = asyncio.Event()

    async def run_agent(session_id: str, prompt: str) -> None:
        manager.calls.append(
            ("run_agent", {"session_id": session_id, "prompt": prompt})
        )
        prompt_started.set()
        await cancel_seen.wait()
        await wire.send(
            TurnEnd(
                session_id=session_id,
                turn_id="turn-1",
                completion_status=CompletionStatus.COMPLETED,
            )
        )

    async def cancel_session_turn(session_id: str) -> Any:
        await prompt_started.wait()
        manager.calls.append(("cancel_session_turn", session_id))
        cancel_seen.set()
        return SimpleNamespace(
            session_id=session_id, turn_id="turn-1", status="cancelling"
        )

    manager.run_agent = run_agent  # type: ignore[method-assign]
    manager.cancel_session_turn = cancel_session_turn  # type: ignore[method-assign]
    stdin = [
        '{"jsonrpc":"2.0","id":1,"method":"session/prompt","params":{"sessionId":"sess-1","prompt":[{"type":"text","text":"wait"}]}}\n',
        '{"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"sess-1"}}\n',
    ]
    stdout: list[str] = []

    await run_stdio(AcpServer(manager), stdin=stdin, stdout=stdout.append)

    assert ("cancel_session_turn", "sess-1") in manager.calls
    assert stdout == ['{"jsonrpc":"2.0","id":1,"result":{"stopReason":"end_turn"}}\n']


@pytest.mark.asyncio
async def test_stdio_text_stream_reading_does_not_block_prompt_task() -> None:
    wire = LocalWire("sess-1")
    manager = FakeManager(wire)
    prompt_started = asyncio.Event()
    finish_prompt = asyncio.Event()
    stdin = BlockingLineInput()
    stdout: list[str] = []

    async def run_agent(session_id: str, prompt: str) -> None:
        manager.calls.append(
            ("run_agent", {"session_id": session_id, "prompt": prompt})
        )
        prompt_started.set()
        await finish_prompt.wait()
        await wire.send(
            TurnEnd(
                session_id=session_id,
                turn_id="turn-1",
                completion_status=CompletionStatus.COMPLETED,
            )
        )

    manager.run_agent = run_agent  # type: ignore[method-assign]
    task = asyncio.create_task(
        run_stdio(AcpServer(manager), stdin=stdin, stdout=stdout.append)
    )

    stdin.put(
        '{"jsonrpc":"2.0","id":1,"method":"session/prompt","params":{"sessionId":"sess-1","prompt":[{"type":"text","text":"wait"}]}}\n'
    )
    await asyncio.wait_for(prompt_started.wait(), timeout=1)
    finish_prompt.set()
    stdin.close()
    await asyncio.wait_for(task, timeout=1)

    assert stdout == ['{"jsonrpc":"2.0","id":1,"result":{"stopReason":"end_turn"}}\n']


class BlockingLineInput:
    def __init__(self) -> None:
        self._lines: Queue[str] = Queue()

    def readline(self) -> str:
        return self._lines.get()

    def put(self, line: str) -> None:
        self._lines.put(line)

    def close(self) -> None:
        self._lines.put("")
