"""API key management.

The plaintext key is shown exactly once, at creation — the same discipline documented in
`docs/SECURITY.md` §2 and already implemented in `core/security.generate_api_key`. This router is what
finally exposes that primitive over HTTP; before this file, a key could only be minted by hand.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import ScopeDep, SessionDep
from app.core.errors import AuthorizationError, NotFoundError
from app.core.security import generate_api_key
from app.db.models import ApiKey
from app.services.persistence import record_audit

router = APIRouter(tags=["api-keys"])


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ApiKeyCreatedResponse(BaseModel):
    id: str
    name: str
    key: str
    key_prefix: str
    created_at: str
    warning: str = "This is the only time the full key is shown. Store it now — it cannot be retrieved later."


class ApiKeySummary(BaseModel):
    id: str
    name: str
    key_prefix: str
    created_at: str
    last_used_at: str | None
    is_active: bool


def _require_user_session(scope: ScopeDep) -> None:
    # An API key must not be usable to mint or revoke OTHER API keys — that is privilege escalation
    # from one credential into unlimited more. Only an interactive (JWT) session may manage keys.
    if scope.via_api_key:
        raise AuthorizationError("API keys cannot be created or revoked using another API key.")


@router.post("/api-keys", response_model=ApiKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreateRequest, session: SessionDep, scope: ScopeDep, request: Request
) -> ApiKeyCreatedResponse:
    scope.require_admin()
    _require_user_session(scope)

    plaintext, prefix, key_hash = generate_api_key()
    row = ApiKey(org_id=scope.org_id, created_by=scope.user_id, name=body.name, key_prefix=prefix, key_hash=key_hash)
    session.add(row)
    await session.flush()

    await record_audit(
        session,
        action="api_key.created",
        org_id=scope.org_id,
        user_id=scope.user_id,
        resource_type="api_key",
        resource_id=row.id,
        ip_address=request.client.host if request.client else "",
        detail={"name": body.name},
    )

    return ApiKeyCreatedResponse(
        id=row.id, name=row.name, key=plaintext, key_prefix=prefix, created_at=row.created_at.isoformat()
    )


@router.get("/api-keys", response_model=list[ApiKeySummary])
async def list_api_keys(session: SessionDep, scope: ScopeDep) -> list[ApiKeySummary]:
    scope.require_admin()
    result = await session.execute(
        select(ApiKey).where(ApiKey.org_id == scope.org_id).order_by(ApiKey.created_at.desc())
    )
    return [
        ApiKeySummary(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            created_at=k.created_at.isoformat(),
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            is_active=k.is_active,
        )
        for k in result.scalars()
    ]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: str, session: SessionDep, scope: ScopeDep, request: Request) -> None:
    scope.require_admin()
    _require_user_session(scope)

    result = await session.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == scope.org_id)
    )
    key = result.scalar_one_or_none()
    if key is None:
        # 404, not 403: confirming another org's key exists is itself a leak (ARCHITECTURE §7).
        raise NotFoundError("API key not found.")

    key.revoked_at = datetime.now(UTC)
    await record_audit(
        session,
        action="api_key.revoked",
        org_id=scope.org_id,
        user_id=scope.user_id,
        resource_type="api_key",
        resource_id=key.id,
        ip_address=request.client.host if request.client else "",
    )
