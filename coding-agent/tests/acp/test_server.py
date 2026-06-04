from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from typing import Any

import pytest

from coding_agent.acp.server import AcpServer, run_stdio
from coding_agent.approval import ApprovalPolicy
from coding_agent.events import DisplayEvent
from coding_agent.wire.local import LocalWire
from coding_agent.wire.protocol import (
    ApprovalRequest,
    CompletionStatus,
    StreamDelta,
    ToolCallDelta,
    TurnEnd,
)


class FakeManager:
    def __init__(
        self, wire: LocalWire | None = None, repo_path: Path | None = None
    ) -> None:
        self.wire = wire or LocalWire("sess-1")
        self.repo_path = repo_path or Path("/tmp")
        self.calls: list[tuple[str, Any]] = []
        self.raise_active_turn = False
        self.runtime_runs: list[Any] = [
            SimpleNamespace(run_id="run-1", started_at=datetime(2026, 1, 1, tzinfo=UTC))
        ]
        self.display_events: list[DisplayEvent] = [
            DisplayEvent(
                source_event_id="event-1",
                run_id="run-1",
                sequence=1,
                display_kind="assistant_text_delta",
                payload={"content": "loaded answer", "role": "assistant"},
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]
        self.additional_directories: list[str] = []

    async def create_session(self, **kwargs: Any) -> str:
        self.calls.append(("create_session", kwargs))
        return "sess-1"

    async def get_session_async(self, session_id: str) -> Any:
        self.calls.append(("get_session_async", session_id))
        return SimpleNamespace(
            id=session_id,
            wire=self.wire,
            repo_path=self.repo_path,
            additional_directories=list(self.additional_directories),
            last_activity=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            default_run_target=None,
        )

    async def list_sessions_async(self) -> list[str]:
        self.calls.append(("list_sessions_async", None))
        return ["sess-1", "sess-2"]

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

    async def close_session(self, session_id: str) -> None:
        self.calls.append(("close_session", session_id))

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

    async def list_runtime_runs(self, session_id: str) -> list[Any]:
        self.calls.append(("list_runtime_runs", session_id))
        return list(self.runtime_runs)

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

    async def close(self) -> None:
        self.calls.append(("close", None))


def _expected_mode_state() -> dict[str, Any]:
    return {
        "currentModeId": "default",
        "availableModes": [
            {
                "id": "default",
                "name": "Default",
                "description": "Standard Coding Agent behavior.",
            }
        ],
    }


@pytest.mark.asyncio
async def test_initialize_returns_protocol_version_and_minimal_capabilities() -> None:
    server = AcpServer(FakeManager())

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": True,
                "mcpCapabilities": {"stdio": True, "http": False, "sse": False},
                "promptCapabilities": {},
                "sessionCapabilities": {
                    "close": {},
                    "list": {},
                    "resume": {},
                    "additionalDirectories": {},
                },
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
async def test_initialize_advertises_load_session() -> None:
    server = AcpServer(FakeManager())

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1},
        }
    )

    assert response is not None
    result = response["result"]
    assert isinstance(result, dict)
    capabilities = result["agentCapabilities"]
    assert capabilities["loadSession"] is True


@pytest.mark.asyncio
async def test_initialize_advertises_session_lifecycle_capabilities() -> None:
    server = AcpServer(FakeManager())

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1},
        }
    )

    assert response is not None
    result = response["result"]
    assert isinstance(result, dict)
    capabilities = result["agentCapabilities"]
    assert capabilities["sessionCapabilities"] == {
        "close": {},
        "list": {},
        "resume": {},
        "additionalDirectories": {},
    }


@pytest.mark.asyncio
async def test_initialize_advertises_additional_directories_capability() -> None:
    server = AcpServer(FakeManager())

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1},
        }
    )

    assert response is not None
    result = response["result"]
    assert isinstance(result, dict)
    capabilities = result["agentCapabilities"]
    assert capabilities["sessionCapabilities"]["additionalDirectories"] == {}


