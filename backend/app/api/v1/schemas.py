"""API request/response schemas.

Response shapes are part of the product's honesty contract, not just serialisation:

* costs are labelled `estimated` vs `actual`;
* every AI-generated payload carries `generated_by: "ai"` (EU AI Act Art. 50, in force since 2026-08-02);
* evaluation results always travel with their provenance and caveats;
* no field anywhere can express "accuracy".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import MIN_PASSWORD_LENGTH


# ======================================================================================
# Auth
# ======================================================================================
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)
    display_name: str = Field(default="", max_length=200)
    organization_name: str = Field(default="", max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    org_id: str
    org_name: str
    role: str
    memory_enabled: bool


# ======================================================================================
# Optimize / execute
# ======================================================================================
class OptimizeRequest(BaseModel):
    input: str = Field(min_length=1, max_length=100_000)
    mode: Literal["auto", "fast", "balanced", "deep", "expert"] = "auto"
    project_id: str | None = None
    output_schema: dict[str, Any] | None = None

    @field_validator("input")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("input cannot be blank")
        return v


class ExecuteRequest(OptimizeRequest):
    model_id: str | None = Field(default=None, description="Pin a model; otherwise routed automatically")
    candidate_count: int | None = Field(default=None, ge=1, le=5)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int | None = Field(default=None, ge=1, le=64_000)
    enable_judge: bool | None = None
    max_cost_usd: float | None = Field(default=None, gt=0)
    save_as_prompt: str | None = Field(default=None, max_length=200)


class CompareRequest(BaseModel):
    """Raw vs optimised, or model vs model.

    This is the feature that proves the product works to a sceptic, and the honest alternative to
    publishing a benchmark number we cannot substantiate (PRODUCT §4).
    """

    input: str = Field(min_length=1, max_length=100_000)
    compare: Literal["raw_vs_optimized", "models"] = "raw_vs_optimized"
    model_ids: list[str] = Field(default_factory=list, max_length=4)
    project_id: str | None = None


class AppliedTechniqueResponse(BaseModel):
    technique: str
    reason: str
    trigger: str


class WhyResponse(BaseModel):
    """'Why this prompt?' — explains OUR transformation, never the model's reasoning (PRODUCT §6)."""

    mode: str
    mode_reason: str
    task_type: str
    complexity: str
    ambiguity: str
    risk_level: str
    applied: list[AppliedTechniqueResponse]
    skipped: list[str]
    rationale: str
    signal_confidence: float
    inferred_fields: dict[str, str] = Field(
        default_factory=dict, description="field -> provenance (user|heuristic|model|default)"
    )


class CompiledPromptResponse(BaseModel):
    system: str
    user: str
    output_schema: dict[str, Any] | None = None


class ModelChoiceResponse(BaseModel):
    model_id: str
    provider: str
    role: str
    reason: str


class RoutingResponse(BaseModel):
    strategy: str
    selections: list[ModelChoiceResponse]
    reason: str
    estimated_cost_usd: float
    estimated_latency_ms: int
    cost_is_estimate: bool = True


class CheckResponse(BaseModel):
    name: str
    status: str
    detail: str
    criterion: str


class ScoreResponse(BaseModel):
    criterion: str
    score: float
    source: str
    reasoning: str


class CandidateResponse(BaseModel):
    id: str
    model_id: str
    provider: str
    text: str
    cost_usd: float
    latency_ms: int
    from_cache: bool
    error: str | None = None
    checks: list[CheckResponse] = Field(default_factory=list)
    scores: list[ScoreResponse] = Field(default_factory=list)
    overall: float | None = None


class MitigationsResponse(BaseModel):
    """Which judge-bias mitigations were actually applied (RESEARCH 5.1)."""

    order_randomized: bool
    model_identity_hidden: bool
    judge_family: str
    self_preference_risk: bool
    length_delta_chars: int


class EvaluationResponse(BaseModel):
    status: str
    selected_candidate_id: str | None
    selection_reason: str
    model_agreement: float | None = None
    judge_agreement: float | None = None
    mitigations: MitigationsResponse | None = None
    cost_usd: float = 0.0
    note: str = Field(
        default=(
            "Evaluation signals indicate how an output scored against a rubric and automated checks. "
            "They are not a measure of factual accuracy."
        )
    )


class CostResponse(BaseModel):
    total_usd: float
    generation_usd: float
    orchestration_usd: float
    orchestration_ratio: float
    input_tokens: int
    output_tokens: int
    llm_calls: int
    is_estimate: bool = False
    includes_unverified_pricing: bool = False


