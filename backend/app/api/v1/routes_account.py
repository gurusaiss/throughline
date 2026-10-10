"""The signed-in user's own profile, password, data export and deletion.

Distinct from `routes_admin.py`: everything here acts on the CALLER's own record, never another user's,
and none of it requires an admin role — it is the self-service counterpart to admin member management.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUserDep, OrgDep, ScopeDep, SessionDep
from app.api.v1 import schemas
from app.core.errors import AuthenticationError, AuthorizationError, ValidationError
from app.core.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.db.models import Execution, Membership
from app.services.persistence import record_audit

router = APIRouter(tags=["account"])


class ProfileUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    memory_enabled: bool | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


class DataExportResponse(BaseModel):
    user: dict
    memberships: list[dict]
    execution_count: int
    note: str = "This is your own account data only. It does not include other users' content."


@router.patch("/account/profile", response_model=schemas.UserResponse)
async def update_profile(
    body: ProfileUpdateRequest, user: CurrentUserDep, scope: ScopeDep, session: SessionDep, org: OrgDep
) -> schemas.UserResponse:
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.memory_enabled is not None:
        user.memory_enabled = body.memory_enabled

    await record_audit(
        session, action="account.profile_updated", org_id=scope.org_id, user_id=user.id,
        detail={k: v for k, v in body.model_dump(exclude_unset=True).items()},
    )
    return schemas.UserResponse(
        id=user.id, email=user.email, display_name=user.display_name, org_id=scope.org_id,
        org_name=org.name, role=scope.role, memory_enabled=user.memory_enabled,
    )


@router.post("/account/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest, user: CurrentUserDep, scope: ScopeDep, session: SessionDep, request: Request
) -> None:
    # An API key must never be able to change the password of the user who created it — that would let a
    # leaked key escalate into full account takeover rather than staying scoped to what the key can do.
    if scope.via_api_key:
        raise AuthorizationError("Password changes require an interactive session, not an API key.")

    if not verify_password(body.current_password, user.password_hash):
        raise AuthenticationError("Current password is incorrect.")
    if body.new_password == body.current_password:
        raise ValidationError("New password must be different from the current password.")

    user.password_hash = hash_password(body.new_password)
    # Audited without any password material — the event matters, the value never does (rule 28).
    await record_audit(
        session, action="account.password_changed", org_id=scope.org_id, user_id=user.id,
        ip_address=request.client.host if request.client else "",
    )


@router.get("/account/export", response_model=DataExportResponse)
async def export_account_data(
    user: CurrentUserDep, scope: ScopeDep, session: SessionDep
) -> DataExportResponse:
    """A real, user-controlled export — not a stub. The privacy model promises this; this makes it true."""
    memberships = (
        await session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalars().all()
    execution_count = (
        await session.execute(
            select(func.count()).select_from(Execution).where(Execution.user_id == user.id)
        )
    ).scalar_one()

    return DataExportResponse(
        user={
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "memory_enabled": user.memory_enabled,
            "preferences": user.preferences,
            "created_at": user.created_at.isoformat(),
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        },
        memberships=[{"org_id": m.org_id, "role": m.role, "joined_at": m.created_at.isoformat()} for m in memberships],
        execution_count=execution_count,
    )


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    user: CurrentUserDep, scope: ScopeDep, session: SessionDep, request: Request
) -> None:
    # Refuse to delete the last owner of an org that still has other members — that would strand the
    # workspace with no one able to administer it. An org with no members left at all (a solo workspace)
    # is fine to delete along with the user; that is the common "delete my account" case.
    memberships = (
        await session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalars().all()

    for m in memberships:
        if m.role != "owner":
            continue
        other_members = (
            await session.execute(
                select(func.count())
                .select_from(Membership)
                .where(Membership.org_id == m.org_id, Membership.user_id != user.id)
            )
        ).scalar_one()
        if other_members > 0:
            raise ValidationError(
                "You are the only owner of a workspace that still has other members. "
                "Transfer ownership or remove the other members before deleting your account."
            )

    # Audit logged BEFORE deletion so the record of the deletion itself survives the delete.
    await record_audit(
        session, action="account.deleted", org_id=scope.org_id, user_id=user.id,
        ip_address=request.client.host if request.client else "",
    )
    await session.delete(user)
