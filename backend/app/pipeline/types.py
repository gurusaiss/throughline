"""The central intermediate representation (ADR-002).

Every pipeline stage reads and writes these types. Nothing in this module imports a provider, a database
session or a FastAPI object, so the whole IR is constructible and testable with no I/O.

The design rule that matters: each inferred field carries *provenance*. Knowing that `audience` was guessed by
a heuristic rather than stated by the user is what lets the optimiser decide whether to ask a clarifying
question, and it is what powers the "why this prompt?" view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------------------
class TaskType(str, Enum):
    """Task taxonomy.

    Deliberately a flat enum plus a `TaskProfile` table rather than a class hierarchy: new categories are
    added by appending a member and a profile row, with no changes to the optimiser or router (rule 5).
    """

    GENERAL_QA = "general_qa"
    CODING = "coding"
    DEBUGGING = "debugging"
    SOFTWARE_ARCHITECTURE = "software_architecture"
    RESEARCH = "research"
    SUMMARIZATION = "summarization"
    WRITING = "writing"
    REWRITING = "rewriting"
    TRANSLATION = "translation"
    EDUCATION = "education"
    MATHEMATICS = "mathematics"
    DATA_ANALYSIS = "data_analysis"
    BUSINESS_ANALYSIS = "business_analysis"
    MARKETING = "marketing"
    SEO = "seo"
    CAREER = "career"
    INTERVIEW_PREP = "interview_prep"
    DOCUMENT_ANALYSIS = "document_analysis"
    IMAGE_ANALYSIS = "image_analysis"
    CREATIVE = "creative"
    STRUCTURED_EXTRACTION = "structured_extraction"
    CLASSIFICATION = "classification"
    PLANNING = "planning"
    BRAINSTORMING = "brainstorming"
    AGENTIC = "agentic"
    REASONING = "reasoning"
    CURRENT_INFO = "current_info"


class Complexity(str, Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"

    @property
    def rank(self) -> int:
        return _COMPLEXITY_RANK[self]


_COMPLEXITY_RANK = {
    Complexity.TRIVIAL: 0,
    Complexity.LOW: 1,
    Complexity.MEDIUM: 2,
    Complexity.HIGH: 3,
    Complexity.VERY_HIGH: 4,
}


class Ambiguity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _AMBIGUITY_RANK[self]


_AMBIGUITY_RANK = {
    Ambiguity.NONE: 0,
    Ambiguity.LOW: 1,
    Ambiguity.MEDIUM: 2,
    Ambiguity.HIGH: 3,
}


class RiskLevel(str, Enum):
    """Risk of the *output* causing harm if wrong.

    Drives evaluation depth and uncertainty disclosure, not refusal. A medical question is high risk and
    gets more verification plus an explicit uncertainty notice; it is not blocked.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}[self]


class OutputFormat(str, Enum):
    PROSE = "prose"
    MARKDOWN = "markdown"
    CODE = "code"
    JSON = "json"
    TABLE = "table"
    LIST = "list"
    EMAIL = "email"
    DOCUMENT = "document"


class Modality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"


class ReasoningDepth(str, Enum):
    NONE = "none"
    SHALLOW = "shallow"
    MODERATE = "moderate"
    DEEP = "deep"


class OrchestrationMode(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    DEEP = "deep"
    EXPERT = "expert"

    @property
    def rank(self) -> int:
        return {
            OrchestrationMode.FAST: 0,
            OrchestrationMode.BALANCED: 1,
            OrchestrationMode.DEEP: 2,
            OrchestrationMode.EXPERT: 3,
        }[self]


class Provenance(str, Enum):
    """Where a field's value came from. Central to the honesty requirements."""

    USER = "user"          # explicitly supplied — authoritative
    HEURISTIC = "heuristic"  # deterministic inference — free, moderate confidence
    MODEL = "model"        # inferred by an LLM — costs a call, higher confidence
    DEFAULT = "default"    # fallback — lowest confidence


# --------------------------------------------------------------------------------------
# Stage 1: signals (heuristic, zero cost) — ADR-003
# --------------------------------------------------------------------------------------
@dataclass(slots=True)
class RequestSignals:
    """Zero-cost deterministic signals extracted before any LLM call.

    `confidence` is the extractor's confidence *in its own classification*, and is the gate for skipping
    intent analysis entirely. It is intentionally conservative: when unsure, we escalate and pay for a call
    rather than guess cheaply and be wrong.
    """

    char_count: int = 0
    word_count: int = 0
    likely_task: TaskType = TaskType.GENERAL_QA
    complexity: Complexity = Complexity.MEDIUM
    ambiguity: Ambiguity = Ambiguity.MEDIUM
    risk: RiskLevel = RiskLevel.LOW
    needs_current_info: bool = False
    needs_tools: bool = False
    needs_multimodal: bool = False
    has_explicit_format: bool = False
    has_explicit_audience: bool = False
    has_constraints: bool = False
    is_question: bool = False
    is_imperative: bool = False
    contains_code: bool = False
    confidence: float = 0.0
    matched_rules: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.likely_task.value}/{self.complexity.value} "
            f"amb={self.ambiguity.value} risk={self.risk.value} conf={self.confidence:.2f}"
        )


# --------------------------------------------------------------------------------------
# Stage 2: the ExecutionSpec
# --------------------------------------------------------------------------------------
class Constraint(BaseModel):
    kind: str = Field(description="e.g. length, tone, must_include, must_avoid, deadline")
    value: str
    source: Provenance = Provenance.HEURISTIC