@pytest.mark.asyncio
async def test_initialize_rejects_missing_protocol_version() -> None:
    response = await AcpServer(FakeManager()).handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32602,
            "message": "initialize params.protocolVersion must be 1",
        },
    }


@pytest.mark.asyncio
async def test_initialize_rejects_unsupported_protocol_version() -> None:
    response = await AcpServer(FakeManager()).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 2},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {
            "code": -32602,
            "message": "initialize params.protocolVersion must be 1",
        },
    }


@pytest.mark.asyncio
async def test_initialize_advertises_stdio_mcp_capability() -> None:
    server = AcpServer(FakeManager())

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1},
        }
    )

    assert response is not None
    result = response["result"]
    assert isinstance(result, dict)
    capabilities = result["agentCapabilities"]
    assert capabilities["mcpCapabilities"] == {
        "stdio": True,
        "http": False,
        "sse": False,
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

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"sessionId": "sess-1", "modes": _expected_mode_state()},
    }
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
                "mcp_servers": {},
                "additional_directories": [],
            },
        )
    ]


@pytest.mark.asyncio
async def test_session_new_passes_stdio_mcp_servers_to_session_manager(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "filesystem",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                        "env": [{"name": "ROOT", "value": str(tmp_path)}],
                    }
                ],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"sessionId": "sess-1", "modes": _expected_mode_state()},
    }
    assert manager.calls[0] == (
        "create_session",
        {
            "repo_path": tmp_path,
            "origin": {"entrypoint": "acp", "mode": "stdio"},
            "approval_policy": ApprovalPolicy.AUTO,
            "provider_name": None,
            "model_name": None,
            "base_url": None,
            "max_steps": 30,
            "mcp_servers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    "env": {"ROOT": str(tmp_path)},
                    "inherit_env": False,
                }
            },
            "additional_directories": [],
        },
    )


@pytest.mark.asyncio
async def test_session_new_passes_additional_directories_to_session_manager(
    tmp_path: Path,
) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    manager = FakeManager()
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [],
                "additionalDirectories": [str(extra)],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"sessionId": "sess-1", "modes": _expected_mode_state()},
    }
    assert manager.calls[0][1]["additional_directories"] == [str(extra)]


@pytest.mark.asyncio
async def test_session_new_rejects_missing_mcp_servers(tmp_path: Path) -> None:
    response = await AcpServer(FakeManager()).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": str(tmp_path)},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32602,
            "message": "session params.mcpServers must be an array",
        },
    }


@pytest.mark.asyncio
async def test_session_new_rejects_relative_additional_directory(
    tmp_path: Path,
) -> None:
    response = await AcpServer(FakeManager()).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [],
                "additionalDirectories": ["relative/path"],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32602,
            "message": "session additionalDirectories[0] must be absolute",
        },
    }


@pytest.mark.asyncio
async def test_session_new_rejects_unsupported_mcp_server_type(tmp_path: Path) -> None:
    response = await AcpServer(FakeManager()).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "remote",
                        "type": "sse",
                        "url": "https://example.invalid/sse",
                        "headers": [],
                    }
                ],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32602,
            "message": "session mcpServers[0].type must be stdio",
        },
    }


@pytest.mark.asyncio
async def test_session_new_rejects_stdio_mcp_server_without_args(
    tmp_path: Path,
) -> None:
    response = await AcpServer(FakeManager()).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "filesystem",
                        "command": "npx",
                        "env": [],
                    }
                ],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32602,
            "message": "session mcpServers[0].args must be a string array",
        },
    }


@pytest.mark.asyncio
async def test_session_new_rejects_stdio_mcp_server_without_env(tmp_path: Path) -> None:
    response = await AcpServer(FakeManager()).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "filesystem",
                        "command": "npx",
                        "args": [],
                    }
                ],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32602,
            "message": "session mcpServers[0].env must be an array",
        },
    }


