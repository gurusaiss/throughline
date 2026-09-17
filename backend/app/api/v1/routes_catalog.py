"""Model catalogue routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import RegistryDep, ScopeDep
from app.api.v1 import mappers, schemas
from app.core.errors import NotFoundError

router = APIRouter(tags=["models"])


@router.get("/models", response_model=list[schemas.ModelResponse])
async def list_models(
    registry: RegistryDep, scope: ScopeDep, request: Request, provider: str | None = None
) -> list[schemas.ModelResponse]:
    configured = request.app.state.pool.configured_providers()
    models = registry.list_models(
        providers={provider} if provider else None, available_only=False
    )
    return [mappers.to_model_response(m, configured=m.provider in configured) for m in models]


@router.get("/models/{model_id}", response_model=schemas.ModelResponse)
async def get_model(
    model_id: str, registry: RegistryDep, scope: ScopeDep, request: Request
) -> schemas.ModelResponse:
    info = registry.find(model_id)
    if info is None:
        raise NotFoundError(f"Unknown model: {model_id}")
    configured = request.app.state.pool.configured_providers()
    return mappers.to_model_response(info, configured=info.provider in configured)


@router.get("/models-status/stale", response_model=list[schemas.ModelResponse])
async def stale_pricing(
    registry: RegistryDep, scope: ScopeDep, request: Request
) -> list[schemas.ModelResponse]:
    """Models whose pricing is unverified or past its verification window.

    Deliberately a first-class endpoint rather than a log line: RESEARCH 3.2 showed a pricing source wrong
    by 50% on a flagship model, so price rot has to be *visible* to whoever operates the platform
    (ADR-004).
    """
    configured = request.app.state.pool.configured_providers()
    return [
        mappers.to_model_response(m, configured=m.provider in configured)
        for m in registry.stale_models()
    ]
