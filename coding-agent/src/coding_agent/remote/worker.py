from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

import httpx
from httpx_sse import aconnect_sse

from agentkit.observability import ObservationSink, record_span
from coding_agent.adapter import PipelineAdapter
from coding_agent.app import create_agent
from coding_agent.adapter_types import TurnOutcome
from coding_agent.ui.headless import HeadlessConsumer
from coding_agent.wire.protocol import (
    ApprovalRequest,
    ApprovalResponse,
    CompletionStatus,
    TurnEnd,
    WireMessage,
)


_CONTROL_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=30.0)


class AttachedExecutorError(RuntimeError):
    """Raised when the attached executor control-plane contract fails."""


ExternalWorkerError = AttachedExecutorError


class AttachedExecutorConsumer:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        run_id: str,
        session_id: str,
        worker_id: str,
        claim_token: str,
        approval_policy: str,
    ) -> None:
        self._client = client
        self._run_id = run_id
        self._session_id = session_id
        self._worker_id = worker_id
        self._claim_token = claim_token
        self._headless = HeadlessConsumer(auto_approve=approval_policy == "yolo")

    async def emit(self, msg: WireMessage) -> None:
        await self._headless.emit(msg)
        await self._post_event(msg)

    async def request_approval(self, req: ApprovalRequest) -> ApprovalResponse:
        await self.emit(req)
        if self._headless.auto_approve:
            return await self._headless.request_approval(req)
        response = await self._client.post(
            f"/executor/runs/{self._run_id}/approval",
            json={
                "executor_id": self._worker_id,
                "claim_token": self._claim_token,
                "request_id": req.request_id,
                "tool_name": req.tool_call.tool_name
                if req.tool_call is not None
                else req.tool,
                "arguments": req.tool_call.arguments
                if req.tool_call is not None
                else req.args,
                "timeout_seconds": req.timeout_seconds,
            },
        )
        response.raise_for_status()
        payload = response.json()
        approved = payload.get("approved")
        if not isinstance(approved, bool):
            raise AttachedExecutorError("executor approval response missing approved")
        return ApprovalResponse(
            session_id=self._session_id,
            request_id=_required_claim_str(payload, "request_id"),
            approved=approved,
            feedback=cast(str | None, payload.get("feedback")),
            scope=cast(Any, payload.get("scope", "once")),
        )

    async def _post_event(self, msg: WireMessage) -> None:
        event_name = type(msg).__name__
        payload = _json_compatible(asdict(msg) if is_dataclass(msg) else {})
        if not isinstance(payload, dict):
            raise TypeError("wire message payload must serialize to a JSON object")
        if event_name == "TurnEnd":
            payload["turn_id"] = self._run_id
        response = await self._client.post(
            f"/executor/runs/{self._run_id}/events",
            json={
                "executor_id": self._worker_id,
                "claim_token": self._claim_token,
                "events": [
                    {
                        "event_id": f"{self._run_id}:executor:{uuid.uuid4().hex}",
                        "event": event_name,
                        "data": payload,
                    }
                ],
            },
        )
        response.raise_for_status()


RemoteWorkerConsumer = AttachedExecutorConsumer


async def run_local_attached_executor_once(
    *,
    base_url: str,
    headers: dict[str, str],
    repo_path: Path,
    goal: str,
    approval_policy: str,
    provider_name: str | None,
    model_name: str | None,
    base_url_override: str | None,
    max_steps: int,
    worker_id: str,
) -> int:
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=_CONTROL_TIMEOUT,
    ) as client:
        worker_instance_id = _new_worker_instance_id(worker_id)
        session_id = await _create_attached_executor_session(
            client=client,
            repo_path=repo_path,
            approval_policy=approval_policy,
            provider_name=provider_name,
            model_name=model_name,
            base_url_override=base_url_override,
            max_steps=max_steps,
            worker_id=worker_id,
        )
        run_id = await _request_attached_executor_run(
            client=client,
            session_id=session_id,
            goal=goal,
        )
        claim = await _claim_run(
            client=client,
            session_id=session_id,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
            repo_path=repo_path,
        )
        if claim is None:
            raise AttachedExecutorError("requested run was not available to claim")
        if claim["run_id"] != run_id:
            raise AttachedExecutorError("claimed run does not match requested run")
        return await _execute_claimed_run(
            client=client,
            claim=claim,
            repo_path=repo_path,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
        )


async def run_local_worker_once(
    **kwargs: Any,
) -> int:
    """Compatibility wrapper for old local worker naming."""
    return await run_local_attached_executor_once(**kwargs)


