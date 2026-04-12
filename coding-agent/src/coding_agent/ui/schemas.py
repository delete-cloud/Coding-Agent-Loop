"""Pydantic schemas for HTTP API request/response validation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PromptRequest(BaseModel):
    """Request schema for sending a prompt."""

    prompt: str = Field(..., min_length=1, max_length=10000)


class CreateSessionRequest(BaseModel):
    """Request schema for creating a session."""

    repo_path: str | None = Field(None, max_length=500)
    approval_policy: str = Field("auto", pattern="^(yolo|interactive|auto)$")


class ApproveRequest(BaseModel):
    """Request schema for approval response."""

    request_id: str = Field(..., min_length=1, max_length=100)
    approved: bool
    feedback: str | None = Field(None, max_length=1000)


class SessionResponse(BaseModel):
    """Response schema for session creation."""

    session_id: str


class ApprovalResponseSchema(BaseModel):
    """Response schema for approval endpoint."""

    status: str
    request_id: str
    decision: str


class CloseSessionResponse(BaseModel):
    """Response schema for session close."""

    status: str
    session_id: str


class HealthResponse(BaseModel):
    """Response schema for health check."""

    status: str
    sessions: int
    version: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, str]
