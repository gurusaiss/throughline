"""FastAPI dependencies: authentication, tenant scoping and shared services.

Authentication accepts either a bearer JWT (browser sessions) or an `X-API-Key` header (programmatic).
Both resolve to the same `TenantScope`, so no route needs to know which was used — a route that forgets to
handle API keys is impossible by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AuthenticationError
from app.core.security import TenantScope, decode_token, hash_api_key
from app.db.models import ApiKey, Membership, Organization, User
from app.db.session import get_session
from app.metering.cost import Budget
from app.pipeline.orchestrator import Orchestrator
from app.registry.registry import ModelRegistry

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_registry_dep(request: Request) -> ModelRegistry:
    return request.app.state.registry


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


RegistryDep = Annotated[ModelRegistry, Depends(get_registry_dep)]
OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]


async def get_scope(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> TenantScope:
    """Resolve the caller to a tenant scope, or reject."""
    if x_api_key:
        return await _scope_from_api_key(session, x_api_key)
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        payload = decode_token(token, settings)
        return TenantScope(
            org_id=payload.org_id, user_id=payload.user_id, role=payload.role, via_api_key=False
        )
    raise AuthenticationError("Authentication required.")


async def _scope_from_api_key(session: AsyncSession, raw_key: str) -> TenantScope:
    key_hash = hash_api_key(raw_key)
    result = await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()
    # Same message whether the key is unknown or revoked: distinguishing them tells an attacker their
    # stolen key was real.
    if api_key is None or not api_key.is_active:
        raise AuthenticationError("Invalid API key.")

    api_key.last_used_at = datetime.now(UTC)
    return TenantScope(org_id=api_key.org_id, user_id=api_key.created_by, role="member", via_api_key=True)


ScopeDep = Annotated[TenantScope, Depends(get_scope)]


async def get_current_user(session: SessionDep, scope: ScopeDep) -> User:
    if scope.user_id is None:
        raise AuthenticationError("This endpoint requires a user session, not an API key.")
    user = await session.get(User, scope.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User account is not available.")
    return user


CurrentUserDep = Annotated[User, Depends(get_current_user)]


async def get_organization(session: SessionDep, scope: ScopeDep) -> Organization:
    org = await session.get(Organization, scope.org_id)
    if org is None:
        raise AuthenticationError("Organization not found.")
    return org


OrgDep = Annotated[Organization, Depends(get_organization)]


async def get_budget(org: OrgDep, settings: SettingsDep) -> Budget:
    """Current spend against the org's monthly limit, enforced pre-flight (ADR-011)."""
    return Budget(
        limit_usd=org.monthly_budget_usd,
        spent_usd=org.current_spend_usd,
        safety_margin=settings.budget_safety_margin,
    )


BudgetDep = Annotated[Budget, Depends(get_budget)]


async def resolve_role(session: AsyncSession, org_id: str, user_id: str) -> str:
    result = await session.execute(
        select(Membership.role).where(Membership.org_id == org_id, Membership.user_id == user_id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise AuthenticationError("You are not a member of this organization.")
    return role