async def run_attached_executor_loop(
    *,
    base_url: str,
    headers: dict[str, str],
    repo_path: Path,
    worker_id: str,
    once: bool,
    poll_interval_seconds: float,
) -> int:
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=_CONTROL_TIMEOUT,
    ) as client:
        worker_instance_id = _new_worker_instance_id(worker_id)
        while True:
            claim = await _claim_run(
                client=client,
                session_id=None,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
                repo_path=repo_path,
                allow_empty=True,
            )
            if claim is None:
                if once:
                    return 0
                await asyncio.sleep(poll_interval_seconds)
                continue
            status = await _execute_claimed_run(
                client=client,
                claim=claim,
                repo_path=repo_path,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
            )
            if once:
                return status


async def run_worker_loop(
    **kwargs: Any,
) -> int:
    """Compatibility wrapper for old worker naming."""
    return await run_attached_executor_loop(**kwargs)


async def _create_attached_executor_session(
    *,
    client: httpx.AsyncClient,
    repo_path: Path,
    approval_policy: str,
    provider_name: str | None,
    model_name: str | None,
    base_url_override: str | None,
    max_steps: int,
    worker_id: str,
) -> str:
    payload: dict[str, Any] = {
        "approval_policy": approval_policy,
        "max_steps": max_steps,
        "run_target": {
            "workspace": {
                "kind": "external_worker_ref",
                "ref": {
                    "kind": "local_path",
                    "display_path": str(repo_path),
                },
                "provider_instance_id": worker_id,
            },
            "executor": {
                "kind": "local_attached",
                "executor_kind": "local_cli",
                "worker_pool": "default",
            },
            "isolation": {"kind": "external_worker_policy"},
            "constraints": {},
            "annotations": {},
        },
    }
    if provider_name is not None:
        payload["provider"] = provider_name
    if model_name is not None:
        payload["model"] = model_name
    if base_url_override is not None:
        payload["base_url"] = base_url_override
    response = await client.post("/sessions", json=payload)
    response.raise_for_status()
    session_id = response.json().get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise AttachedExecutorError("create session response missing session_id")
    return session_id


async def _request_attached_executor_run(
    *,
    client: httpx.AsyncClient,
    session_id: str,
    goal: str,
) -> str:
    async with aconnect_sse(
        client,
        "POST",
        f"/sessions/{session_id}/prompt",
        json={"prompt": goal},
    ) as event_source:
        event_source.response.raise_for_status()
        async for sse in event_source.aiter_sse():
            if sse.event != "RunRequested":
                continue
            payload = json.loads(sse.data)
            run_id = payload.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise AttachedExecutorError("RunRequested payload missing run_id")
            return run_id
    raise AttachedExecutorError("prompt stream ended before RunRequested")


async def _claim_run(
    *,
    client: httpx.AsyncClient,
    session_id: str | None,
    worker_id: str,
    worker_instance_id: str,
    repo_path: Path,
    allow_empty: bool = False,
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "executor_id": worker_id,
        "executor_kind": "local_cli",
        "worker_instance_id": worker_instance_id,
        "process_id": os.getpid(),
        "capabilities": _attached_executor_capabilities(),
        "workspace_sync": _workspace_sync_metadata(repo_path),
    }
    if session_id is not None:
        payload["session_id"] = session_id
    response = await client.post(
        "/executor/runs/claim",
        json=payload,
    )
    if allow_empty and response.status_code == 404:
        return None
    response.raise_for_status()
    response_payload = response.json()
    if not isinstance(response_payload, dict):
        raise AttachedExecutorError("executor claim response must be an object")
    return response_payload