@pytest.mark.asyncio
async def test_session_load_updates_mcp_servers_from_params(tmp_path: Path) -> None:
    manager = FakeManager(repo_path=tmp_path)
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/load",
            "params": {
                "sessionId": "sess-1",
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "toolbox",
                        "command": "python",
                        "args": ["server.py"],
                        "env": [],
                    }
                ],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"modes": _expected_mode_state()},
    }
    assert (
        "update_session_mcp_servers",
        {
            "session_id": "sess-1",
            "mcp_servers": {
                "toolbox": {
                    "command": "python",
                    "args": ["server.py"],
                    "env": {},
                    "inherit_env": False,
                }
            },
        },
    ) in manager.calls


@pytest.mark.asyncio
async def test_session_load_updates_additional_directories_from_params(
    tmp_path: Path,
) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    manager = FakeManager(repo_path=tmp_path)
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/load",
            "params": {
                "sessionId": "sess-1",
                "cwd": str(tmp_path),
                "mcpServers": [],
                "additionalDirectories": [str(extra)],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"modes": _expected_mode_state()},
    }
    assert (
        "update_session_additional_directories",
        {"session_id": "sess-1", "additional_directories": [str(extra)]},
    ) in manager.calls


@pytest.mark.asyncio
async def test_session_resume_updates_session_params_without_replay(
    tmp_path: Path,
) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    manager = FakeManager(repo_path=tmp_path)
    notifications: list[dict[str, Any]] = []
    server = AcpServer(manager, emit=notifications.append)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/resume",
            "params": {
                "sessionId": "sess-1",
                "cwd": str(tmp_path),
                "mcpServers": [
                    {
                        "name": "toolbox",
                        "command": "python",
                        "args": ["server.py"],
                        "env": [{"name": "EXPLICIT_OK", "value": "yes"}],
                    }
                ],
                "additionalDirectories": [str(extra)],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"modes": _expected_mode_state()},
    }
    assert (
        "update_session_mcp_servers",
        {
            "session_id": "sess-1",
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
    assert (
        "update_session_additional_directories",
        {"session_id": "sess-1", "additional_directories": [str(extra)]},
    ) in manager.calls
    assert ("list_runtime_runs", "sess-1") not in manager.calls
    assert notifications == []


@pytest.mark.asyncio
async def test_session_resume_allows_omitted_mcp_servers(tmp_path: Path) -> None:
    manager = FakeManager(repo_path=tmp_path)
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/resume",
            "params": {
                "sessionId": "sess-1",
                "cwd": str(tmp_path),
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"modes": _expected_mode_state()},
    }
    assert (
        "update_session_mcp_servers",
        {"session_id": "sess-1", "mcp_servers": {}},
    ) in manager.calls
    assert (
        "update_session_additional_directories",
        {"session_id": "sess-1", "additional_directories": []},
    ) in manager.calls


@pytest.mark.asyncio
async def test_session_resume_rejects_relative_cwd(tmp_path: Path) -> None:
    response = await AcpServer(FakeManager(repo_path=tmp_path)).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/resume",
            "params": {"sessionId": "sess-1", "cwd": "relative/path"},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {
            "code": -32602,
            "message": "session/resume params.cwd must be absolute",
        },
    }


@pytest.mark.asyncio
async def test_session_load_rejects_missing_mcp_servers(tmp_path: Path) -> None:
    response = await AcpServer(FakeManager(repo_path=tmp_path)).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/load",
            "params": {"sessionId": "sess-1", "cwd": str(tmp_path)},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {
            "code": -32602,
            "message": "session params.mcpServers must be an array",
        },
    }


