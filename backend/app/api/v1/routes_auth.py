"""Authentication and account routes."""

from __future__ import annotations

import re
from datetime import UTC

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select

from app.api.deps import CurrentUserDep, ScopeDep, SessionDep, SettingsDep
from app.api.v1 import schemas
from app.core.errors import AuthenticationError, ValidationError
from app.core.security import (
    TokenPayload,
    create_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.db.models import Membership, Organization, User
from app.services.persistence import record_audit

router = APIRouter(tags=["auth"])


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "workspace"


@router.post("/auth/signup", response_model=schemas.TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: schemas.SignupRequest, session: SessionDep, settings: SettingsDep, request: Request
) -> schemas.TokenResponse:
    email = body.email.lower().strip()
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        # Deliberately the same shape as a normal validation error. Confirming which emails are
        # registered is an account-enumeration leak.
        raise ValidationError("That email address cannot be used to sign up.")

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name or email.split("@")[0],
    )
    session.add(user)

    org_name = body.organization_name or f"{user.display_name}'s workspace"
    base_slug = _slugify(org_name)
    slug = base_slug
    suffix = 1
    while (
        await session.execute(select(func.count()).select_from(Organization).where(Organization.slug == slug))
    ).scalar_one() > 0:
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    org = Organization(
        name=org_name, slug=slug, monthly_budget_usd=settings.default_org_monthly_budget_usd
    )
    session.add(org)
    await session.flush()

    session.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    await record_audit(
        session,
        action="user.signup",
        org_id=org.id,
        user_id=user.id,
        ip_address=request.client.host if request.client else "",
    )
    await session.flush()

    return _issue_tokens(user.id, org.id, "owner", settings)


@router.post("/auth/login", response_model=schemas.TokenResponse)
async def login(
    body: schemas.LoginRequest, session: SessionDep, settings: SettingsDep, request: Request
) -> schemas.TokenResponse:
    email = body.email.lower().strip()
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Same error for unknown user and wrong password, to avoid account enumeration.
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        await record_audit(
            session,
            action="auth.login_failed",
            user_id=user.id if user else None,
            ip_address=request.client.host if request.client else "",
        )
        raise AuthenticationError("Incorrect email or password.")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)

    membership = await session.execute(select(Membership).where(Membership.user_id == user.id))
    m = membership.scalars().first()
    if m is None:
        raise AuthenticationError("This account is not attached to a workspace.")

    from datetime import datetime

    user.last_login_at = datetime.now(UTC)
    await record_audit(
        session,
        action="auth.login",
        org_id=m.org_id,
        user_id=user.id,
        ip_address=request.client.host if request.client else "",
    )
    return _issue_tokens(user.id, m.org_id, m.role, settings)


@router.post("/auth/refresh", response_model=schemas.TokenResponse)
async def refresh(
    refresh_token: str, session: SessionDep, settings: SettingsDep
) -> schemas.TokenResponse:
    payload = decode_token(refresh_token, settings, expected_type="refresh")
    user = await session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is not available.")
    return _issue_tokens(payload.user_id, payload.org_id, payload.role, settings)


@router.get("/auth/me", response_model=schemas.UserResponse)
async def me(user: CurrentUserDep, scope: ScopeDep, session: SessionDep) -> schemas.UserResponse:
    org = await session.get(Organization, scope.org_id)
    return schemas.UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        org_id=scope.org_id,
        org_name=org.name if org else "",
        role=scope.role,
        memory_enabled=user.memory_enabled,
    )


def _issue_tokens(user_id: str, org_id: str, role: str, settings: SettingsDep) -> schemas.TokenResponse:
    access = create_token(TokenPayload(user_id, org_id, role, "access"), settings)
    refresh_tok = create_token(TokenPayload(user_id, org_id, role, "refresh"), settings)
    return schemas.TokenResponse(
        access_token=access,
        refresh_token=refresh_tok,
        expires_in=settings.access_token_ttl_minutes * 60,
    )