class ExecutionSpec(BaseModel):
    """The compiled understanding of what the user wants.

    Produced by the compiler from signals (+ optional LLM intent analysis). Consumed by the optimiser,
    router, generator and evaluator. This is the object that gets persisted and replayed.
    """

    raw_input: str
    goal: str = ""
    task_type: TaskType = TaskType.GENERAL_QA
    domain: str = ""
    entities: list[str] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    context: str = ""

    audience: str = ""
    tone: str = ""
    output_format: OutputFormat = OutputFormat.MARKDOWN
    output_schema: dict[str, Any] | None = None

    complexity: Complexity = Complexity.MEDIUM
    ambiguity: Ambiguity = Ambiguity.MEDIUM
    risk_level: RiskLevel = RiskLevel.LOW
    reasoning_depth: ReasoningDepth = ReasoningDepth.SHALLOW

    needs_current_info: bool = False
    needs_tools: bool = False
    required_modalities: list[Modality] = Field(default_factory=lambda: [Modality.TEXT])

    missing_information: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)

    provenance: dict[str, Provenance] = Field(default_factory=dict)

    def provenance_of(self, field_name: str) -> Provenance:
        return self.provenance.get(field_name, Provenance.DEFAULT)

    def is_user_specified(self, field_name: str) -> bool:
        return self.provenance.get(field_name) is Provenance.USER

    @property
    def requires_structured_output(self) -> bool:
        return self.output_schema is not None or self.output_format is OutputFormat.JSON


# --------------------------------------------------------------------------------------
# Stage 3: optimisation
# --------------------------------------------------------------------------------------
class Technique(str, Enum):
    """Prompt-construction techniques the optimiser may apply.

    Each is applied only when a signal justifies it (rule 6: do not blindly add all techniques).
    """

    EXPLICIT_OBJECTIVE = "explicit_objective"
    ROLE_ASSIGNMENT = "role_assignment"
    CONTEXT_INJECTION = "context_injection"
    CONSTRAINT_ENUMERATION = "constraint_enumeration"
    OUTPUT_FORMAT_SPEC = "output_format_spec"
    OUTPUT_SCHEMA = "output_schema"
    FEW_SHOT = "few_shot"
    TASK_DECOMPOSITION = "task_decomposition"
    VERIFICATION_INSTRUCTION = "verification_instruction"
    AMBIGUITY_RESOLUTION = "ambiguity_resolution"
    SOURCE_REQUIREMENT = "source_requirement"
    TOOL_DECLARATION = "tool_declaration"
    AUDIENCE_SPEC = "audience_spec"
    TONE_SPEC = "tone_spec"
    UNCERTAINTY_DISCLOSURE = "uncertainty_disclosure"


class AppliedTechnique(BaseModel):
    technique: Technique
    reason: str = Field(description="User-facing justification, shown in 'why this prompt?'")
    trigger: str = Field(description="The signal that caused this, for offline audit")


class OptimizationDecision(BaseModel):
    """What the optimiser did and why.

    Persisted so we can later answer "does optimisation actually help for this task type?" (RESEARCH 2.2).
    Without this record the A/B harness cannot attribute an outcome to a decision.
    """

    applied: list[AppliedTechnique] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    rationale: str = ""

    @property
    def technique_names(self) -> list[str]:
        return [a.technique.value for a in self.applied]


class CompiledPrompt(BaseModel):
    """The execution-ready request.

    Ordered stable-prefix-first so the provider's prompt cache covers as much as possible (RESEARCH 7.2):
    system -> project context -> task instructions -> volatile user input.
    """

    system: str = ""
    user: str = ""
    cacheable_prefix_chars: int = 0
    output_schema: dict[str, Any] | None = None
    decision: OptimizationDecision = Field(default_factory=OptimizationDecision)

    def render_debug(self) -> str:
        return f"--- SYSTEM ---\n{self.system}\n--- USER ---\n{self.user}"


# --------------------------------------------------------------------------------------
# Stage 4: routing
# --------------------------------------------------------------------------------------
class RoutingStrategy(str, Enum):
    SINGLE = "single"
    FALLBACK = "fallback"
    PARALLEL = "parallel"
    ENSEMBLE = "ensemble"
    SPECIALIST = "specialist"


class ModelChoice(BaseModel):
    model_id: str
    provider: str
    role: str = "primary"  # primary | fallback | candidate | judge | specialist
    score: float = 0.0
    reason: str = ""


class RoutingPlan(BaseModel):
    strategy: RoutingStrategy = RoutingStrategy.SINGLE
    selections: list[ModelChoice] = Field(default_factory=list)
    reason: str = ""
    estimated_cost_usd: float = 0.0
    estimated_latency_ms: int = 0
    rejected: list[dict[str, str]] = Field(default_factory=list)

    @property
    def primary(self) -> ModelChoice | None:
        for s in self.selections:
            if s.role == "primary":
                return s
        return self.selections[0] if self.selections else None

    @property
    def candidates(self) -> list[ModelChoice]:
        return [s for s in self.selections if s.role in ("primary", "candidate")]


# --------------------------------------------------------------------------------------
# Stage 5: generation
# --------------------------------------------------------------------------------------
class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class Candidate(BaseModel):
    id: str
    model_id: str
    provider: str
    text: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    latency_ms: int = 0
    finish_reason: str = "stop"
    error: str | None = None
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())
