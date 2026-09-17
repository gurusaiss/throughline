"""Database schema.

Every table earns its place (rule 33). Tenant-owned tables carry `org_id` with a composite index, because
every query is scoped by org and the isolation guarantee depends on it (ARCHITECTURE §7).

Deferred deliberately, and documented rather than pre-built: `datasets`, `dataset_items`, `experiments`,
`experiment_runs`, `files`, `subscriptions`. They belong to Phase 2 features; empty tables are not
architecture.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


# JSONB on Postgres, JSON elsewhere — keeps the SQLite-backed test suite working without a second schema.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ======================================================================================
# Tenancy
# ======================================================================================
class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(50), default="free")

    monthly_budget_usd: Mapped[float] = mapped_column(Float, default=25.0)
    # Reset monthly by the usage service; kept denormalised because it is read on every request for the
    # pre-flight budget check (ADR-011) and a SUM over usage_records would be a per-request aggregate.
    current_spend_usd: Mapped[float] = mapped_column(Float, default=0.0)
    spend_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    store_raw_prompts: Mapped[bool] = mapped_column(Boolean, default=False)

    memberships: Mapped[list[Membership]] = relationship(back_populates="organization")

    __table_args__ = (CheckConstraint("monthly_budget_usd >= 0", name="ck_org_budget_non_negative"),)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # User-controlled preference memory (rule 24). Explicit, inspectable, deletable.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # owner | admin | member | viewer

    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_membership_org_user"),
        CheckConstraint(
            "role IN ('owner','admin','member','viewer')", name="ck_membership_role"
        ),
    )


class Project(Base, TimestampMixin):
    """Durable context reused across requests (rule 25)."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (Index("ix_projects_org_created", "org_id", "created_at"),)


class ApiKey(Base, TimestampMixin):
    """Programmatic access. Only a hash is stored; the plaintext is shown once at creation."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)  # for display and lookup narrowing
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class ProviderAccount(Base, TimestampMixin):
    """A tenant's own provider credential (BYO key).

    `encrypted_key` is Fernet ciphertext — never plaintext, never returned by any API (rule 28).
    """

    __tablename__ = "provider_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(200), default="")
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_last_four: Mapped[str] = mapped_column(String(8), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("org_id", "provider", "label", name="uq_provider_account"),)


# ======================================================================================
# Prompts
# ======================================================================================
class Prompt(Base, TimestampMixin):
    __tablename__ = "prompts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    task_type: Mapped[str] = mapped_column(String(50), default="general_qa")
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    # Points at the version currently served. Separating "latest" from "active" is what makes a rollback
    # a metadata change rather than a new edit.
    active_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    versions: Mapped[list[PromptVersion]] = relationship(
        back_populates="prompt", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_prompts_org_name", "org_id", "name"),)


class PromptVersion(Base, TimestampMixin):
    """Immutable once created. Editing a prompt creates a new version (rule 15)."""

    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    prompt_id: Mapped[str] = mapped_column(ForeignKey("prompts.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    system_text: Mapped[str] = mapped_column(Text, default="")
    user_text: Mapped[str] = mapped_column(Text, default="")
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    optimization_decision: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    spec_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")

    prompt: Mapped[Prompt] = relationship(back_populates="versions")

    __table_args__ = (UniqueConstraint("prompt_id", "version", name="uq_prompt_version"),)


# ======================================================================================
# Executions and tracing
# ======================================================================================
class Execution(Base, TimestampMixin):
    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    prompt_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    status: Mapped[str] = mapped_column(String(30), default="ok", index=True)
    mode: Mapped[str] = mapped_column(String(20), default="balanced", index=True)
    mode_reason: Mapped[str] = mapped_column(Text, default="")

    # Raw content is stored only when the org opts in (rule 28 / privacy default off).
    raw_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_redacted: Mapped[bool] = mapped_column(Boolean, default=False)

    task_type: Mapped[str] = mapped_column(String(50), default="general_qa", index=True)
    complexity: Mapped[str] = mapped_column(String(20), default="medium")
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    confidence: Mapped[str] = mapped_column(String(20), default="low")

    routing_strategy: Mapped[str] = mapped_column(String(20), default="single")
    selected_model_id: Mapped[str] = mapped_column(String(100), default="", index=True)

    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    orchestration_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    trace: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSONType, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # The analytics queries we actually run: recent executions per org, and cost/quality by task.
        Index("ix_executions_org_created", "org_id", "created_at"),
        Index("ix_executions_org_task_created", "org_id", "task_type", "created_at"),
    )


class ModelCall(Base, TimestampMixin):
    """One provider call. The unit of cost attribution and the basis of usage reporting."""

    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    purpose: Mapped[str] = mapped_column(String(20), default="generate")  # generate | intent | judge

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    from_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    # Recorded per call so historical cost can be re-derived if a price was later corrected (ADR-004).
    pricing_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Evaluation(Base, TimestampMixin):
    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="skipped")
    selected_candidate_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    selection_reason: Mapped[str] = mapped_column(Text, default="")
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_agreement: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_model_id: Mapped[str] = mapped_column(String(100), default="")
    # Persisted so a score produced without a cross-family judge stays visibly different from one
    # produced with it (RESEARCH 5.1).
    mitigations: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    results: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class Feedback(Base, TimestampMixin):
    """Human signal. The scarcest and most valuable evaluation input (rule 11)."""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    rating: Mapped[str] = mapped_column(String(20))  # up | down
    reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    edited_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("rating IN ('up','down')", name="ck_feedback_rating"),
        Index("ix_feedback_org_created", "org_id", "created_at"),
    )


class UsageRecordRow(Base):
    """Append-only usage ledger — the billing system of record (ADR-011).

    Separate from `model_calls` on purpose: model calls are debug/trace data subject to retention
    deletion, whereas the ledger must survive content deletion for billing integrity. It intentionally
    holds no user content.
    """

    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    model_id: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50))
    purpose: Mapped[str] = mapped_column(String(20))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (Index("ix_usage_org_recorded", "org_id", "recorded_at"),)


class AuditLog(Base):
    """Security-relevant events (rule 28). Append-only; never deleted by retention jobs."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str | None] = mapped_column(String(32), index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(50), default="")
    resource_id: Mapped[str] = mapped_column(String(32), default="")
    ip_address: Mapped[str] = mapped_column(String(45), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    __table_args__ = (Index("ix_audit_org_occurred", "org_id", "occurred_at"),)
