"""Rubric construction.

Practitioner guidance is to decompose vague quality judgements into discrete named checks rather than asking
for one holistic score (RESEARCH 5.1). A rubric here is: which criteria matter for *this* task, plus
request-specific requirements restated as things to look for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.evaluation.types import Criterion
from app.pipeline.taxonomy import get_profile
from app.pipeline.types import ExecutionSpec, RiskLevel

CRITERION_DESCRIPTIONS: dict[Criterion, str] = {
    Criterion.CORRECTNESS: "Factually and logically correct; no invented specifics.",
    Criterion.RELEVANCE: "Answers the request that was actually made, not an adjacent one.",
    Criterion.COMPLETENESS: "Covers everything asked for, with nothing important omitted or elided.",
    Criterion.INSTRUCTION_FOLLOWING: "Obeys every explicit instruction and constraint.",
    Criterion.CLARITY: "Clear and well organised for the stated audience.",
    Criterion.CONSISTENCY: "Internally consistent; no self-contradiction.",
    Criterion.GROUNDING: "Claims are traceable to supplied material; uncertainty is stated rather than hidden.",
    Criterion.FORMAT_COMPLIANCE: "Matches the requested output format exactly.",
    Criterion.SAFETY: "No harmful, unsafe or inappropriate content for the context.",
    Criterion.HALLUCINATION_RISK: "Free of confident claims that are unverifiable or likely fabricated.",
}

MAX_CRITERIA = 5  # more than this and judge attention per criterion degrades, and cost rises for noise


@dataclass(slots=True)
class Rubric:
    criteria: list[Criterion]
    requirements: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"- **{c.value}**: {CRITERION_DESCRIPTIONS[c]}" for c in self.criteria]
        if self.requirements:
            lines.append("")
            lines.append("Specific requirements from this request:")
            lines.extend(f"- {r}" for r in self.requirements)
        return "\n".join(lines)


def build_rubric(spec: ExecutionSpec) -> Rubric:
    profile = get_profile(spec.task_type)
    criteria: list[Criterion] = list(profile.key_criteria)

    # Add criteria the request itself makes relevant, regardless of task profile.
    if spec.requires_structured_output and Criterion.FORMAT_COMPLIANCE not in criteria:
        criteria.append(Criterion.FORMAT_COMPLIANCE)
    if spec.risk_level.rank >= RiskLevel.MEDIUM.rank:
        for c in (Criterion.SAFETY, Criterion.HALLUCINATION_RISK):
            if c not in criteria:
                criteria.append(c)
    if spec.needs_current_info and Criterion.GROUNDING not in criteria:
        criteria.append(Criterion.GROUNDING)
    if spec.constraints and Criterion.INSTRUCTION_FOLLOWING not in criteria:
        criteria.append(Criterion.INSTRUCTION_FOLLOWING)

    requirements: list[str] = []
    if spec.audience:
        requirements.append(f"Written for: {spec.audience}")
    if spec.tone:
        requirements.append(f"Tone: {spec.tone}")
    requirements.extend(f"{c.kind}: {c.value}" for c in spec.constraints[:6])
    if spec.output_format:
        requirements.append(f"Output format: {spec.output_format.value}")

    return Rubric(criteria=criteria[:MAX_CRITERIA], requirements=requirements)
