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
            return self._initialize()
        if method == "session/new":
            return await self._session_new(params)
        if method == "session/prompt":
            return await self._session_prompt(params)
        if method == "session/cancel":
            await self._session_cancel(params)
            return None
        raise JsonRpcError(-32601, f"Method not found: {method}")

    def _initialize(self) -> JSONObject:
        return {
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
        }

    async def _session_new(self, params: JSONObject) -> JSONObject:
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise JsonRpcError(-32602, "session/new params.cwd must be a string")
        repo_path = Path(cwd)
        if not repo_path.is_absolute():
            raise JsonRpcError(-32602, "session/new params.cwd must be absolute")

        session_id = await self._session_manager.create_session(
            repo_path=repo_path,
            origin={"entrypoint": "acp", "mode": "stdio"},
            approval_policy=self._approval_policy,
            provider_name=self._provider_name,
            model_name=self._model_name,
            base_url=self._base_url,
            max_steps=self._max_steps,
        )
        return {"sessionId": session_id}

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
                    await task
                    raise JsonRpcError(-32603, "Prompt turn ended without TurnEnd")

                message = message_task.result()
                if isinstance(message, TurnEnd) and not message.agent_id:
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
        await self._session_manager.cancel_session_turn(session_id)

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
