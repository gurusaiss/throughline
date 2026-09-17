"""Task taxonomy and per-task profiles.

Extensibility requirement (rule 5): adding a task category must not require editing the optimiser, router or
evaluator. It is done by appending a `TaskType` member and a `TaskProfile` row here. Everything downstream
reads the profile.

A profile answers: what does this kind of work *usually* need? It is a prior, not a decision — the optimiser
still consults the actual request signals before applying anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.evaluation.types import Criterion
from app.pipeline.types import (
    OutputFormat,
    ReasoningDepth,
    TaskType,
    Technique,
)
from app.providers.base import Capability


@dataclass(frozen=True, slots=True)
class TaskProfile:
    task: TaskType
    label: str
    default_format: OutputFormat = OutputFormat.MARKDOWN
    reasoning_depth: ReasoningDepth = ReasoningDepth.SHALLOW
    # Techniques that tend to help for this task. The optimiser intersects these with what the signals
    # actually justify — a profile never forces a technique on its own.
    helpful_techniques: frozenset[Technique] = field(default_factory=frozenset)
    required_capabilities: frozenset[Capability] = field(default_factory=frozenset)
    # Criteria that matter most when scoring this task's output, used to weight the rubric.
    key_criteria: tuple[Criterion, ...] = (Criterion.RELEVANCE, Criterion.INSTRUCTION_FOLLOWING)
    # Whether a domain-expert role framing is genuinely useful. For mechanical tasks (translation,
    # classification) it is noise that costs tokens and adds nothing.
    benefits_from_role: bool = True
    benefits_from_examples: bool = False
    needs_verification: bool = False
    typical_output_tokens: int = 800


_PROFILES: dict[TaskType, TaskProfile] = {}


def _register(profile: TaskProfile) -> None:
    _PROFILES[profile.task] = profile


# --------------------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------------------
_register(TaskProfile(
    task=TaskType.GENERAL_QA, label="General Q&A",
    reasoning_depth=ReasoningDepth.SHALLOW,
    helpful_techniques=frozenset({Technique.EXPLICIT_OBJECTIVE}),
    key_criteria=(Criterion.CORRECTNESS, Criterion.RELEVANCE, Criterion.CLARITY),
    benefits_from_role=False, typical_output_tokens=400,
))

_register(TaskProfile(
    task=TaskType.CODING, label="Coding",
    default_format=OutputFormat.CODE, reasoning_depth=ReasoningDepth.MODERATE,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.ROLE_ASSIGNMENT, Technique.CONSTRAINT_ENUMERATION,
        Technique.OUTPUT_FORMAT_SPEC, Technique.VERIFICATION_INSTRUCTION, Technique.TASK_DECOMPOSITION,
    }),
    key_criteria=(Criterion.CORRECTNESS, Criterion.COMPLETENESS, Criterion.INSTRUCTION_FOLLOWING),
    needs_verification=True, typical_output_tokens=2000,
))

_register(TaskProfile(
    task=TaskType.DEBUGGING, label="Debugging",
    default_format=OutputFormat.MARKDOWN, reasoning_depth=ReasoningDepth.DEEP,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.ROLE_ASSIGNMENT, Technique.CONTEXT_INJECTION,
        Technique.TASK_DECOMPOSITION, Technique.VERIFICATION_INSTRUCTION,
    }),
    key_criteria=(Criterion.CORRECTNESS, Criterion.RELEVANCE, Criterion.COMPLETENESS),
    needs_verification=True, typical_output_tokens=1200,
))

_register(TaskProfile(
    task=TaskType.SOFTWARE_ARCHITECTURE, label="Software architecture",
    reasoning_depth=ReasoningDepth.DEEP,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.ROLE_ASSIGNMENT, Technique.CONSTRAINT_ENUMERATION,
        Technique.TASK_DECOMPOSITION, Technique.OUTPUT_FORMAT_SPEC, Technique.UNCERTAINTY_DISCLOSURE,
    }),
    key_criteria=(Criterion.COMPLETENESS, Criterion.CORRECTNESS, Criterion.CONSISTENCY),
    needs_verification=True, typical_output_tokens=2500,
))

_register(TaskProfile(
    task=TaskType.RESEARCH, label="Research",
    reasoning_depth=ReasoningDepth.DEEP,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.SOURCE_REQUIREMENT, Technique.TOOL_DECLARATION,
        Technique.OUTPUT_FORMAT_SPEC, Technique.UNCERTAINTY_DISCLOSURE, Technique.VERIFICATION_INSTRUCTION,
    }),
    required_capabilities=frozenset(),
    key_criteria=(Criterion.GROUNDING, Criterion.CORRECTNESS, Criterion.COMPLETENESS),
    needs_verification=True, typical_output_tokens=2000,
))

_register(TaskProfile(
    task=TaskType.SUMMARIZATION, label="Summarization",
    reasoning_depth=ReasoningDepth.SHALLOW,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.CONSTRAINT_ENUMERATION, Technique.OUTPUT_FORMAT_SPEC,
        Technique.AUDIENCE_SPEC,
    }),
    key_criteria=(Criterion.COMPLETENESS, Criterion.RELEVANCE, Criterion.FORMAT_COMPLIANCE),
    benefits_from_role=False, typical_output_tokens=400,
))

_register(TaskProfile(
    task=TaskType.WRITING, label="Writing",
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.AUDIENCE_SPEC, Technique.TONE_SPEC,
        Technique.CONSTRAINT_ENUMERATION, Technique.OUTPUT_FORMAT_SPEC, Technique.ROLE_ASSIGNMENT,
    }),
    key_criteria=(Criterion.RELEVANCE, Criterion.CLARITY, Criterion.INSTRUCTION_FOLLOWING),
    typical_output_tokens=900,
))

_register(TaskProfile(
    task=TaskType.REWRITING, label="Rewriting",
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.CONSTRAINT_ENUMERATION, Technique.TONE_SPEC,
        Technique.AUDIENCE_SPEC,
    }),
    key_criteria=(Criterion.INSTRUCTION_FOLLOWING, Criterion.CLARITY, Criterion.CONSISTENCY),
    benefits_from_role=False, typical_output_tokens=700,
))

_register(TaskProfile(
    task=TaskType.TRANSLATION, label="Translation",
    default_format=OutputFormat.PROSE, reasoning_depth=ReasoningDepth.NONE,
    # Deliberately minimal. Translation is the canonical case where prompt elaboration adds cost and
    # subtracts nothing; the request already fully specifies the job.
    helpful_techniques=frozenset({Technique.CONSTRAINT_ENUMERATION}),
    key_criteria=(Criterion.CORRECTNESS, Criterion.INSTRUCTION_FOLLOWING),
    benefits_from_role=False, typical_output_tokens=300,
))

_register(TaskProfile(
    task=TaskType.EDUCATION, label="Education",
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.AUDIENCE_SPEC, Technique.OUTPUT_FORMAT_SPEC,
    }),
    key_criteria=(Criterion.CORRECTNESS, Criterion.CLARITY, Criterion.RELEVANCE),
    typical_output_tokens=800,
))

_register(TaskProfile(
    task=TaskType.MATHEMATICS, label="Mathematics",
    reasoning_depth=ReasoningDepth.DEEP,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.VERIFICATION_INSTRUCTION, Technique.OUTPUT_FORMAT_SPEC,
    }),
    required_capabilities=frozenset({Capability.REASONING}),
    key_criteria=(Criterion.CORRECTNESS, Criterion.CONSISTENCY),
    needs_verification=True, benefits_from_role=False, typical_output_tokens=900,
))

_register(TaskProfile(
    task=TaskType.DATA_ANALYSIS, label="Data analysis",
    default_format=OutputFormat.TABLE, reasoning_depth=ReasoningDepth.MODERATE,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.CONTEXT_INJECTION, Technique.OUTPUT_FORMAT_SPEC,
        Technique.VERIFICATION_INSTRUCTION, Technique.UNCERTAINTY_DISCLOSURE,
    }),
    key_criteria=(Criterion.CORRECTNESS, Criterion.GROUNDING, Criterion.COMPLETENESS),
    needs_verification=True, typical_output_tokens=1200,
))

_register(TaskProfile(
    task=TaskType.BUSINESS_ANALYSIS, label="Business analysis",
    reasoning_depth=ReasoningDepth.DEEP,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.ROLE_ASSIGNMENT, Technique.CONTEXT_INJECTION,
        Technique.TASK_DECOMPOSITION, Technique.UNCERTAINTY_DISCLOSURE,
    }),
    key_criteria=(Criterion.COMPLETENESS, Criterion.RELEVANCE, Criterion.GROUNDING),
    typical_output_tokens=1800,
))

_register(TaskProfile(
    task=TaskType.MARKETING, label="Marketing",
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.AUDIENCE_SPEC, Technique.TONE_SPEC,
        Technique.CONTEXT_INJECTION, Technique.OUTPUT_FORMAT_SPEC,
    }),
    key_criteria=(Criterion.RELEVANCE, Criterion.CLARITY, Criterion.INSTRUCTION_FOLLOWING),
    typical_output_tokens=1000,
))

_register(TaskProfile(
    task=TaskType.SEO, label="SEO",
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.CONSTRAINT_ENUMERATION, Technique.OUTPUT_FORMAT_SPEC,
        Technique.AUDIENCE_SPEC,
    }),
    key_criteria=(Criterion.INSTRUCTION_FOLLOWING, Criterion.FORMAT_COMPLIANCE, Criterion.RELEVANCE),
    typical_output_tokens=1000,
))

_register(TaskProfile(
    task=TaskType.CAREER, label="Resume & career",
    default_format=OutputFormat.DOCUMENT,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.ROLE_ASSIGNMENT, Technique.AUDIENCE_SPEC,
        Technique.TONE_SPEC, Technique.CONSTRAINT_ENUMERATION, Technique.OUTPUT_FORMAT_SPEC,
        Technique.AMBIGUITY_RESOLUTION,
    }),
    key_criteria=(Criterion.RELEVANCE, Criterion.COMPLETENESS, Criterion.INSTRUCTION_FOLLOWING),
    typical_output_tokens=1200,
))

_register(TaskProfile(
    task=TaskType.INTERVIEW_PREP, label="Interview preparation",
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.ROLE_ASSIGNMENT, Technique.AUDIENCE_SPEC,
        Technique.OUTPUT_FORMAT_SPEC,
    }),
    key_criteria=(Criterion.RELEVANCE, Criterion.COMPLETENESS),
    typical_output_tokens=1500,
))

_register(TaskProfile(
    task=TaskType.DOCUMENT_ANALYSIS, label="Document analysis",
    reasoning_depth=ReasoningDepth.MODERATE,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.CONTEXT_INJECTION, Technique.SOURCE_REQUIREMENT,
        Technique.OUTPUT_FORMAT_SPEC, Technique.UNCERTAINTY_DISCLOSURE,
    }),
    required_capabilities=frozenset({Capability.LONG_CONTEXT}),
    key_criteria=(Criterion.GROUNDING, Criterion.CORRECTNESS, Criterion.COMPLETENESS),
    needs_verification=True, typical_output_tokens=1200,
))

_register(TaskProfile(
    task=TaskType.IMAGE_ANALYSIS, label="Image analysis",
    helpful_techniques=frozenset({Technique.EXPLICIT_OBJECTIVE, Technique.OUTPUT_FORMAT_SPEC}),
    required_capabilities=frozenset({Capability.VISION}),
    key_criteria=(Criterion.CORRECTNESS, Criterion.GROUNDING, Criterion.RELEVANCE),
    typical_output_tokens=700,
))

_register(TaskProfile(
    task=TaskType.CREATIVE, label="Creative generation",
    default_format=OutputFormat.PROSE,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.TONE_SPEC, Technique.CONSTRAINT_ENUMERATION,
    }),
    key_criteria=(Criterion.RELEVANCE, Criterion.INSTRUCTION_FOLLOWING, Criterion.CLARITY),
    typical_output_tokens=1000,
))

_register(TaskProfile(
    task=TaskType.STRUCTURED_EXTRACTION, label="Structured extraction",
    default_format=OutputFormat.JSON, reasoning_depth=ReasoningDepth.SHALLOW,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.OUTPUT_SCHEMA, Technique.FEW_SHOT,
        Technique.CONTEXT_INJECTION,
    }),
    required_capabilities=frozenset({Capability.STRUCTURED_OUTPUT}),
    key_criteria=(Criterion.FORMAT_COMPLIANCE, Criterion.CORRECTNESS, Criterion.GROUNDING),
    benefits_from_role=False, benefits_from_examples=True, typical_output_tokens=600,
))

_register(TaskProfile(
    task=TaskType.CLASSIFICATION, label="Classification",
    default_format=OutputFormat.JSON, reasoning_depth=ReasoningDepth.NONE,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.OUTPUT_SCHEMA, Technique.FEW_SHOT,
    }),
    required_capabilities=frozenset({Capability.STRUCTURED_OUTPUT}),
    key_criteria=(Criterion.CORRECTNESS, Criterion.FORMAT_COMPLIANCE),
    benefits_from_role=False, benefits_from_examples=True, typical_output_tokens=200,
))

_register(TaskProfile(
    task=TaskType.PLANNING, label="Planning",
    reasoning_depth=ReasoningDepth.MODERATE,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.TASK_DECOMPOSITION, Technique.CONSTRAINT_ENUMERATION,
        Technique.OUTPUT_FORMAT_SPEC,
    }),
    key_criteria=(Criterion.COMPLETENESS, Criterion.RELEVANCE, Criterion.CONSISTENCY),
    typical_output_tokens=1500,
))

_register(TaskProfile(
    task=TaskType.BRAINSTORMING, label="Brainstorming",
    default_format=OutputFormat.LIST,
    helpful_techniques=frozenset({Technique.EXPLICIT_OBJECTIVE, Technique.CONSTRAINT_ENUMERATION}),
    key_criteria=(Criterion.RELEVANCE, Criterion.COMPLETENESS),
    benefits_from_role=False, typical_output_tokens=800,
))

_register(TaskProfile(
    task=TaskType.AGENTIC, label="Agentic workflow",
    reasoning_depth=ReasoningDepth.DEEP,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.TASK_DECOMPOSITION, Technique.TOOL_DECLARATION,
        Technique.VERIFICATION_INSTRUCTION, Technique.CONSTRAINT_ENUMERATION,
    }),
    required_capabilities=frozenset({Capability.TOOL_USE}),
    key_criteria=(Criterion.CORRECTNESS, Criterion.COMPLETENESS, Criterion.SAFETY),
    needs_verification=True, typical_output_tokens=2000,
))

_register(TaskProfile(
    task=TaskType.REASONING, label="Reasoning-heavy",
    reasoning_depth=ReasoningDepth.DEEP,
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.TASK_DECOMPOSITION, Technique.VERIFICATION_INSTRUCTION,
        Technique.UNCERTAINTY_DISCLOSURE,
    }),
    required_capabilities=frozenset({Capability.REASONING}),
    key_criteria=(Criterion.CORRECTNESS, Criterion.CONSISTENCY, Criterion.COMPLETENESS),
    needs_verification=True, typical_output_tokens=1500,
))

_register(TaskProfile(
    task=TaskType.CURRENT_INFO, label="Current information",
    helpful_techniques=frozenset({
        Technique.EXPLICIT_OBJECTIVE, Technique.TOOL_DECLARATION, Technique.SOURCE_REQUIREMENT,
        Technique.UNCERTAINTY_DISCLOSURE,
    }),
    required_capabilities=frozenset({Capability.WEB_SEARCH}),
    key_criteria=(Criterion.GROUNDING, Criterion.CORRECTNESS, Criterion.RELEVANCE),
    needs_verification=True, typical_output_tokens=800,
))

# Remaining categories fall back to a sane generic profile rather than raising, so a newly added TaskType
# is functional before anyone writes its profile.
_DEFAULT_PROFILE = TaskProfile(
    task=TaskType.GENERAL_QA, label="General",
    helpful_techniques=frozenset({Technique.EXPLICIT_OBJECTIVE, Technique.OUTPUT_FORMAT_SPEC}),
)


def get_profile(task: TaskType) -> TaskProfile:
    return _PROFILES.get(task, _DEFAULT_PROFILE)


def all_profiles() -> list[TaskProfile]:
    return list(_PROFILES.values())


def registered_tasks() -> set[TaskType]:
    return set(_PROFILES.keys())