@pytest.mark.asyncio
async def test_session_list_returns_session_info_and_filters_by_cwd(
    tmp_path: Path,
) -> None:
    manager = FakeManager(repo_path=tmp_path)
    extra = tmp_path / "extra"
    extra.mkdir()
    manager.additional_directories = [str(extra)]
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "session/list",
            "params": {"cwd": str(tmp_path)},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 9,
        "result": {
            "sessions": [
                {
                    "sessionId": "sess-1",
                    "cwd": str(tmp_path),
                    "title": None,
                    "updatedAt": "2026-01-02T03:04:05+00:00",
                    "additionalDirectories": [str(extra)],
                },
                {
                    "sessionId": "sess-2",
                    "cwd": str(tmp_path),
                    "title": None,
                    "updatedAt": "2026-01-02T03:04:05+00:00",
                    "additionalDirectories": [str(extra)],
                },
            ],
            "nextCursor": None,
        },
    }
    assert ("list_sessions_async", None) in manager.calls


@pytest.mark.asyncio
async def test_session_close_calls_session_manager_close() -> None:
    manager = FakeManager()
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "session/close",
            "params": {"sessionId": "sess-1"},
        }
    )

    assert response == {"jsonrpc": "2.0", "id": 10, "result": {}}
    assert ("close_session", "sess-1") in manager.calls


@pytest.mark.asyncio
async def test_session_set_mode_accepts_default_mode() -> None:
    manager = FakeManager()
    server = AcpServer(manager)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "session/set_mode",
            "params": {"sessionId": "sess-1", "modeId": "default"},
        }
    )

    assert response == {"jsonrpc": "2.0", "id": 11, "result": {}}
    assert ("get_session_async", "sess-1") in manager.calls


@pytest.mark.asyncio
async def test_session_set_mode_rejects_unknown_mode() -> None:
    response = await AcpServer(FakeManager()).handle_message(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "session/set_mode",
            "params": {"sessionId": "sess-1", "modeId": "plan"},
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 11,
        "error": {
            "code": -32602,
            "message": "session/set_mode params.modeId must be default",
        },
    }


@pytest.mark.asyncio
async def test_session_load_replays_display_events_before_response(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    manager.display_events = [
        DisplayEvent(
            source_event_id="event-1",
            run_id="run-1",
            sequence=1,
            display_kind="assistant_text_delta",
            payload={"content": "first", "role": "assistant"},
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
        DisplayEvent(
            source_event_id="event-2",
            run_id="run-1",
            sequence=2,
            display_kind="tool_result",
            payload={
                "call_id": "call-1",
                "tool_name": "bash_run",
                "display_result": "ok",
                "is_error": False,
            },
            created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        ),
    ]
    notifications: list[dict[str, Any]] = []
    server = AcpServer(manager, emit=notifications.append)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "session/load",
            "params": {
                "sessionId": "sess-1",
                "cwd": str(tmp_path),
                "mcpServers": [],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {"modes": _expected_mode_state()},
    }
    assert notifications == [
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "first"},
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-1",
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-1",
                    "status": "completed",
                    "content": [
                        {
                            "type": "content",
                            "content": {"type": "text", "text": "ok"},
                        }
                    ],
                },
            },
        },
    ]
    assert manager.calls[:5] == [
        ("get_session_async", "sess-1"),
        (
            "update_session_mcp_servers",
            {"session_id": "sess-1", "mcp_servers": {}},
        ),
        (
            "update_session_additional_directories",
            {"session_id": "sess-1", "additional_directories": []},
        ),
        ("list_runtime_runs", "sess-1"),
        (
            "replay_display_events",
            {"run_id": "run-1", "last_event_id": None, "limit": 1000},
        ),
    ]


