from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Protocol, cast

from coding_agent.runtime_store import (
    AgentInteractionRecord,
    JSONObject,
    JSONValue,
)
from coding_agent.stores import RuntimeInteractionStore
from coding_agent.wire.protocol import ApprovalRequest, ApprovalResponse


class ApprovalInteractionSession(Protocol):
    id: str
    current_turn_id: str | None


def approval_interaction_id(run_id: str, request_id: str) -> str:
    return f"{run_id}:approval:{request_id}"


def approval_interaction_status(response: ApprovalResponse) -> str:
    return "approved" if response.approved else "rejected"


def approval_request_payload(request: ApprovalRequest) -> JSONObject:
    payload: JSONObject = {
        "session_id": request.session_id,
        "request_id": request.request_id,
        "timestamp": request.timestamp.isoformat(),
        "timeout_seconds": request.timeout_seconds,
    }
    if request.tool_call is not None:
        tool_call_payload = _json_compatible_value(asdict(request.tool_call))
        if not isinstance(tool_call_payload, dict):
            raise TypeError("approval tool_call payload must serialize to an object")
        payload["tool_call"] = cast(JSONValue, tool_call_payload)
    return payload


def approval_response_payload(response: ApprovalResponse) -> JSONObject:
    return {
        "session_id": response.session_id,
        "request_id": response.request_id,
        "approved": response.approved,
        "feedback": response.feedback,
        "scope": response.scope,
    }


@dataclass(frozen=True)
class ApprovalInteractionService:
    store: RuntimeInteractionStore | None
    owner_id: str | None = None
    fencing_token: int | None = None

    async def create(
        self,
        session: ApprovalInteractionSession,
        request: ApprovalRequest,
    ) -> str | None:
        if self.store is None:
            return None
        run_id = session.current_turn_id
        if run_id is None:
            return None
        interaction_id = approval_interaction_id(run_id, request.request_id)
        await self.store.create_agent_interaction(
            AgentInteractionRecord(
                interaction_id=interaction_id,
                run_id=run_id,
                interaction_kind="approval",
                status="pending",
                request_payload=approval_request_payload(request),
                response_payload={},
                metadata=self._metadata(session, request),
                created_at=request.timestamp,
            )
        )
        return interaction_id

    async def resolve(
        self,
        session: ApprovalInteractionSession,
        request_id: str,
        response: ApprovalResponse,
        *,
        status: str | None = None,
    ) -> None:
        if self.store is None:
            return
        run_id = session.current_turn_id
        if run_id is None:
            return
        if request_id != response.request_id:
            raise ValueError(
                "approval response request_id does not match "
                "interaction request_id"
            )
        await self.store.resolve_agent_interaction(
            approval_interaction_id(run_id, request_id),
            status=status or approval_interaction_status(response),
            response_payload=approval_response_payload(response),
            resolved_at=response.timestamp,
        )

    def _metadata(
        self,
        session: ApprovalInteractionSession,
        request: ApprovalRequest,
    ) -> JSONObject:
        metadata: JSONObject = {
            "session_id": session.id,
            "request_id": request.request_id,
        }
        if request.tool_call is not None:
            metadata["tool_call_id"] = request.tool_call.call_id
            metadata["tool_name"] = request.tool_call.tool_name
        if self.owner_id is not None:
            metadata["owner_id"] = self.owner_id
        if self.fencing_token is not None:
            metadata["fencing_token"] = self.fencing_token
        return metadata


def _json_compatible_value(value: object) -> JSONValue:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return cast(JSONValue, value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_compatible_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_compatible_value(item) for item in value]
    return str(value)


__all__ = [
    "ApprovalInteractionService",
    "ApprovalInteractionSession",
    "approval_interaction_id",
    "approval_interaction_status",
    "approval_request_payload",
    "approval_response_payload",
]
