from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
import json
from pathlib import Path
import sys
from typing import Any, Protocol, TextIO

from coding_agent.acp.mapper import (
    acp_stop_reason,
    approval_request_to_permission_params,
    display_event_to_session_update,
    permission_outcome_to_approval,
    prompt_blocks_to_text,
    wire_message_to_session_update,
)
from coding_agent.approval import ApprovalPolicy
from coding_agent.wire.protocol import ApprovalRequest, TurnEnd

JSONObject = dict[str, Any]
AcpEmitter = Callable[[JSONObject], None | Awaitable[None]]
AcpClientCaller = Callable[[str, JSONObject], Awaitable[JSONObject]]
LineWriter = Callable[[str], None]
ACP_DEFAULT_MODE_ID = "default"


class AcpSessionManager(Protocol):
    async def create_session(self, **kwargs: Any) -> str: ...

    async def get_session_async(self, session_id: str) -> Any: ...

    async def run_agent(self, session_id: str, prompt: str) -> None: ...

    async def cancel_session_turn(self, session_id: str) -> Any: ...

    async def submit_approval_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
        feedback: str | None = None,
        scope: str = "once",
    ) -> Any: ...

    async def replay_display_events(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        limit: int = 1000,
    ) -> list[Any]: ...

    async def list_runtime_runs(self, session_id: str) -> list[Any]: ...

    async def list_sessions_async(self) -> list[str]: ...

    async def close_session(self, session_id: str) -> None: ...

    async def update_session_mcp_servers(
        self,
        session_id: str,
        mcp_servers: dict[str, dict[str, Any]],
    ) -> None: ...

    async def update_session_additional_directories(
        self,
        session_id: str,
        additional_directories: list[str],
    ) -> None: ...


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AcpServer:
    def __init__(
        self,
        session_manager: AcpSessionManager,
        *,
        emit: AcpEmitter | None = None,
        call_client: AcpClientCaller | None = None,
        approval_policy: ApprovalPolicy = ApprovalPolicy.AUTO,
        provider_name: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        max_steps: int = 30,
    ) -> None:
        self._session_manager = session_manager
        self._emit = emit
        self._call_client = call_client
        self._approval_policy = approval_policy
        self._provider_name = provider_name
        self._model_name = model_name
        self._base_url = base_url
        self._max_steps = max_steps
        self._active_prompt_sessions: set[str] = set()
        self._cancelled_prompt_sessions: set[str] = set()

    def set_emit(self, emit: AcpEmitter) -> None:
        self._emit = emit

    def set_call_client(self, call_client: AcpClientCaller) -> None:
        self._call_client = call_client

    async def handle_message(self, message: object) -> JSONObject | None:
        request_id: object | None = None
        try:
            if not isinstance(message, dict):
                raise JsonRpcError(-32600, "Invalid Request")
            if message.get("jsonrpc") != "2.0":
                raise JsonRpcError(-32600, "Invalid Request")
            method = message.get("method")
            if not isinstance(method, str) or not method:
                raise JsonRpcError(-32600, "Invalid Request")
            request_id = message.get("id")
            params = message.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise JsonRpcError(-32602, "Invalid params")

            result = await self._dispatch(method, params)
            if "id" not in message:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except JsonRpcError as exc:
            if isinstance(message, dict) and "id" not in message:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": exc.code, "message": exc.message},
            }
        except RuntimeError as exc:
            if str(exc) == "turn already in progress":
                message_text = "Turn already in progress"
                code = -32000
            else:
                message_text = str(exc) or type(exc).__name__
                code = -32603
            if isinstance(message, dict) and "id" not in message:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message_text},
            }

    async def _dispatch(self, method: str, params: JSONObject) -> object:
        if method == "initialize":
            return self._initialize(params)
        if method == "authenticate":
            return self._authenticate(params)
        if method == "logout":
            return self._logout(params)
        if method == "session/new":
            return await self._session_new(params)
        if method == "session/load":
            await self._session_load(params)
            return {"modes": _session_mode_state()}
        if method == "session/resume":
            await self._session_resume(params)
            return {"modes": _session_mode_state()}
        if method == "session/list":
            return await self._session_list(params)
        if method == "session/close":
            await self._session_close(params)
            return {}
        if method == "session/set_mode":
            await self._session_set_mode(params)
            return {}
        if method == "session/set_config_option":
            return await self._session_set_config_option(params)
        if method == "session/prompt":
            return await self._session_prompt(params)
        if method == "session/cancel":
            await self._session_cancel(params)
            return None
        raise JsonRpcError(-32601, f"Method not found: {method}")

    def _initialize(self, params: JSONObject) -> JSONObject:
        if params.get("protocolVersion") != 1:
            raise JsonRpcError(
                -32602,
                "initialize params.protocolVersion must be 1",
            )
        return {
            "protocolVersion": 1,
            "agentCapabilities": {
                "loadSession": True,
                "mcpCapabilities": {"http": False, "sse": False},
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
        }

    def _authenticate(self, params: JSONObject) -> JSONObject:
        method_id = params.get("methodId")
        if not isinstance(method_id, str) or not method_id:
            raise JsonRpcError(
                -32602,
                "authenticate params.methodId must be a string",
            )
        raise JsonRpcError(-32602, "authenticate params.methodId is not supported")

    def _logout(self, params: JSONObject) -> JSONObject:
        raise JsonRpcError(
            -32602,
            "logout is not supported because authentication is disabled",
        )

    async def _session_new(self, params: JSONObject) -> JSONObject:
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise JsonRpcError(-32602, "session/new params.cwd must be a string")
        repo_path = Path(cwd)
        if not repo_path.is_absolute():
            raise JsonRpcError(-32602, "session/new params.cwd must be absolute")
        mcp_servers = _parse_mcp_servers(params, method="session")
        additional_directories = _parse_additional_directories(
            params,
            method="session",
        )

        session_id = await self._session_manager.create_session(
            repo_path=repo_path,
            origin={"entrypoint": "acp", "mode": "stdio"},
            approval_policy=self._approval_policy,
            provider_name=self._provider_name,
            model_name=self._model_name,
            base_url=self._base_url,
            max_steps=self._max_steps,
            mcp_servers=mcp_servers,
            additional_directories=additional_directories,
        )
        return {"sessionId": session_id, "modes": _session_mode_state()}

    async def _session_list(self, params: JSONObject) -> JSONObject:
        cursor = params.get("cursor")
        if cursor is not None:
            raise JsonRpcError(-32602, "session/list cursor is not supported")
        cwd_filter = params.get("cwd")
        if cwd_filter is not None:
            if not isinstance(cwd_filter, str) or not cwd_filter:
                raise JsonRpcError(-32602, "session/list params.cwd must be a string")
            cwd_path = Path(cwd_filter)
            if not cwd_path.is_absolute():
                raise JsonRpcError(-32602, "session/list params.cwd must be absolute")
            cwd_filter = str(cwd_path)

        sessions: list[JSONObject] = []
        for session_id in await self._session_manager.list_sessions_async():
            try:
                session = await self._session_manager.get_session_async(session_id)
            except KeyError:
                continue
            cwd = _session_cwd(session)
            if cwd is None:
                continue
            if cwd_filter is not None and cwd != cwd_filter:
                continue
            updated_at = getattr(session, "last_activity", None)
            sessions.append(
                {
                    "sessionId": session_id,
                    "cwd": cwd,
                    "title": None,
                    "updatedAt": _session_updated_at(updated_at),
                    "additionalDirectories": _session_additional_directories(session),
                }
            )
        return {"sessions": sessions, "nextCursor": None}

    async def _session_close(self, params: JSONObject) -> None:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(
                -32602, "session/close params.sessionId must be a string"
            )
        try:
            await self._session_manager.close_session(session_id)
        except KeyError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc

    async def _session_load(self, params: JSONObject) -> None:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(-32602, "session/load params.sessionId must be a string")
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise JsonRpcError(-32602, "session/load params.cwd must be a string")
        repo_path = Path(cwd)
        if not repo_path.is_absolute():
            raise JsonRpcError(-32602, "session/load params.cwd must be absolute")
        mcp_servers = _parse_mcp_servers(params, method="session")
        additional_directories = _parse_additional_directories(
            params,
            method="session",
        )

        await self._session_manager.get_session_async(session_id)
        await self._session_manager.update_session_mcp_servers(
            session_id,
            mcp_servers,
        )
        await self._session_manager.update_session_additional_directories(
            session_id,
            additional_directories,
        )
        runs = await self._session_manager.list_runtime_runs(session_id)
        for run in _sort_runtime_runs(runs):
            run_id = getattr(run, "run_id", None)
            if not isinstance(run_id, str) or not run_id:
                continue
            display_events = await self._session_manager.replay_display_events(
                run_id,
                limit=1000,
            )
            for event in display_events:
                update = display_event_to_session_update(event)
                if update is None:
                    continue
                await self._emit_notification(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": update,
                        },
                    }
                )

    async def _session_resume(self, params: JSONObject) -> None:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(
                -32602, "session/resume params.sessionId must be a string"
            )
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise JsonRpcError(-32602, "session/resume params.cwd must be a string")
        repo_path = Path(cwd)
        if not repo_path.is_absolute():
            raise JsonRpcError(-32602, "session/resume params.cwd must be absolute")
        mcp_servers = _parse_mcp_servers(
            params,
            method="session/resume",
            required=False,
        )
        additional_directories = _parse_additional_directories(
            params,
            method="session/resume",
        )

        await self._session_manager.get_session_async(session_id)
        await self._session_manager.update_session_mcp_servers(
            session_id,
            mcp_servers,
        )
        await self._session_manager.update_session_additional_directories(
            session_id,
            additional_directories,
        )

    async def _session_set_mode(self, params: JSONObject) -> None:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(
                -32602, "session/set_mode params.sessionId must be a string"
            )
        mode_id = params.get("modeId")
        if mode_id != ACP_DEFAULT_MODE_ID:
            raise JsonRpcError(
                -32602,
                f"session/set_mode params.modeId must be {ACP_DEFAULT_MODE_ID}",
            )
        await self._session_manager.get_session_async(session_id)

    async def _session_set_config_option(self, params: JSONObject) -> JSONObject:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(
                -32602,
                "session/set_config_option params.sessionId must be a string",
            )
        config_id = params.get("configId")
        if not isinstance(config_id, str) or not config_id:
            raise JsonRpcError(
                -32602,
                "session/set_config_option params.configId must be a string",
            )
        value = params.get("value")
        if not isinstance(value, str):
            raise JsonRpcError(
                -32602,
                "session/set_config_option params.value must be a string",
            )

        await self._session_manager.get_session_async(session_id)
        raise JsonRpcError(
            -32602,
            "session/set_config_option params.configId is not supported",
        )

    async def _session_prompt(self, params: JSONObject) -> JSONObject:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(
                -32602, "session/prompt params.sessionId must be a string"
            )
        try:
            prompt = prompt_blocks_to_text(params.get("prompt"))
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc

        session = await self._session_manager.get_session_async(session_id)
        self._cancelled_prompt_sessions.discard(session_id)
        self._active_prompt_sessions.add(session_id)
        task = asyncio.create_task(self._session_manager.run_agent(session_id, prompt))
        try:
            while True:
                message_task = asyncio.create_task(session.wire.get_next_outgoing())
                done, pending = await asyncio.wait(
                    {message_task, task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if task in done and message_task in pending:
                    message_task.cancel()
                    try:
                        await message_task
                    except asyncio.CancelledError:
                        pass
                    if self._consume_prompt_cancel(session_id):
                        await _await_cancelled_prompt_task(task)
                        return {"stopReason": "cancelled"}
                    await task
                    raise JsonRpcError(-32603, "Prompt turn ended without TurnEnd")

                message = message_task.result()
                if isinstance(message, TurnEnd) and not message.agent_id:
                    if self._consume_prompt_cancel(session_id):
                        await _await_cancelled_prompt_task(task)
                        return {"stopReason": "cancelled"}
                    await task
                    return {"stopReason": acp_stop_reason(message)}

                if isinstance(message, ApprovalRequest):
                    await self.handle_approval_request(session_id, message)
                    continue

                update = wire_message_to_session_update(message)
                if update is not None:
                    await self._emit_notification(
                        {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": session_id,
                                "update": update,
                            },
                        }
                    )

                for pending_task in pending:
                    if pending_task is not task:
                        pending_task.cancel()
        finally:
            self._active_prompt_sessions.discard(session_id)
            self._cancelled_prompt_sessions.discard(session_id)
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _session_cancel(self, params: JSONObject) -> None:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(
                -32602, "session/cancel params.sessionId must be a string"
            )
        if session_id in self._active_prompt_sessions:
            self._cancelled_prompt_sessions.add(session_id)
        await self._session_manager.cancel_session_turn(session_id)

    def _consume_prompt_cancel(self, session_id: str) -> bool:
        if session_id not in self._cancelled_prompt_sessions:
            return False
        self._cancelled_prompt_sessions.discard(session_id)
        return True

    async def handle_approval_request(
        self,
        session_id: str,
        request: ApprovalRequest,
    ) -> None:
        if self._call_client is None:
            raise JsonRpcError(-32603, "ACP client call handler is not configured")

        permission_result = await self._call_client(
            "session/request_permission",
            approval_request_to_permission_params(session_id, request),
        )
        try:
            approved, feedback, scope = permission_outcome_to_approval(
                permission_result
            )
        except ValueError as exc:
            raise JsonRpcError(-32602, str(exc)) from exc

        submitted = await self._session_manager.submit_approval_response(
            session_id=session_id,
            request_id=request.request_id,
            approved=approved,
            feedback=feedback,
            scope=scope,
        )
        if submitted is None:
            raise JsonRpcError(
                -32603,
                f"Approval request not found: {request.request_id}",
            )

    async def _emit_notification(self, message: JSONObject) -> None:
        if self._emit is None:
            return
        result = self._emit(message)
        if isinstance(result, Awaitable):
            await result


async def _await_cancelled_prompt_task(task: asyncio.Task[object]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:
        # ACP cancellation is the boundary: report stopReason=cancelled even when
        # the interrupted runtime surfaces an implementation-specific exception.
        return


async def run_stdio(
    server: AcpServer,
    *,
    stdin: Iterable[str] | TextIO | None = None,
    stdout: LineWriter | None = None,
    stderr: LineWriter | None = None,
) -> None:
    input_lines = sys.stdin if stdin is None else stdin
    write_stdout = _stream_writer(sys.stdout) if stdout is None else stdout
    write_stderr = _stream_writer(sys.stderr) if stderr is None else stderr
    write_lock = asyncio.Lock()
    pending: set[asyncio.Task[None]] = set()
    pending_client_responses: dict[object, asyncio.Future[JSONObject]] = {}
    next_client_request_id = 1

    async def write_message(message: JSONObject) -> None:
        line = json.dumps(message, separators=(",", ":")) + "\n"
        async with write_lock:
            write_stdout(line)

    async def emit(message: JSONObject) -> None:
        await write_message(message)

    async def call_client(method: str, params: JSONObject) -> JSONObject:
        nonlocal next_client_request_id
        request_id = f"coding-agent-acp-{next_client_request_id}"
        next_client_request_id += 1
        loop = asyncio.get_running_loop()
        response_future: asyncio.Future[JSONObject] = loop.create_future()
        pending_client_responses[request_id] = response_future
        await write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        try:
            return await response_future
        finally:
            pending_client_responses.pop(request_id, None)

    server.set_emit(emit)
    server.set_call_client(call_client)

    async def process_line(raw_line: str) -> None:
        line = raw_line.rstrip("\n")
        if not line:
            return
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            await write_message(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            )
            write_stderr(f"ACP parse error: {exc}\n")
            return
        if _is_jsonrpc_response(request):
            request_id = request.get("id")
            response_future = pending_client_responses.pop(request_id, None)
            if response_future is None:
                write_stderr(f"ACP response for unknown request id: {request_id}\n")
                return
            if "error" in request:
                error = request["error"]
                message = (
                    error.get("message")
                    if isinstance(error, dict)
                    else "ACP client request failed"
                )
                response_future.set_exception(RuntimeError(str(message)))
                return
            result = request.get("result")
            if not isinstance(result, dict):
                response_future.set_exception(
                    RuntimeError("ACP client response result must be an object")
                )
                return
            response_future.set_result(result)
            return
        response = await server.handle_message(request)
        if response is not None:
            await write_message(response)

    async for raw_line in _iter_stdin_lines(input_lines):
        task = asyncio.create_task(process_line(raw_line))
        pending.add(task)
        task.add_done_callback(pending.discard)

    if pending:
        await asyncio.gather(*pending)


def _stream_writer(stream: TextIO) -> LineWriter:
    def write(line: str) -> None:
        stream.write(line)
        stream.flush()

    return write


async def _iter_stdin_lines(input_lines: Iterable[str] | TextIO) -> AsyncIterator[str]:
    readline = getattr(input_lines, "readline", None)
    if callable(readline):
        while True:
            line = await asyncio.to_thread(readline)
            if line == "":
                break
            yield line
        return

    for line in input_lines:
        yield line


def _is_jsonrpc_response(message: object) -> bool:
    if not isinstance(message, dict):
        return False
    return "method" not in message and ("result" in message or "error" in message)


def _sort_runtime_runs(runs: Iterable[Any]) -> list[Any]:
    return sorted(
        runs,
        key=lambda run: (
            _sortable_started_at(getattr(run, "started_at", None)),
            getattr(run, "run_id", ""),
        ),
    )


def _sortable_started_at(value: object) -> str:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return ""


def _session_cwd(session: object) -> str | None:
    repo_path = getattr(session, "repo_path", None)
    if isinstance(repo_path, Path):
        return str(repo_path)
    target = getattr(session, "default_run_target", None)
    workspace = getattr(target, "workspace", None)
    path = getattr(workspace, "path", None)
    if isinstance(path, str) and path:
        return path
    return None


def _session_updated_at(value: object) -> str | None:
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return None


def _session_additional_directories(session: object) -> list[str]:
    value = getattr(session, "additional_directories", [])
    if not isinstance(value, list) or not all(
        isinstance(directory, str) for directory in value
    ):
        raise JsonRpcError(-32603, "Session has invalid additional_directories")
    return list(value)


def _session_mode_state() -> JSONObject:
    return {
        "currentModeId": ACP_DEFAULT_MODE_ID,
        "availableModes": [
            {
                "id": ACP_DEFAULT_MODE_ID,
                "name": "Default",
                "description": "Standard Coding Agent behavior.",
            }
        ],
    }


def _parse_mcp_servers(
    params: JSONObject,
    *,
    method: str,
    required: bool = True,
) -> dict[str, dict[str, Any]]:
    raw_servers = params.get("mcpServers")
    if raw_servers is None and not required:
        return {}
    if not isinstance(raw_servers, list):
        raise JsonRpcError(-32602, f"{method} params.mcpServers must be an array")

    servers: dict[str, dict[str, Any]] = {}
    for index, raw_server in enumerate(raw_servers):
        if not isinstance(raw_server, dict):
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}] must be an object",
            )
        name = raw_server.get("name")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].name must be a string",
            )
        if name in servers:
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].name must be unique",
            )
        server_type = raw_server.get("type")
        if server_type is not None:
            if server_type != "stdio":
                raise JsonRpcError(
                    -32602,
                    f"{method} mcpServers[{index}].type must be stdio",
                )
        transport = raw_server.get("transport")
        if transport is not None and transport != "stdio":
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].transport must be stdio",
            )
        command = raw_server.get("command")
        if not isinstance(command, str) or not command:
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].command must be a string",
            )
        args_raw = raw_server.get("args")
        if not isinstance(args_raw, list) or not all(
            isinstance(arg, str) for arg in args_raw
        ):
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].args must be a string array",
            )
        servers[name] = {
            "command": command,
            "args": list(args_raw),
            "env": _parse_mcp_env(raw_server.get("env"), method=method, index=index),
            "inherit_env": False,
        }
    return servers