class ExecutionResponse(BaseModel):
    execution_id: str
    status: str
    output: str
    generated_by: Literal["ai"] = "ai"  # EU AI Act Art. 50 transparency
    confidence: str
    confidence_note: str = Field(
        default=(
            "Confidence reflects the strength of automated evidence about this response, not a "
            "probability that it is correct."
        )
    )
    why: WhyResponse
    prompt: CompiledPromptResponse
    routing: RoutingResponse | None = None
    candidates: list[CandidateResponse] = Field(default_factory=list)
    evaluation: EvaluationResponse | None = None
    cost: CostResponse
    latency_ms: int
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class OptimizeResponse(BaseModel):
    """Prompt-only. No execution, no generation cost."""

    status: str
    prompt: CompiledPromptResponse
    why: WhyResponse
    routing: RoutingResponse | None = None
    estimated_cost_usd: float = 0.0


class ComparisonArm(BaseModel):
    label: str
    prompt: CompiledPromptResponse
    output: str
    model_id: str
    cost_usd: float
    latency_ms: int
    checks_passed: int
    checks_failed: int
    overall: float | None = None


class CompareResponse(BaseModel):
    comparison_id: str
    arms: list[ComparisonArm]
    verdict: str
    caveat: str = Field(
        default=(
            "A single comparison is one sample, not evidence that one approach is better in general. "
            "Use the evaluation lab on a dataset for a conclusion you can rely on."
        )
    )


# ======================================================================================
# Models / registry
# ======================================================================================
class PricingResponse(BaseModel):
    input_per_mtok: float
    output_per_mtok: float
    cache_read_per_mtok: float | None = None
    verified: bool
    verified_at: str | None = None
    source: str = ""
    staleness_warning: str = ""


class ModelResponse(BaseModel):
    id: str
    provider: str
    display_name: str
    family: str
    tier: str
    context_window: int
    max_output_tokens: int
    modalities: list[str]
    capabilities: list[str]
    quality_index: int
    speed_index: int
    pricing: PricingResponse
    available: bool
    configured: bool = Field(description="Whether a credential exists for this provider")


# ======================================================================================
# Prompts
# ======================================================================================
class PromptCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    system_text: str = Field(default="", max_length=100_000)
    user_text: str = Field(default="", max_length=100_000)
    task_type: str = "general_qa"
    output_schema: dict[str, Any] | None = None
    project_id: str | None = None


class PromptVersionCreateRequest(BaseModel):
    system_text: str = Field(default="", max_length=100_000)
    user_text: str = Field(default="", max_length=100_000)
    output_schema: dict[str, Any] | None = None
    notes: str = Field(default="", max_length=2000)


class PromptVersionResponse(BaseModel):
    id: str
    version: int
    system_text: str
    user_text: str
    output_schema: dict[str, Any] | None
    notes: str
    created_at: str
    is_active: bool


class PromptResponse(BaseModel):
    id: str
    name: str
    description: str
    task_type: str
    version_count: int
    active_version: PromptVersionResponse | None = None
    created_at: str
    updated_at: str


# ======================================================================================
# Feedback / analytics
# ======================================================================================
class FeedbackRequest(BaseModel):
    execution_id: str
    rating: Literal["up", "down"]
    reason: (
        Literal["incorrect", "incomplete", "ignored_instructions", "wrong_format", "unsafe", "other"]
        | None
    ) = None
    comment: str = Field(default="", max_length=5000)
    edited_output: str | None = Field(default=None, max_length=200_000)


class ExecutionSummary(BaseModel):
    id: str
    created_at: str
    status: str
    mode: str
    task_type: str
    selected_model_id: str
    cost_usd: float
    latency_ms: int
    llm_calls: int
    confidence: str
    preview: str


class AnalyticsResponse(BaseModel):
    period_days: int
    executions: int
    total_cost_usd: float
    generation_cost_usd: float
    orchestration_cost_usd: float
    orchestration_ratio: float
    avg_latency_ms: int
    avg_llm_calls: float
    mode_distribution: dict[str, int]
    task_distribution: dict[str, int]
    model_distribution: dict[str, int]
    feedback_positive: int
    feedback_negative: int
    budget_limit_usd: float
    budget_spent_usd: float
    note: str = Field(
        default=(
            "Cost figures derive from provider-reported token usage priced against the model catalogue. "
            "Entries with unverified pricing are approximate."
        )
    )


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: str | None = None
