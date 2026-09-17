"""Prompt optimiser: technique selection and prompt rendering.

The governing rule (spec rule 6, PRODUCT §2): **do not blindly add all techniques.** Each technique must be
justified by a signal in the `ExecutionSpec`. Every application records the trigger that justified it, so the
A/B harness can later attribute outcomes to decisions and tell us when our own optimiser is not helping
(RESEARCH 2.2).

Prompt assembly is ordered stable-prefix-first — role, then durable context, then task instructions, then the
volatile user input — so the provider's prompt cache covers the largest possible prefix (RESEARCH 7.2: a cache
read costs 0.1x input and a 5-minute write 1.25x, so it pays back after a single read).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.pipeline.taxonomy import get_profile
from app.pipeline.types import (
    Ambiguity,
    AppliedTechnique,
    CompiledPrompt,
    Complexity,
    ExecutionSpec,
    OptimizationDecision,
    OutputFormat,
    Provenance,
    ReasoningDepth,
    RiskLevel,
    Technique,
)

FORMAT_INSTRUCTIONS: dict[OutputFormat, str] = {
    OutputFormat.JSON: "Respond with valid JSON only. No prose before or after the JSON.",
    OutputFormat.MARKDOWN: "Format the response in Markdown with clear headings where it aids scanning.",
    OutputFormat.CODE: (
        "Provide complete, runnable code in fenced code blocks with the language annotated. "
        "Include imports. Do not abbreviate with placeholders like '# ... rest of code'."
    ),
    OutputFormat.TABLE: "Present the result as a Markdown table.",
    OutputFormat.LIST: "Present the result as a concise list.",
    OutputFormat.EMAIL: "Write it as a complete email including a subject line.",
    OutputFormat.PROSE: "Write in prose. Do not use headings or bullet points.",
    OutputFormat.DOCUMENT: "Produce a complete, well-structured document ready to use as-is.",
}

ROLE_TEMPLATES: dict[str, str] = {
    "coding": "You are a senior software engineer writing production code.",
    "debugging": "You are a senior engineer diagnosing a defect from evidence.",
    "software_architecture": "You are a principal engineer designing systems for scale and operability.",
    "research": "You are a research analyst who distinguishes evidence from inference.",
    "writing": "You are a professional writer.",
    "marketing": "You are a marketing strategist who writes specific, non-generic copy.",
    "seo": "You are an SEO specialist.",
    "career": "You are a career coach and professional resume writer.",
    "interview_prep": "You are an experienced interviewer in the relevant field.",
    "business_analysis": "You are a business analyst who states assumptions explicitly.",
    "data_analysis": "You are a data analyst who does not infer beyond the data provided.",
    "document_analysis": "You are an analyst extracting facts from source documents.",
    "mathematics": "You are a mathematician who verifies each step.",
    "planning": "You are a planner who produces concrete, sequenced, actionable steps.",
    "education": "You are a teacher who explains clearly without condescension.",
    "creative": "You are a creative writer.",
}


class PromptOptimizer:
    """Selects techniques from spec signals and renders the compiled prompt."""

    def optimize(self, spec: ExecutionSpec, *, project_context: str = "") -> CompiledPrompt:
        decision = OptimizationDecision()
        profile = get_profile(spec.task_type)

        def apply(technique: Technique, reason: str, trigger: str) -> bool:
            """Apply a technique only if the task profile considers it useful for this kind of work."""
            if technique not in profile.helpful_techniques and technique not in _ALWAYS_ELIGIBLE:
                decision.skipped.append(f"{technique.value}: not useful for {spec.task_type.value}")
                return False
            decision.applied.append(
                AppliedTechnique(technique=technique, reason=reason, trigger=trigger)
            )
            return True

        # ---- decide -------------------------------------------------------------------
        # A minimal request gets a minimal prompt. This branch is the product principle made executable:
        # a trivial, unambiguous, low-risk request is passed through almost untouched.
        minimal = self._is_minimal(spec)
        if minimal:
            decision.rationale = (
                "Request is specific, low-ambiguity and low-risk. Passing through with minimal "
                "transformation — additional instructions would add cost without improving the answer."
            )
            decision.skipped.append("all_elaboration: request already sufficient")
            return self._render(spec, decision, project_context="", minimal=True)

        use_role = (
            profile.benefits_from_role
            and spec.complexity.rank >= Complexity.LOW.rank
            and apply(
                Technique.ROLE_ASSIGNMENT,
                "The task benefits from domain expertise framing.",
                f"task_type={spec.task_type.value}",
            )
        )

        apply(
            Technique.EXPLICIT_OBJECTIVE,
            "The objective was restated so the model optimises for the outcome, not the phrasing.",
            "always for non-minimal requests",
        )

        use_format = False
        if spec.output_schema is not None:
            use_format = apply(
                Technique.OUTPUT_SCHEMA,
                "A strict output schema was required, so it is stated and will be validated.",
                "output_schema present",
            )
        elif spec.provenance_of("output_format") is not Provenance.USER:
            use_format = apply(
                Technique.OUTPUT_FORMAT_SPEC,
                "No output format was specified, so a suitable one was chosen for this task.",
                "output_format not user-specified",
            )
        else:
            use_format = apply(
                Technique.OUTPUT_FORMAT_SPEC,
                "The format you asked for was made explicit in the instructions.",
                "output_format user-specified",
            )

        use_audience = bool(spec.audience) and apply(
            Technique.AUDIENCE_SPEC,
            f"Writing for '{spec.audience}' changes the register and depth of the answer.",
            "audience known",
        )
        use_tone = bool(spec.tone) and apply(
            Technique.TONE_SPEC, f"Tone set to '{spec.tone}'.", "tone known"
        )
        use_constraints = bool(spec.constraints) and apply(
            Technique.CONSTRAINT_ENUMERATION,
            "Constraints were listed explicitly so they are not overlooked.",
            f"{len(spec.constraints)} constraints detected",
        )
        use_context = bool(project_context) and apply(
            Technique.CONTEXT_INJECTION,
            "Project context was included so the answer fits your situation.",
            "project context available",
        )

        # Decomposition earns its tokens only on genuinely large work.
        use_decomposition = spec.complexity.rank >= Complexity.HIGH.rank and apply(
            Technique.TASK_DECOMPOSITION,
            "The task is large, so the model is asked to work through it in parts.",
            f"complexity={spec.complexity.value}",
        )

        use_verification = (
            profile.needs_verification and spec.complexity.rank >= Complexity.MEDIUM.rank
        ) and apply(
            Technique.VERIFICATION_INSTRUCTION,
            "The model is asked to check its own work before answering.",
            f"task needs verification, complexity={spec.complexity.value}",
        )

        use_sources = (spec.needs_current_info or profile.needs_verification) and apply(
            Technique.SOURCE_REQUIREMENT,
            "The answer must indicate what it is based on.",
            "grounding matters for this task",
        )

        use_tools = spec.needs_tools and apply(
            Technique.TOOL_DECLARATION,
            "The request depends on information the model cannot know on its own.",
            "needs_tools=true",
        )

        # Ambiguity is resolved by stating assumptions rather than by interrogating the user. Asking
        # questions is a worse product than answering well and showing the assumptions.
        use_ambiguity = spec.ambiguity.rank >= Ambiguity.MEDIUM.rank and apply(
            Technique.AMBIGUITY_RESOLUTION,
            "The request left some things open, so the model states its assumptions instead of guessing "
            "silently.",
            f"ambiguity={spec.ambiguity.value}",
        )

        use_uncertainty = (
            spec.risk_level.rank >= RiskLevel.MEDIUM.rank or spec.needs_current_info
        ) and apply(
            Technique.UNCERTAINTY_DISCLOSURE,
            "This topic carries real-world consequences, so the answer must flag what it is unsure about.",
            f"risk={spec.risk_level.value}",
        )

        use_examples = profile.benefits_from_examples and spec.output_schema is not None and apply(
            Technique.FEW_SHOT,
            "A worked example makes the expected output shape unambiguous.",
            "task benefits from examples and a schema exists",
        )

        decision.rationale = self._rationale(spec, decision)

        return self._render(
            spec,
            decision,
            project_context=project_context,
            minimal=False,
            flags=_RenderFlags(
                role=use_role, format=use_format, audience=use_audience, tone=use_tone,
                constraints=use_constraints, context=use_context, decomposition=use_decomposition,
                verification=use_verification, sources=use_sources, tools=use_tools,
                ambiguity=use_ambiguity, uncertainty=use_uncertainty, examples=use_examples,
            ),
        )

    # -- helpers -------------------------------------------------------------------------
    def _is_minimal(self, spec: ExecutionSpec) -> bool:
        """Should this request pass through essentially untouched?

        Requires: low complexity, low ambiguity, low risk, no schema, no tool need. Translation and
        classification qualify readily — the request already contains everything needed.
        """
        return (
            spec.complexity.rank <= Complexity.LOW.rank
            and spec.ambiguity.rank <= Ambiguity.LOW.rank
            and spec.risk_level is RiskLevel.LOW
            and spec.output_schema is None
            and not spec.needs_tools
            and not spec.constraints
        )

    def _rationale(self, spec: ExecutionSpec, decision: OptimizationDecision) -> str:
        n = len(decision.applied)
        return (
            f"Classified as {spec.task_type.value} with {spec.complexity.value} complexity and "
            f"{spec.ambiguity.value} ambiguity. Applied {n} technique{'s' if n != 1 else ''} "
            f"targeted at the gaps found; skipped {len(decision.skipped)} that would not have helped."
        )

    def _render(
        self,
        spec: ExecutionSpec,
        decision: OptimizationDecision,
        *,
        project_context: str,
        minimal: bool,
        flags: _RenderFlags | None = None,
    ) -> CompiledPrompt:
        if minimal:
            return CompiledPrompt(
                system="", user=spec.raw_input.strip(), cacheable_prefix_chars=0, decision=decision
            )

        f = flags or _RenderFlags()

        # ---- system: the stable, cacheable prefix ------------------------------------
        system_parts: list[str] = []
        if f.role:
            system_parts.append(
                ROLE_TEMPLATES.get(spec.task_type.value, "You are an expert assistant.")
            )
        if f.context and project_context:
            system_parts.append(f"## Context\n{project_context.strip()}")
        if f.verification:
            system_parts.append(
                "Before answering, check your work for errors, omissions and internal contradictions. "
                "Correct them silently; present only the corrected result."
            )
        if f.uncertainty:
            system_parts.append(
                "State clearly what you are uncertain about and what you are assuming. Do not present "
                "uncertain claims as settled facts."
            )
        if f.sources:
            system_parts.append(
                "Indicate what each substantive claim rests on. If you do not know something, say so "
                "rather than filling the gap."
            )
        if f.tools:
            system_parts.append(
                "This request may depend on information you cannot verify from memory (current events, "
                "live data, or the user's private files). Where that is the case, say so explicitly "
                "instead of inventing specifics."
            )
        system = "\n\n".join(system_parts)

        # ---- user: the volatile suffix ------------------------------------------------
        user_parts: list[str] = []
        # Only state the objective when it adds something. On the heuristic path `goal` is a truncation of
        # the raw input, so emitting both would duplicate tokens and tell the model nothing.
        goal = spec.goal.strip()
        raw = spec.raw_input.strip()
        objective_is_informative = bool(goal) and _normalise(goal) != _normalise(raw)
        if (
            Technique.EXPLICIT_OBJECTIVE in {a.technique for a in decision.applied}
            and objective_is_informative
        ):
            user_parts.append(f"## Objective\n{goal}")

        user_parts.append(f"## Request\n{raw}")

        requirements: list[str] = []
        if f.audience and spec.audience:
            requirements.append(f"Audience: {spec.audience}")
        if f.tone and spec.tone:
            requirements.append(f"Tone: {spec.tone}")
        if f.constraints:
            requirements.extend(f"{c.kind}: {c.value}" for c in spec.constraints)
        if f.format and spec.output_schema is None:
            requirements.append(FORMAT_INSTRUCTIONS.get(spec.output_format, ""))
        if f.ambiguity and spec.missing_information:
            requirements.append(
                "The request does not specify: "
                + "; ".join(spec.missing_information[:5])
                + ". Choose sensible defaults, state them in one short line at the start, and proceed."
            )
        elif f.ambiguity:
            requirements.append(
                "Where the request is open to interpretation, choose a sensible reading, state it in one "
                "short line, and proceed."
            )
        if f.decomposition:
            requirements.append(
                "Work through this in clearly separated parts rather than as one block of text."
            )
        requirements = [r for r in requirements if r]
        if requirements:
            user_parts.append(
                "## Requirements\n" + "\n".join(f"- {r}" for r in requirements)
            )

        if spec.output_schema is not None:
            user_parts.append(
                "## Output schema\nReturn JSON matching exactly this schema:\n```json\n"
                + json.dumps(spec.output_schema, indent=2)
                + "\n```"
            )
            if f.examples:
                user_parts.append(
                    "Return only the JSON object. Do not wrap it in prose or explanation."
                )

        if spec.reasoning_depth is ReasoningDepth.DEEP and not f.decomposition:
            user_parts.append(
                "Think carefully about this before answering, but present only your conclusions."
            )

        user = "\n\n".join(user_parts)

        return CompiledPrompt(
            system=system,
            user=user,
            cacheable_prefix_chars=len(system),
            output_schema=spec.output_schema,
            decision=decision,
        )


def _normalise(text: str) -> str:
    """Whitespace/case-insensitive comparison, used to detect a restated-but-identical objective."""
    return " ".join(text.lower().split()).rstrip(".")


@dataclass(slots=True)
class _RenderFlags:
    """Which techniques were selected, for the renderer to act on.

    A real dataclass rather than `**kw`/`setattr`: every field is checked by mypy, so a typo in a caller
    (or a renamed flag) is caught at review time instead of silently rendering an incomplete prompt.
    """

    role: bool = False
    format: bool = False
    audience: bool = False
    tone: bool = False
    constraints: bool = False
    context: bool = False
    decomposition: bool = False
    verification: bool = False
    sources: bool = False
    tools: bool = False
    ambiguity: bool = False
    uncertainty: bool = False
    examples: bool = False


# Techniques that apply regardless of task profile, because they respond to request-level facts
# (risk, ambiguity, schema) rather than to the kind of work.
_ALWAYS_ELIGIBLE = {
    Technique.EXPLICIT_OBJECTIVE,
    Technique.OUTPUT_SCHEMA,
    Technique.AMBIGUITY_RESOLUTION,
    Technique.UNCERTAINTY_DISCLOSURE,
    Technique.CONTEXT_INJECTION,
    Technique.OUTPUT_FORMAT_SPEC,
}