def _parse_additional_directories(
    params: JSONObject,
    *,
    method: str,
) -> list[str]:
    raw_directories = params.get("additionalDirectories", [])
    if not isinstance(raw_directories, list):
        raise JsonRpcError(
            -32602,
            f"{method} params.additionalDirectories must be an array",
        )

    directories: list[str] = []
    for index, raw_directory in enumerate(raw_directories):
        if not isinstance(raw_directory, str) or not raw_directory:
            raise JsonRpcError(
                -32602,
                f"{method} additionalDirectories[{index}] must be a string",
            )
        directory = Path(raw_directory).expanduser()
        if not directory.is_absolute():
            raise JsonRpcError(
                -32602,
                f"{method} additionalDirectories[{index}] must be absolute",
            )
        directories.append(str(directory.resolve()))
    return directories


def _parse_mcp_env(
    raw_env: object,
    *,
    method: str,
    index: int,
) -> dict[str, str]:
    if isinstance(raw_env, dict):
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw_env.items()
        ):
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].env must contain string values",
            )
        return dict(raw_env)
    if not isinstance(raw_env, list):
        raise JsonRpcError(
            -32602,
            f"{method} mcpServers[{index}].env must be an array",
        )

    env: dict[str, str] = {}
    for env_index, raw_var in enumerate(raw_env):
        if not isinstance(raw_var, dict):
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].env[{env_index}] must be an object",
            )
        name = raw_var.get("name")
        value = raw_var.get("value")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].env[{env_index}].name must be a string",
            )
        if not isinstance(value, str):
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].env[{env_index}].value must be a string",
            )
        if name in env:
            raise JsonRpcError(
                -32602,
                f"{method} mcpServers[{index}].env[{env_index}].name must be unique",
            )
        env[name] = value
    return env
