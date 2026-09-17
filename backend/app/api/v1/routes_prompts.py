"""Prompt library and versioning (rule 15).

Versions are immutable. Editing creates a new version; `active_version_id` selects which one is served, so
a rollback is a pointer change rather than an edit — the audit trail stays intact.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import func, select

from app.api.deps import ScopeDep, SessionDep
from app.api.v1 import schemas
from app.core.errors import NotFoundError
from app.db.models import Prompt, PromptVersion

router = APIRouter(tags=["prompts"])


def _version_response(v: PromptVersion, active_id: str | None) -> schemas.PromptVersionResponse:
    return schemas.PromptVersionResponse(
        id=v.id,
        version=v.version,
        system_text=v.system_text,
        user_text=v.user_text,
        output_schema=v.output_schema,
        notes=v.notes,
        created_at=v.created_at.isoformat(),
        is_active=v.id == active_id,
    )


async def _load_prompt(session: SessionDep, prompt_id: str, scope: ScopeDep) -> Prompt:
    result = await session.execute(
        select(Prompt).where(Prompt.id == prompt_id, Prompt.org_id == scope.org_id)
    )
    prompt = result.scalar_one_or_none()
    if prompt is None:
        # 404 rather than 403 for another org's prompt: a 403 confirms it exists.
        raise NotFoundError("Prompt not found.")
    return prompt


@router.get("/prompts", response_model=list[schemas.PromptResponse])
async def list_prompts(session: SessionDep, scope: ScopeDep) -> list[schemas.PromptResponse]:
    result = await session.execute(
        select(Prompt).where(Prompt.org_id == scope.org_id).order_by(Prompt.updated_at.desc())
    )
    prompts = list(result.scalars())
    out: list[schemas.PromptResponse] = []
    for p in prompts:
        count = (
            await session.execute(
                select(func.count()).select_from(PromptVersion).where(PromptVersion.prompt_id == p.id)
            )
        ).scalar_one()
        active = (
            await session.get(PromptVersion, p.active_version_id) if p.active_version_id else None
        )
        out.append(
            schemas.PromptResponse(
                id=p.id,
                name=p.name,
                description=p.description,
                task_type=p.task_type,
                version_count=count,
                active_version=_version_response(active, p.active_version_id) if active else None,
                created_at=p.created_at.isoformat(),
                updated_at=p.updated_at.isoformat(),
            )
        )
    return out


@router.post("/prompts", response_model=schemas.PromptResponse, status_code=status.HTTP_201_CREATED)
async def create_prompt(
    body: schemas.PromptCreateRequest, session: SessionDep, scope: ScopeDep
) -> schemas.PromptResponse:
    scope.require_write()
    prompt = Prompt(
        org_id=scope.org_id,
        project_id=body.project_id,
        created_by=scope.user_id,
        name=body.name,
        description=body.description,
        task_type=body.task_type,
    )
    session.add(prompt)
    await session.flush()

    version = PromptVersion(
        org_id=scope.org_id,
        prompt_id=prompt.id,
        version=1,
        created_by=scope.user_id,
        system_text=body.system_text,
        user_text=body.user_text,
        output_schema=body.output_schema,
        notes="Initial version",
    )
    session.add(version)
    await session.flush()
    prompt.active_version_id = version.id

    return schemas.PromptResponse(
        id=prompt.id,
        name=prompt.name,
        description=prompt.description,
        task_type=prompt.task_type,
        version_count=1,
        active_version=_version_response(version, version.id),
        created_at=prompt.created_at.isoformat(),
        updated_at=prompt.updated_at.isoformat(),
    )


@router.get("/prompts/{prompt_id}/versions", response_model=list[schemas.PromptVersionResponse])
async def list_versions(
    prompt_id: str, session: SessionDep, scope: ScopeDep
) -> list[schemas.PromptVersionResponse]:
    prompt = await _load_prompt(session, prompt_id, scope)
    result = await session.execute(
        select(PromptVersion)
        .where(PromptVersion.prompt_id == prompt.id)
        .order_by(PromptVersion.version.desc())
    )
    return [_version_response(v, prompt.active_version_id) for v in result.scalars()]


@router.post(
    "/prompts/{prompt_id}/versions",
    response_model=schemas.PromptVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    prompt_id: str,
    body: schemas.PromptVersionCreateRequest,
    session: SessionDep,
    scope: ScopeDep,
) -> schemas.PromptVersionResponse:
    scope.require_write()
    prompt = await _load_prompt(session, prompt_id, scope)
    highest = (
        await session.execute(
            select(func.max(PromptVersion.version)).where(PromptVersion.prompt_id == prompt.id)
        )
    ).scalar_one() or 0

    version = PromptVersion(
        org_id=scope.org_id,
        prompt_id=prompt.id,
        version=highest + 1,
        created_by=scope.user_id,
        system_text=body.system_text,
        user_text=body.user_text,
        output_schema=body.output_schema,
        notes=body.notes,
    )
    session.add(version)
    await session.flush()
    prompt.active_version_id = version.id
    return _version_response(version, version.id)


@router.post("/prompts/{prompt_id}/activate/{version_id}", response_model=schemas.PromptVersionResponse)
async def activate_version(
    prompt_id: str, version_id: str, session: SessionDep, scope: ScopeDep
) -> schemas.PromptVersionResponse:
    """Roll back (or forward) by pointing at an existing version. Nothing is mutated or lost."""
    scope.require_write()
    prompt = await _load_prompt(session, prompt_id, scope)
    version = await session.get(PromptVersion, version_id)
    if version is None or version.prompt_id != prompt.id:
        raise NotFoundError("Version not found for this prompt.")
    prompt.active_version_id = version.id
    return _version_response(version, version.id)
