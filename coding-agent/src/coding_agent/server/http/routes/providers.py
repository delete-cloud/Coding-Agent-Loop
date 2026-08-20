"""Provider model listing route."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from coding_agent.server.auth import (
    verify_api_key,
)
from coding_agent.server.provider_models import list_provider_models
from coding_agent.server.rate_limit import RateLimits, limiter
from coding_agent.server.schemas import (
    ProviderModelSchema,
    ProviderModelsResponse,
    validate_provider_value,
)

from coding_agent.server.http._bindings import LOGGER_NAME

from fastapi import APIRouter

logger = logging.getLogger(LOGGER_NAME)
router = APIRouter()


@router.get("/providers/{provider}/models", response_model=ProviderModelsResponse)
@limiter.limit(RateLimits.GET_SESSION)
async def list_provider_models_endpoint(
    request: Request,
    provider: str,
    api_key: str | None = Depends(verify_api_key),
) -> ProviderModelsResponse:
    """List available models for a provider.

    Never fails on provider-side errors (missing API key, network, unsupported
    provider): returns ``source="unavailable"`` with an empty model list so the
    client can fall back to its presets.
    """
    del request, api_key
    try:
        provider = validate_provider_value(provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        model_ids = await list_provider_models(provider)
    except Exception:
        logger.info(
            "Provider model listing unavailable provider=%s",
            provider,
            exc_info=True,
        )
        model_ids = []
    if not model_ids:
        return ProviderModelsResponse(
            provider=provider, models=[], source="unavailable"
        )
    return ProviderModelsResponse(
        provider=provider,
        models=[ProviderModelSchema(id=model_id) for model_id in model_ids],
        source="live",
    )


__all__ = [
    "list_provider_models_endpoint",
]
