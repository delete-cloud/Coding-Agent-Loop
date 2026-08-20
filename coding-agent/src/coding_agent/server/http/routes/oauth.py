"""Codex OAuth device-flow and account routes."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from coding_agent.server.auth import (
    verify_api_key,
)
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    CodexOAuthAccountDeleteResponse,
    CodexOAuthAccountListResponse,
    CodexOAuthAccountResponse,
    CodexOAuthFlowListResponse,
    CodexOAuthFlowResponse,
    CodexOAuthStartRequest,
    CodexOAuthStartResponse,
)
from coding_agent.server.oauth_flows import (
    CODEX_PROVIDER_KEY_PATTERN,
    exception_error_message,
)

from coding_agent.server.http import _bindings
from coding_agent.server.http._bindings import LOGGER_NAME

from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.post("/oauth/codex/start", response_model=CodexOAuthStartResponse)
@limiter.limit(RateLimits.CREATE_SESSION)
async def start_codex_oauth_flow(
    request: Request,
    body: CodexOAuthStartRequest | None = None,
    api_key: str | None = Depends(verify_api_key),
) -> CodexOAuthStartResponse:
    """Start a codex device-code login flow (multi-account supported)."""
    del api_key
    label = body.label if body is not None else None
    try:
        flow = await _bindings.module().codex_oauth_flow_manager.start(label)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"codex device code request failed: {exception_error_message(exc)}"
            ),
        ) from exc
    logger.info("Started codex OAuth flow: %s", flow.flow_id)
    return CodexOAuthStartResponse(
        flow_id=flow.flow_id,
        verification_url=flow.device_code.verification_url,
        user_code=flow.device_code.user_code,
        expires_in=_bindings.module().codex_oauth_flow_manager.ttl_seconds,
    )


@router.get("/oauth/codex/flows", response_model=CodexOAuthFlowListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_codex_oauth_flows(
    request: Request,
    api_key: str | None = Depends(verify_api_key),
) -> CodexOAuthFlowListResponse:
    """List in-flight and recently finished codex OAuth flows."""
    del request, api_key
    return CodexOAuthFlowListResponse(
        flows=[
            CodexOAuthFlowResponse(**flow.to_dict())
            for flow in _bindings.module().codex_oauth_flow_manager.list_flows()
        ]
    )


@router.get("/oauth/codex/flows/{flow_id}", response_model=CodexOAuthFlowResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def get_codex_oauth_flow(
    request: Request,
    flow_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CodexOAuthFlowResponse:
    """Get the state of one codex OAuth flow."""
    del request, api_key
    flow = _bindings.module().codex_oauth_flow_manager.get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="OAuth flow not found")
    return CodexOAuthFlowResponse(**flow.to_dict())


@router.post(
    "/oauth/codex/flows/{flow_id}/cancel",
    response_model=CodexOAuthFlowResponse,
)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def cancel_codex_oauth_flow(
    request: Request,
    flow_id: str,
    api_key: str | None = Depends(verify_api_key),
) -> CodexOAuthFlowResponse:
    """Cancel a pending codex OAuth flow."""
    del request, api_key
    flow = _bindings.module().codex_oauth_flow_manager.cancel(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="OAuth flow not found")
    return CodexOAuthFlowResponse(**flow.to_dict())


@router.get("/oauth/accounts", response_model=CodexOAuthAccountListResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_oauth_accounts(
    request: Request,
    api_key: str | None = Depends(verify_api_key),
) -> CodexOAuthAccountListResponse:
    """List connected codex accounts (default key plus named codex:<label>)."""
    del request, api_key
    accounts = await _bindings.module().asyncio.to_thread(
        _bindings.module().codex_oauth_flow_manager.list_accounts
    )
    return CodexOAuthAccountListResponse(
        accounts=[CodexOAuthAccountResponse(**account) for account in accounts]
    )


@router.delete(
    "/oauth/accounts/{provider_key}",
    response_model=CodexOAuthAccountDeleteResponse,
)
@limiter.limit(RateLimits.CLOSE_SESSION)
async def delete_oauth_account(
    request: Request,
    provider_key: str,
    api_key: str | None = Depends(verify_api_key),
) -> CodexOAuthAccountDeleteResponse:
    """Delete a codex account's local record (no remote revoke)."""
    del request, api_key
    if CODEX_PROVIDER_KEY_PATTERN.fullmatch(provider_key) is None:
        raise HTTPException(
            status_code=400,
            detail=f"invalid codex provider key: {provider_key!r}",
        )
    deleted = await _bindings.module().asyncio.to_thread(
        _bindings.module().codex_oauth_flow_manager.delete_account, provider_key
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="OAuth account not found")
    logger.info("Deleted codex OAuth account: %s", provider_key)
    return CodexOAuthAccountDeleteResponse(status="deleted", provider=provider_key)


__all__ = [
    "cancel_codex_oauth_flow",
    "delete_oauth_account",
    "get_codex_oauth_flow",
    "list_codex_oauth_flows",
    "list_oauth_accounts",
    "start_codex_oauth_flow",
]
