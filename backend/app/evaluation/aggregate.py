"""Score aggregation and candidate selection.

The single most important rule in this module: **a deterministically invalid candidate never wins.**

A judge can be talked into liking a response that fails its schema, exceeds its word limit, or contains
placeholder code. A validator cannot. Where the two disagree, the machine-checkable fact wins — otherwise
the deterministic checks are decoration.

Selection is also explainable: `selection_reason` is user-facing copy, not a debug string.
"""

from __future__ import annotations

import difflib

from app.evaluation.types import (
    CandidateEvaluation,
    ConfidenceBand,
    Criterion,
    CriterionScore,
    DeterministicCheck,
    EvaluationReport,
    EvaluationStatus,
    JudgeMitigations,
    ScoreSource,
)
from app.pipeline.types import Candidate, ExecutionSpec, RiskLevel

# Deterministic failures are weighted heavily rather than made absolutely fatal, so that when *every*
# candidate fails something we still return the least-broken one instead of nothing.
DETERMINISTIC_FAILURE_PENALTY = 0.5


def merge_evaluations(
    candidates: list[Candidate],
    deterministic: dict[str, list[DeterministicCheck]],
    judged: list[CandidateEvaluation] | None,
    spec: ExecutionSpec,
) -> list[CandidateEvaluation]:
    """Combine deterministic checks and judge scores into one evaluation per candidate."""
    judged_by_id = {e.candidate_id: e for e in (judged or [])}
    merged: list[CandidateEvaluation] = []

    for cand in candidates:
        checks = deterministic.get(cand.id, [])
        judge_eval = judged_by_id.get(cand.id)
        scores: list[CriterionScore] = list(judge_eval.scores) if judge_eval else []

        # Deterministic format compliance overrides any judge opinion on the same criterion: it is a fact,
        # not an assessment.
        format_checks = [c for c in checks if c.criterion is Criterion.FORMAT_COMPLIANCE]
        if format_checks:
            passed = all(c.status.value != "fail" for c in format_checks)
            scores = [s for s in scores if s.criterion is not Criterion.FORMAT_COMPLIANCE]
            scores.append(
                CriterionScore(
                    criterion=Criterion.FORMAT_COMPLIANCE,
                    score=1.0 if passed else 0.0,
                    source=ScoreSource.DETERMINISTIC,
                    reasoning=(
                        "Verified programmatically."
                        if passed
                        else "; ".join(c.detail for c in format_checks if c.detail)[:300]
                    ),
                )
            )

        overall = _overall_score(scores, checks)
        deterministic_ok = not [c for c in checks if c.is_blocking]
        confidence = _confidence(
            overall,
            deterministic_ok=deterministic_ok,
            has_judge=bool(judge_eval),
            risk=spec.risk_level,
        )
        merged.append(
            CandidateEvaluation(
                candidate_id=cand.id,
                checks=checks,
                scores=scores,
                overall=round(overall, 4),
                confidence=round(confidence, 3),
                notes=judge_eval.notes if judge_eval else "",
            )
        )
    return merged


def _overall_score(scores: list[CriterionScore], checks: list[DeterministicCheck]) -> float:
    base = sum(s.score for s in scores) / len(scores) if scores else 0.5
    failures = len([c for c in checks if c.is_blocking])
    if failures:
        base *= DETERMINISTIC_FAILURE_PENALTY ** failures
    return max(0.0, min(1.0, base))


def _confidence(
    overall: float, *, deterministic_ok: bool, has_judge: bool, risk: RiskLevel
) -> float:
    """How much weight this evaluation deserves — distinct from how good the output is.

    A high score from a single judge on a high-risk request is not high confidence. Confidence reflects the
    *strength of evidence*, which is why it drops when the only signal is one model's opinion.
    """
    conf = 0.5
    if deterministic_ok:
        conf += 0.2
    else:
        conf -= 0.2
    if has_judge:
        conf += 0.15
    else:
        # No judge means no opinion on correctness/relevance at all, only mechanical checks.
        conf -= 0.05
    if risk is RiskLevel.HIGH:
        conf -= 0.2
    elif risk is RiskLevel.MEDIUM:
        conf -= 0.1
    # Extreme scores in either direction are more trustworthy than a middling one.
    conf += 0.1 * abs(overall - 0.5) * 2
    return max(0.0, min(1.0, conf))


