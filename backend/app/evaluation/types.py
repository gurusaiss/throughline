"""Evaluation IR.

Design constraints come straight from RESEARCH 5.1 (judge bias is real, named and measurable):

* every score carries its *source* — a deterministic check and an LLM opinion are not the same kind of fact,
  and collapsing them into one number is how eval systems start lying;
* judged results carry the mitigations that were actually applied, so an un-mitigated score is visibly
  un-mitigated rather than quietly equivalent;
* nothing here can express "accuracy" as a percentage of truth.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Criterion(str, Enum):
    CORRECTNESS = "correctness"
    RELEVANCE = "relevance"
    COMPLETENESS = "completeness"
    INSTRUCTION_FOLLOWING = "instruction_following"
    CLARITY = "clarity"
    CONSISTENCY = "consistency"
    GROUNDING = "grounding"
    FORMAT_COMPLIANCE = "format_compliance"
    SAFETY = "safety"
    HALLUCINATION_RISK = "hallucination_risk"


class ScoreSource(str, Enum):
    DETERMINISTIC = "deterministic"  # authoritative where applicable
    JUDGE = "judge"                  # advisory only
    HUMAN = "human"                  # authoritative, sparse


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    ERROR = "error"


class DeterministicCheck(BaseModel):
    """A machine-verifiable fact about an output. No model opinion involved."""

    name: str
    status: CheckStatus
    detail: str = ""
    criterion: Criterion = Criterion.FORMAT_COMPLIANCE

    @property
    def is_blocking(self) -> bool:
        return self.status is CheckStatus.FAIL


class CriterionScore(BaseModel):
    criterion: Criterion
    score: float = Field(ge=0.0, le=1.0)
    source: ScoreSource
    reasoning: str = Field(default="", description="Concise justification; never raw chain-of-thought")


class JudgeMitigations(BaseModel):
    """Which bias mitigations were actually applied to a judging call (RESEARCH 5.1).

    Recorded rather than assumed: `self_preference_risk=True` means we could not find a cross-family judge,
    and the score should be read with that in mind. A system that silently degrades its own safeguards is
    worse than one that never had them, because the number looks identical.
    """

    order_randomized: bool = False
    permutation: list[int] = Field(default_factory=list)
    model_identity_hidden: bool = True
    judge_family: str = ""
    generator_families: list[str] = Field(default_factory=list)
    self_preference_risk: bool = False
    length_delta_chars: int = 0


class CandidateEvaluation(BaseModel):
    candidate_id: str
    checks: list[DeterministicCheck] = Field(default_factory=list)
    scores: list[CriterionScore] = Field(default_factory=list)
    overall: float = 0.0
    confidence: float = 0.0
    notes: str = ""

    @property
    def failed_checks(self) -> list[DeterministicCheck]:
        return [c for c in self.checks if c.is_blocking]

    @property
    def deterministically_valid(self) -> bool:
        return not self.failed_checks

    def score_for(self, criterion: Criterion) -> float | None:
        for s in self.scores:
            if s.criterion is criterion:
                return s.score
        return None


class EvaluationStatus(str, Enum):
    COMPLETE = "complete"
    DETERMINISTIC_ONLY = "deterministic_only"
    UNAVAILABLE = "unavailable"   # evaluator failed; the answer is still returned
    SKIPPED = "skipped"           # policy chose not to evaluate (e.g. FAST mode)


class EvaluationReport(BaseModel):
    status: EvaluationStatus = EvaluationStatus.SKIPPED
    evaluations: list[CandidateEvaluation] = Field(default_factory=list)
    selected_candidate_id: str | None = None
    selection_reason: str = ""
    mitigations: JudgeMitigations | None = None
    judge_agreement: float | None = Field(
        default=None,
        description="Agreement across judges when >1 judge ran; None when not measured.",
    )
    model_agreement: float | None = Field(
        default=None,
        description="Similarity across candidate outputs; a weak convergence signal, not a truth signal.",
    )
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None

    def for_candidate(self, candidate_id: str) -> CandidateEvaluation | None:
        for e in self.evaluations:
            if e.candidate_id == candidate_id:
                return e
        return None


class ConfidenceBand(str, Enum):
    """Deliberately coarse.

    A three-band signal that users read correctly beats a two-decimal score that implies a precision the
    underlying judge does not have (RESEARCH 5.1).
    """

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"

    @classmethod
    def from_score(cls, score: float, *, deterministic_ok: bool, risk_high: bool) -> ConfidenceBand:
        if not deterministic_ok:
            return cls.LOW
        if risk_high:
            # High-risk outputs never report high confidence from automated signals alone.
            return cls.MODERATE if score >= 0.75 else cls.LOW
        if score >= 0.8:
            return cls.HIGH
        if score >= 0.55:
            return cls.MODERATE
        return cls.LOW
