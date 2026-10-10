"""Organization administration: members, org settings, audit log, provider accounts.

Every route here requires `scope.require_admin()` — this is the admin surface referenced throughout
`docs/SECURITY.md` and `docs/ROADMAP.md` as "the next concrete task." It is not a stub: member management
actually enforces the last-owner guard, provider accounts are actually encrypted, and privacy-significant
settings changes are actually audit-logged with before/after values.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import OrgDep, ScopeDep, SessionDep, SettingsDep
from app.core.errors import NotFoundError, ValidationError
from app.core.security import CredentialCipher
from app.db.models import AuditLog, Membership, ProviderAccount, User
from app.services.persistence import record_audit

router = APIRouter(tags=["admin"])

VALID_ROLES = {"owner", "admin", "member", "viewer"}


# ======================================================================================
# Members
# ======================================================================================
class MemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str
    joined_at: str


class AddMemberRequest(BaseModel):
    email: str
    role: str = "member"


class RoleUpdateRequest(BaseModel):
    role: str


@router.get("/admin/members", response_model=list[MemberResponse])
async def list_members(session: SessionDep, scope: ScopeDep) -> list[MemberResponse]:
    scope.require_admin()
    result = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == scope.org_id)
        .order_by(Membership.created_at.asc())
    )
    return [
        MemberResponse(
            user_id=u.id, email=u.email, display_name=u.display_name, role=m.role,
            joined_at=m.created_at.isoformat(),
        )
        for m, u in result.all()
    ]


@router.post("/admin/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def add_member(
    body: AddMemberRequest, session: SessionDep, scope: ScopeDep, request: Request
) -> MemberResponse:
    scope.require_admin()
    if body.role not in VALID_ROLES:
        raise ValidationError(f"role must be one of {sorted(VALID_ROLES)}")

    email = body.email.lower().strip()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        # There is no email-invitation system in this build (that needs a configured email service —
        # see MANUAL_STEPS.md). Rather than pretend one exists, the admin is told plainly what to do.
        raise ValidationError(
            f"No account exists for {email}. Ask them to sign up first, then add them by email."
        )

    existing = (
        await session.execute(
            select(Membership).where(Membership.org_id == scope.org_id, Membership.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValidationError(f"{email} is already a member of this organization.")

    membership = Membership(org_id=scope.org_id, user_id=user.id, role=body.role)
    session.add(membership)
    await session.flush()

    await record_audit(
        session, action="member.added", org_id=scope.org_id, user_id=scope.user_id,
        resource_type="user", resource_id=user.id,
        ip_address=request.client.host if request.client else "",
        detail={"email": email, "role": body.role},
    )
    return MemberResponse(
        user_id=user.id, email=user.email, display_name=user.display_name, role=membership.role,
        joined_at=membership.created_at.isoformat(),
    )


async def _guard_not_last_owner(session: SessionDep, org_id: str, target_user_id: str) -> None:
    target = (
        await session.execute(
            select(Membership).where(Membership.org_id == org_id, Membership.user_id == target_user_id)
        )
    ).scalar_one_or_none()
    if target is None or target.role != "owner":
        return
    other_owners = (
        await session.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.org_id == org_id, Membership.role == "owner", Membership.user_id != target_user_id)
        )
    ).scalar_one()
    if other_owners == 0:
        raise ValidationError(
            "This is the only owner of the organization. Promote another member to owner first."
        )


@router.patch("/admin/members/{user_id}", response_model=MemberResponse)
async def update_member_role(
    user_id: str, body: RoleUpdateRequest, session: SessionDep, scope: ScopeDep, request: Request
) -> MemberResponse:
    scope.require_admin()
    if body.role not in VALID_ROLES:
        raise ValidationError(f"role must be one of {sorted(VALID_ROLES)}")

    membership = (
        await session.execute(
            select(Membership).where(Membership.org_id == scope.org_id, Membership.user_id == user_id)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Member not found in this organization.")

    if membership.role == "owner" and body.role != "owner":
        # An org with no owner cannot administer itself — budgets, retention, and membership all become
        # unchangeable. This guard is why no single accidental demotion can brick a workspace.
        await _guard_not_last_owner(session, scope.org_id, user_id)

    old_role = membership.role
    membership.role = body.role
    user = await session.get(User, user_id)
    await record_audit(
        session, action="member.role_changed", org_id=scope.org_id, user_id=scope.user_id,
        resource_type="user", resource_id=user_id,
        ip_address=request.client.host if request.client else "",
        detail={"from": old_role, "to": body.role},
    )
    return MemberResponse(
        user_id=user_id, email=user.email if user else "", display_name=user.display_name if user else "",
        role=membership.role, joined_at=membership.created_at.isoformat(),
    )


@router.delete("/admin/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: str, session: SessionDep, scope: ScopeDep, request: Request
) -> None:
    scope.require_admin()
    membership = (
        await session.execute(
            select(Membership).where(Membership.org_id == scope.org_id, Membership.user_id == user_id)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Member not found in this organization.")

    await _guard_not_last_owner(session, scope.org_id, user_id)

    await record_audit(
        session, action="member.removed", org_id=scope.org_id, user_id=scope.user_id,
        resource_type="user", resource_id=user_id,
        ip_address=request.client.host if request.client else "",
    )
    await session.delete(membership)


# ======================================================================================
# Organization settings
# ======================================================================================
class OrgSettingsResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    monthly_budget_usd: float
    current_spend_usd: float
    retention_days: int
    store_raw_prompts: bool
    created_at: str


class OrgSettingsUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    store_raw_prompts: bool | None = None


@router.get("/admin/organization", response_model=OrgSettingsResponse)
async def get_organization_settings(org: OrgDep, scope: ScopeDep) -> OrgSettingsResponse:
    scope.require_admin()
    return OrgSettingsResponse(
        id=org.id, name=org.name, slug=org.slug, plan=org.plan,
        monthly_budget_usd=org.monthly_budget_usd, current_spend_usd=org.current_spend_usd,
        retention_days=org.retention_days, store_raw_prompts=org.store_raw_prompts,
        created_at=org.created_at.isoformat(),
    )


@router.patch("/admin/organization", response_model=OrgSettingsResponse)
async def update_organization_settings(
    body: OrgSettingsUpdateRequest, org: OrgDep, session: SessionDep, scope: ScopeDep, request: Request
) -> OrgSettingsResponse:
    scope.require_admin()
    changes: dict[str, dict[str, object]] = {}

    for field_name in ("name", "monthly_budget_usd", "retention_days", "store_raw_prompts"):
        new_value = getattr(body, field_name)
        if new_value is None:
            continue
        old_value = getattr(org, field_name)
        if new_value != old_value:
            changes[field_name] = {"from": old_value, "to": new_value}
            setattr(org, field_name, new_value)

    if changes:
        # store_raw_prompts is a privacy-significant toggle (it controls whether user prompt content is
        # retained at all — see docs/SECURITY.md §4 and docs/DATABASE.md). Every change is audited with
        # explicit before/after values so there is a record of exactly when retention policy changed.
        await record_audit(
            session, action="organization.settings_updated", org_id=scope.org_id, user_id=scope.user_id,
            ip_address=request.client.host if request.client else "", detail=changes,
        )

    return OrgSettingsResponse(
        id=org.id, name=org.name, slug=org.slug, plan=org.plan,
        monthly_budget_usd=org.monthly_budget_usd, current_spend_usd=org.current_spend_usd,
        retention_days=org.retention_days, store_raw_prompts=org.store_raw_prompts,
        created_at=org.created_at.isoformat(),
    )


# ======================================================================================
# Audit log
# ======================================================================================
class AuditLogEntry(BaseModel):
    id: str
    occurred_at: str
    action: str
    user_id: str | None
    resource_type: str
    resource_id: str
    ip_address: str
    detail: dict


@router.get("/admin/audit-logs", response_model=list[AuditLogEntry])
async def list_audit_logs(
    session: SessionDep,
    scope: ScopeDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    action: str | None = None,
) -> list[AuditLogEntry]:
    scope.require_admin()
    stmt = select(AuditLog).where(AuditLog.org_id == scope.org_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.occurred_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return [
        AuditLogEntry(
            id=e.id, occurred_at=e.occurred_at.isoformat(), action=e.action, user_id=e.user_id,
            resource_type=e.resource_type, resource_id=e.resource_id, ip_address=e.ip_address,
            detail=e.detail,
        )
        for e in result.scalars()
    ]


# ======================================================================================
# Provider accounts (bring-your-own provider API keys)
# ======================================================================================
class ProviderAccountResponse(BaseModel):
    id: str
    provider: str
    label: str
    key_last_four: str
    is_active: bool
    created_at: str


class ProviderAccountCreateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    label: str = Field(default="", max_length=200)
    api_key: str = Field(min_length=1, max_length=1000)


@router.get("/admin/provider-accounts", response_model=list[ProviderAccountResponse])
async def list_provider_accounts(session: SessionDep, scope: ScopeDep) -> list[ProviderAccountResponse]:
    scope.require_admin()
    result = await session.execute(
        select(ProviderAccount).where(ProviderAccount.org_id == scope.org_id)
    )
    return [
        ProviderAccountResponse(
            id=a.id, provider=a.provider, label=a.label, key_last_four=a.key_last_four,
            is_active=a.is_active, created_at=a.created_at.isoformat(),
        )
        for a in result.scalars()
    ]


@router.post(
    "/admin/provider-accounts", response_model=ProviderAccountResponse, status_code=status.HTTP_201_CREATED
)
async def create_provider_account(
    body: ProviderAccountCreateRequest,
    session: SessionDep,
    scope: ScopeDep,
    settings: SettingsDep,
    request: Request,
) -> ProviderAccountResponse:
    scope.require_admin()
    if not settings.encryption_key:
        # Never fall back to storing the key in plaintext — refuse outright and tell the operator what
        # to fix (ENCRYPTION_KEY generation is step 1.1 of MANUAL_STEPS.md).
        raise ValidationError(
            "This server has no ENCRYPTION_KEY configured, so tenant-supplied provider keys cannot be "
            "stored safely. Ask your operator to set ENCRYPTION_KEY (see MANUAL_STEPS.md)."
        )

    cipher = CredentialCipher(settings.encryption_key)
    encrypted = cipher.encrypt(body.api_key)
    account = ProviderAccount(
        org_id=scope.org_id, provider=body.provider, label=body.label,
        encrypted_key=encrypted, key_last_four=body.api_key[-4:], is_active=True,
    )
    session.add(account)
    await session.flush()

    await record_audit(
        session, action="provider_account.created", org_id=scope.org_id, user_id=scope.user_id,
        resource_type="provider_account", resource_id=account.id,
        ip_address=request.client.host if request.client else "",
        detail={"provider": body.provider, "label": body.label},  # never the key material itself
    )
    return ProviderAccountResponse(
        id=account.id, provider=account.provider, label=account.label,
        key_last_four=account.key_last_four, is_active=account.is_active,
        created_at=account.created_at.isoformat(),
    )


@router.delete("/admin/provider-accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_account(
    account_id: str, session: SessionDep, scope: ScopeDep, request: Request
) -> None:
    scope.require_admin()
    account = (
        await session.execute(
            select(ProviderAccount).where(ProviderAccount.id == account_id, ProviderAccount.org_id == scope.org_id)
        )
    ).scalar_one_or_none()
    if account is None:
        raise NotFoundError("Provider account not found.")

    await record_audit(
        session, action="provider_account.deleted", org_id=scope.org_id, user_id=scope.user_id,
        resource_type="provider_account", resource_id=account.id,
        ip_address=request.client.host if request.client else "",
        detail={"provider": account.provider},
    )
    await session.delete(account)