async def _execute_claimed_run(
    *,
    client: httpx.AsyncClient,
    claim: dict[str, Any],
    repo_path: Path,
    worker_id: str,
    worker_instance_id: str,
) -> int:
    run_id = _required_claim_str(claim, "run_id")
    session_id = _required_claim_str(claim, "session_id")
    claim_token = _required_claim_str(claim, "claim_token")
    prompt = _required_claim_str(claim, "prompt")
    approval_policy = _required_claim_str(claim, "approval_policy")
    max_steps = _required_claim_int(claim, "max_steps")
    consumer = AttachedExecutorConsumer(
        client=client,
        run_id=run_id,
        session_id=session_id,
        worker_id=worker_id,
        claim_token=claim_token,
        approval_policy=approval_policy,
    )
    pipeline, ctx = create_agent(
        workspace_root=repo_path,
        provider_override=cast(str | None, claim.get("provider_name")),
        model_override=cast(str | None, claim.get("model_name")),
        base_url_override=cast(str | None, claim.get("base_url")),
        max_steps_override=max_steps,
        approval_mode_override=approval_policy,
        session_id_override=session_id,
        run_id_override=run_id,
    )
    ctx.config["wire_consumer"] = consumer
    adapter = PipelineAdapter(pipeline=pipeline, ctx=ctx, consumer=consumer)
    with record_span(
        "remote.workspace.agent_phase",
        sink=_observation_sink_from_context(ctx),
        attributes={
            "session_id": session_id,
            "run_id": run_id,
            "executor_kind": "local",
            "workspace_ref_kind": "local_path",
            "remote_status": "started",
        },
    ) as span:
        status = "completed"
        result: dict[str, Any] = {}
        error: str | None = None
        run_task = asyncio.create_task(adapter.run_turn(prompt))
        heartbeat_task = asyncio.create_task(
            _heartbeat_until_complete(
                client=client,
                run_id=run_id,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
                claim_token=claim_token,
                run_task=run_task,
                repo_path=repo_path,
            )
        )
        try:
            outcome = await run_task
            if isinstance(outcome, TurnOutcome):
                result = {
                    "stop_reason": outcome.stop_reason.value,
                    "steps_taken": outcome.steps_taken,
                }
                if outcome.error is not None:
                    status = "failed"
                    error = outcome.error
        except asyncio.CancelledError:
            status = "cancelled"
            error = "cancelled"
        except Exception as exc:
            status = "failed"
            error = str(exc)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                heartbeat_error = f"heartbeat failed: {exc}"
                if status == "completed":
                    status = "failed"
                    error = heartbeat_error
                elif error is None:
                    error = heartbeat_error
                else:
                    error = f"{error}; {heartbeat_error}"
            if status != "completed":
                await consumer.emit(
                    TurnEnd(
                        session_id=session_id,
                        agent_id="",
                        turn_id=run_id,
                        completion_status=_completion_status_for_executor_status(
                            status
                        ),
                    )
                )
            span.set_attribute("remote_status", status)
            response = await client.post(
                f"/executor/runs/{run_id}/complete",
                json={
                    "executor_id": worker_id,
                    "claim_token": claim_token,
                    "status": status,
                    "result": result,
                    "error": error,
                    "tape_id": getattr(ctx.tape, "tape_id", None),
                    "tape_entries": ctx.tape.to_list(),
                },
            )
            response.raise_for_status()
        return 0 if status == "completed" else 1


def _completion_status_for_executor_status(status: str) -> CompletionStatus:
    if status == "completed":
        return CompletionStatus.COMPLETED
    if status == "cancelled":
        return CompletionStatus.BLOCKED
    return CompletionStatus.ERROR


async def _heartbeat_until_complete(
    *,
    client: httpx.AsyncClient,
    run_id: str,
    worker_id: str,
    worker_instance_id: str,
    claim_token: str,
    run_task: asyncio.Task[Any],
    repo_path: Path,
) -> None:
    while not run_task.done():
        response = await client.post(
            f"/executor/runs/{run_id}/heartbeat",
            json={
                "executor_id": worker_id,
                "worker_instance_id": worker_instance_id,
                "process_id": os.getpid(),
                "capabilities": _attached_executor_capabilities(),
                "workspace_sync": _workspace_sync_metadata(repo_path),
                "claim_token": claim_token,
                "lease_seconds": 30,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("cancel_requested") is True:
            run_task.cancel()
            return
        await asyncio.sleep(5)


def _new_worker_instance_id(worker_id: str) -> str:
    return f"{worker_id}:{uuid.uuid4().hex}"


def _attached_executor_capabilities() -> dict[str, Any]:
    return {
        "executor": "local_cli",
        "process_reconnect": "metadata_only",
        "workspace_sync": "metadata_only",
        "approval_interactions": True,
    }


def _workspace_sync_metadata(repo_path: Path) -> dict[str, Any]:
    return {
        "mode": "none",
        "workspace_ref_kind": "local_path",
        "display_path": str(repo_path),
    }


def _observation_sink_from_context(ctx: Any) -> ObservationSink | None:
    config = getattr(ctx, "config", None)
    if not isinstance(config, dict):
        return None
    sink = config.get("observation_sink")
    if sink is None:
        return None
    if not isinstance(sink, ObservationSink):
        raise TypeError("observation_sink must implement ObservationSink")
    return sink


def _required_claim_str(claim: dict[str, Any], key: str) -> str:
    value = claim.get(key)
    if not isinstance(value, str) or not value:
        raise AttachedExecutorError(f"executor claim response missing {key}")
    return value


def _required_claim_int(claim: dict[str, Any], key: str) -> int:
    value = claim.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AttachedExecutorError(f"executor claim response missing integer {key}")
    return value


def _json_compatible(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_compatible(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    return value