def select_best(
    candidates: list[Candidate],
    evaluations: list[CandidateEvaluation],
    spec: ExecutionSpec,
) -> tuple[str | None, str]:
    """Choose the candidate to return. Returns (candidate_id, user-facing reason)."""
    usable = [c for c in candidates if c.ok]
    if not usable:
        return None, "No candidate produced a usable response."
    if len(usable) == 1:
        return usable[0].id, "Only one model produced a usable response."

    by_id = {e.candidate_id: e for e in evaluations}

    def sort_key(c: Candidate) -> tuple[int, float, float, int]:
        ev = by_id.get(c.id)
        if ev is None:
            return (0, 0.0, 0.0, -c.latency_ms)
        # Tier 1: deterministic validity. This dominates everything below it.
        valid = 1 if ev.deterministically_valid else 0
        # Tier 4 (tie-break): prefer the shorter answer. Counter-pressure against verbosity bias
        # (RESEARCH 5.1) — when two answers score equally, the padded one should not win.
        return (valid, ev.overall, ev.confidence, -len(c.text))

    ranked = sorted(usable, key=sort_key, reverse=True)
    best = ranked[0]
    best_eval = by_id.get(best.id)

    if best_eval is None:
        return best.id, "Selected the only evaluated response."

    reasons: list[str] = []
    if not best_eval.deterministically_valid:
        reasons.append("no candidate passed every automated check; this one failed fewest")
    else:
        failed_others = [c for c in ranked[1:] if not (by_id.get(c.id) or best_eval).deterministically_valid]
        if failed_others:
            reasons.append("it was the only response to pass every automated check")

    top_criteria = sorted(
        [s for s in best_eval.scores if s.source is ScoreSource.JUDGE],
        key=lambda s: s.score,
        reverse=True,
    )[:2]
    if top_criteria:
        names = " and ".join(c.criterion.value.replace("_", " ") for c in top_criteria)
        reasons.append(f"it scored highest on {names}")

    runner_up = ranked[1] if len(ranked) > 1 else None
    if runner_up is not None:
        runner_eval = by_id.get(runner_up.id)
        if runner_eval and abs(runner_eval.overall - best_eval.overall) < 0.05:
            reasons.append("the alternatives were close, so this is a narrow preference")

    reason = "Selected because " + "; ".join(reasons) + "." if reasons else "Selected on overall score."
    return best.id, reason


def model_agreement(candidates: list[Candidate]) -> float | None:
    """Rough similarity across candidate outputs.

    Convergence is a weak signal that the task was well specified — **not** evidence of correctness. Models
    sharing training data agree on the same mistakes. Surfaced as `model_agreement` and labelled as such;
    it must never be presented as accuracy.
    """
    usable = [c for c in candidates if c.ok]
    if len(usable) < 2:
        return None
    ratios: list[float] = []
    for i in range(len(usable)):
        for j in range(i + 1, len(usable)):
            ratios.append(
                difflib.SequenceMatcher(None, usable[i].text, usable[j].text).ratio()
            )
    return round(sum(ratios) / len(ratios), 3) if ratios else None


def build_report(
    candidates: list[Candidate],
    deterministic: dict[str, list[DeterministicCheck]],
    judged: list[CandidateEvaluation] | None,
    spec: ExecutionSpec,
    *,
    mitigations: JudgeMitigations | None = None,
    status: EvaluationStatus,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    error: str | None = None,
) -> EvaluationReport:
    evaluations = merge_evaluations(candidates, deterministic, judged, spec)
    selected_id, reason = select_best(candidates, evaluations, spec)
    return EvaluationReport(
        status=status,
        evaluations=evaluations,
        selected_candidate_id=selected_id,
        selection_reason=reason,
        mitigations=mitigations,
        model_agreement=model_agreement(candidates),
        cost_usd=round(cost_usd, 6),
        latency_ms=latency_ms,
        error=error,
    )


def confidence_band(
    report: EvaluationReport, spec: ExecutionSpec
) -> ConfidenceBand:
    """Coarse band for the UI. High-risk requests are capped at moderate by construction."""
    if report.selected_candidate_id is None:
        return ConfidenceBand.LOW
    ev = report.for_candidate(report.selected_candidate_id)
    if ev is None:
        return ConfidenceBand.LOW

    band = ConfidenceBand.from_score(
        ev.overall,
        deterministic_ok=ev.deterministically_valid,
        risk_high=spec.risk_level is RiskLevel.HIGH,
    )

    # Cap at MODERATE when no judge ran. Deterministic checks verify *shape*, never correctness — a
    # response can pass every format check and still be wrong. Reporting HIGH confidence because a
    # Markdown table parsed would be exactly the kind of number that "looks reasonable and is wrong"
    # (RESEARCH 5.1), and the product forbids it (PRODUCT §7).
    if report.status in (EvaluationStatus.DETERMINISTIC_ONLY, EvaluationStatus.UNAVAILABLE,
                         EvaluationStatus.SKIPPED) and band is ConfidenceBand.HIGH:
        return ConfidenceBand.MODERATE
    return band