@pytest.mark.asyncio
async def test_session_load_replays_all_runs_in_started_order(tmp_path: Path) -> None:
    manager = FakeManager()
    manager.runtime_runs = [
        SimpleNamespace(
            run_id="run-2",
            started_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        ),
        SimpleNamespace(
            run_id="run-1",
            started_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        ),
    ]

    async def replay_display_events(
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[DisplayEvent]:
        manager.calls.append(
            (
                "replay_display_events",
                {"run_id": run_id, "last_event_id": last_event_id, "limit": limit},
            )
        )
        return [
            DisplayEvent(
                source_event_id=f"{run_id}-event-1",
                run_id=run_id,
                sequence=1,
                display_kind="assistant_text_delta",
                payload={"content": run_id, "role": "assistant"},
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]

    manager.replay_display_events = replay_display_events  # type: ignore[method-assign]
    notifications: list[dict[str, Any]] = []
    server = AcpServer(manager, emit=notifications.append)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "session/load",
            "params": {
                "sessionId": "sess-1",
                "cwd": str(tmp_path),
                "mcpServers": [],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 8,
        "result": {"modes": _expected_mode_state()},
    }
    assert [
        notification["params"]["update"]["content"]["text"]
        for notification in notifications
    ] == ["run-1", "run-2"]


@pytest.mark.asyncio
async def test_session_load_without_runs_returns_empty_object_without_replay(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    manager.runtime_runs = []
    notifications: list[dict[str, Any]] = []
    server = AcpServer(manager, emit=notifications.append)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "session/load",
            "params": {
                "sessionId": "sess-1",
                "cwd": str(tmp_path),
                "mcpServers": [],
            },
        }
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 8,
        "result": {"modes": _expected_mode_state()},
    }
    assert notifications == []
    assert ("replay_display_events",) not in [
        (name,) for name, _payload in manager.calls
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
async def test_permission_request_calls_client_and_submits_allow_once() -> None:
    wire = LocalWire("sess-1")
    manager = FakeManager(wire)
    client_calls: list[tuple[str, dict[str, Any]]] = []

    async def call_client(method: str, params: dict[str, Any]) -> dict[str, Any]:
        client_calls.append((method, params))
        return {"outcome": {"outcome": "selected", "optionId": "allow-once"}}

    async def run_agent(session_id: str, prompt: str) -> None:
        await wire.send(
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
        await wire.send(
            TurnEnd(
                session_id=session_id,
                turn_id="turn-1",
                completion_status=CompletionStatus.COMPLETED,
            )
        )

    manager.run_agent = run_agent  # type: ignore[method-assign]
    server = AcpServer(manager, call_client=call_client)

    response = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "session/prompt",
            "params": {
                "sessionId": "sess-1",
                "prompt": [{"type": "text", "text": "needs approval"}],
            },
        }
    )

    assert response == {"jsonrpc": "2.0", "id": 6, "result": {"stopReason": "end_turn"}}
    assert client_calls == [
        (
            "session/request_permission",
            {
                "sessionId": "sess-1",
                "toolCall": {
                    "toolCallId": "tool-1",
                    "title": "bash_run",
                    "kind": "execute",
                    "status": "pending",
                    "rawInput": {"cmd": "pwd"},
                },
                "options": [
                    {
                        "optionId": "allow-once",
                        "name": "Allow once",
                        "kind": "allow_once",
                    },
                    {
                        "optionId": "allow-session",
                        "name": "Allow for this session",
                        "kind": "allow_always",
                    },
                    {
                        "optionId": "reject-once",
                        "name": "Reject",
                        "kind": "reject_once",
                    },
                ],
            },
        )
    ]
    assert (
        "submit_approval_response",
        {
            "session_id": "sess-1",
            "request_id": "approval-1",
            "approved": True,
            "feedback": None,
            "scope": "once",
        },
    ) in manager.calls


@pytest.mark.asyncio
async def test_permission_request_submits_session_scope_for_allow_session() -> None:
    manager = FakeManager()

    async def call_client(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        return {"outcome": {"outcome": "selected", "optionId": "allow-session"}}

    server = AcpServer(manager, call_client=call_client)

    await server.handle_approval_request(
        "sess-1",
        ApprovalRequest(
            session_id="sess-1",
            request_id="approval-1",
            tool_call=ToolCallDelta(
                session_id="sess-1",
                tool_name="file_write",
                arguments={"path": "a.txt"},
                call_id="tool-1",
            ),
        ),
    )

    assert (
        "submit_approval_response",
        {
            "session_id": "sess-1",
            "request_id": "approval-1",
            "approved": True,
            "feedback": None,
            "scope": "session",
        },
    ) in manager.calls


@pytest.mark.asyncio
async def test_permission_request_submits_denial_for_cancelled_outcome() -> None:
    manager = FakeManager()

    async def call_client(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        return {"outcome": {"outcome": "cancelled"}}

    server = AcpServer(manager, call_client=call_client)

    await server.handle_approval_request(
        "sess-1",
        ApprovalRequest(
            session_id="sess-1",
            request_id="approval-1",
            tool_call=ToolCallDelta(
                session_id="sess-1",
                tool_name="bash_run",
                arguments={},
                call_id="tool-1",
            ),
        ),
    )

    assert (
        "submit_approval_response",
        {
            "session_id": "sess-1",
            "request_id": "approval-1",
            "approved": False,
            "feedback": "Permission request cancelled by ACP client",
            "scope": "once",
        },
    ) in manager.calls


@pytest.mark.asyncio
async def test_stdio_server_writes_jsonrpc_to_stdout_only() -> None:
    manager = FakeManager()
    stdout: list[str] = []
    stderr: list[str] = []
    stdin = [
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}\n',
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
async def test_stdio_session_load_writes_replay_updates_before_response(
    tmp_path: Path,
) -> None:
    manager = FakeManager()
    stdout: list[str] = []
    stdin = [
        '{"jsonrpc":"2.0","id":1,"method":"session/load","params":{"sessionId":"sess-1","cwd":"'
        + str(tmp_path)
        + '","mcpServers":[]}}\n',
    ]

    await run_stdio(AcpServer(manager), stdin=stdin, stdout=stdout.append)

    assert stdout == [
        '{"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"sess-1","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"loaded answer"}}}}\n',
        '{"jsonrpc":"2.0","id":1,"result":{"modes":{"currentModeId":"default","availableModes":[{"id":"default","name":"Default","description":"Standard Coding Agent behavior."}]}}}\n',
    ]


@pytest.mark.asyncio
async def test_stdio_session_list_and_close(tmp_path: Path) -> None:
    manager = FakeManager(repo_path=tmp_path)
    stdout: list[str] = []
    stdin = [
        '{"jsonrpc":"2.0","id":1,"method":"session/list","params":{}}\n',
        '{"jsonrpc":"2.0","id":2,"method":"session/close","params":{"sessionId":"sess-1"}}\n',
    ]

    await run_stdio(AcpServer(manager), stdin=stdin, stdout=stdout.append)

    assert stdout == [
        '{"jsonrpc":"2.0","id":1,"result":{"sessions":[{"sessionId":"sess-1","cwd":"'
        + str(tmp_path)
        + '","title":null,"updatedAt":"2026-01-02T03:04:05+00:00","additionalDirectories":[]},{"sessionId":"sess-2","cwd":"'
        + str(tmp_path)
        + '","title":null,"updatedAt":"2026-01-02T03:04:05+00:00","additionalDirectories":[]}],"nextCursor":null}}\n',
        '{"jsonrpc":"2.0","id":2,"result":{}}\n',
    ]
    assert ("close_session", "sess-1") in manager.calls


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
    assert stdout == ['{"jsonrpc":"2.0","id":1,"result":{"stopReason":"cancelled"}}\n']


@pytest.mark.asyncio
async def test_stdio_cancel_returns_cancelled_when_prompt_task_is_cancelled() -> None:
    manager = FakeManager()
    prompt_started = asyncio.Event()
    cancel_seen = asyncio.Event()

    async def run_agent(session_id: str, prompt: str) -> None:
        manager.calls.append(
            ("run_agent", {"session_id": session_id, "prompt": prompt})
        )
        prompt_started.set()
        await cancel_seen.wait()
        raise asyncio.CancelledError

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
    assert stdout == ['{"jsonrpc":"2.0","id":1,"result":{"stopReason":"cancelled"}}\n']


@pytest.mark.asyncio
async def test_stdio_processes_close_while_prompt_is_running() -> None:
    wire = LocalWire("sess-1")
    manager = FakeManager(wire)
    prompt_started = asyncio.Event()
    close_seen = asyncio.Event()

    async def run_agent(session_id: str, prompt: str) -> None:
        manager.calls.append(
            ("run_agent", {"session_id": session_id, "prompt": prompt})
        )
        prompt_started.set()
        await close_seen.wait()
        await wire.send(
            TurnEnd(
                session_id=session_id,
                turn_id="turn-1",
                completion_status=CompletionStatus.COMPLETED,
            )
        )

    async def close_session(session_id: str) -> None:
        await prompt_started.wait()
        manager.calls.append(("close_session", session_id))
        close_seen.set()

    manager.run_agent = run_agent  # type: ignore[method-assign]
    manager.close_session = close_session  # type: ignore[method-assign]
    stdin = [
        '{"jsonrpc":"2.0","id":1,"method":"session/prompt","params":{"sessionId":"sess-1","prompt":[{"type":"text","text":"wait"}]}}\n',
        '{"jsonrpc":"2.0","id":2,"method":"session/close","params":{"sessionId":"sess-1"}}\n',
    ]
    stdout: list[str] = []

    await run_stdio(AcpServer(manager), stdin=stdin, stdout=stdout.append)

    assert ("close_session", "sess-1") in manager.calls
    assert '{"jsonrpc":"2.0","id":2,"result":{}}\n' in stdout
    assert stdout[-1] == '{"jsonrpc":"2.0","id":1,"result":{"stopReason":"end_turn"}}\n'


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


@pytest.mark.asyncio
async def test_stdio_resolves_agent_originated_permission_response() -> None:
    wire = LocalWire("sess-1")
    manager = FakeManager(wire)
    stdin = BlockingLineInput()
    stdout: list[str] = []

    async def run_agent(session_id: str, prompt: str) -> None:
        await wire.send(
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
        '{"jsonrpc":"2.0","id":1,"method":"session/prompt","params":{"sessionId":"sess-1","prompt":[{"type":"text","text":"approve"}]}}\n'
    )
    while not stdout:
        await asyncio.sleep(0)
    permission_request = stdout[0]
    assert '"method":"session/request_permission"' in permission_request
    assert '"id":"coding-agent-acp-1"' in permission_request

    stdin.put(
        '{"jsonrpc":"2.0","id":"coding-agent-acp-1","result":{"outcome":{"outcome":"selected","optionId":"allow-once"}}}\n'
    )
    stdin.close()
    await asyncio.wait_for(task, timeout=1)

    assert stdout[-1] == '{"jsonrpc":"2.0","id":1,"result":{"stopReason":"end_turn"}}\n'
    assert any(
        call
        == (
            "submit_approval_response",
            {
                "session_id": "sess-1",
                "request_id": "approval-1",
                "approved": True,
                "feedback": None,
                "scope": "once",
            },
        )
        for call in manager.calls
    )


@pytest.mark.asyncio
async def test_stdio_routes_unknown_agent_response_to_stderr() -> None:
    manager = FakeManager()
    stderr: list[str] = []
    stdin = [
        '{"jsonrpc":"2.0","id":"missing","result":{}}\n',
    ]

    await run_stdio(
        AcpServer(manager), stdin=stdin, stdout=lambda _line: None, stderr=stderr.append
    )

    assert stderr == ["ACP response for unknown request id: missing\n"]
