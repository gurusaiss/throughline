"""Evaluation engine tests.

The rule under test throughout: **deterministic facts outrank model opinion.** A judge can be talked into
liking a response that fails its schema; a validator cannot.
"""

from __future__ import annotations

import pytest

from app.evaluation import aggregate, deterministic
from app.evaluation.rubric import build_rubric
from app.evaluation.types import (
    CandidateEvaluation,
    CheckStatus,
    ConfidenceBand,
    Criterion,
    CriterionScore,
    EvaluationStatus,
    JudgeMitigations,
    ScoreSource,
)
from app.pipeline.types import (
    Candidate,
    Complexity,
    Constraint,
    ExecutionSpec,
    OutputFormat,
    RiskLevel,
    TaskType,
)


def make_candidate(text: str, cid: str = "c1", **kw) -> Candidate:
    return Candidate(id=cid, model_id="scripted-standard", provider="scripted", text=text, **kw)


def make_spec(**kw) -> ExecutionSpec:
    return ExecutionSpec(raw_input=kw.pop("raw_input", "test request"), **kw)


class TestDeterministicChecks:
    def test_detects_invalid_json(self) -> None:
        spec = make_spec(output_schema={"type": "object", "properties": {"a": {"type": "string"}}})
        checks = deterministic.run_all(make_candidate("{not json"), spec)
        assert any(c.name == "json_valid" and c.status is CheckStatus.FAIL for c in checks)

    def test_detects_schema_violation(self) -> None:
        """Valid JSON that does not match the schema must still fail (ADR-010)."""
        spec = make_spec(
            output_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                "required": ["name", "age"],
            }
        )
        checks = deterministic.run_all(make_candidate('{"name": "Ada"}'), spec)
        schema_check = next(c for c in checks if c.name == "schema_compliance")
        assert schema_check.status is CheckStatus.FAIL
        assert "age" in schema_check.detail

    def test_accepts_valid_schema_output(self) -> None:
        spec = make_spec(
            output_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        )
        checks = deterministic.run_all(make_candidate('{"name": "Ada"}'), spec)
        assert all(c.status is not CheckStatus.FAIL for c in checks)

    def test_extracts_json_from_fenced_block(self) -> None:
        """Tolerant extraction lets the schema check still run on a nearly-right answer."""
        spec = make_spec(
            output_schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
        )
        text = 'Here you go:\n```json\n{"n": 5}\n```\nHope that helps.'
        checks = deterministic.run_all(make_candidate(text), spec)
        assert next(c for c in checks if c.name == "schema_compliance").status is CheckStatus.PASS

    @pytest.mark.parametrize(
        ("constraint", "text", "should_pass"),
        [
            ("under 10 words", "one two three", True),
            ("under 3 words", "one two three four five", False),
            ("at least 5 words", "one two three four five six", True),
            ("at least 20 words", "too short", False),
        ],
    )
    def test_enforces_length_constraints(
        self, constraint: str, text: str, should_pass: bool
    ) -> None:
        spec = make_spec(constraints=[Constraint(kind="length", value=constraint)])
        checks = deterministic.run_all(make_candidate(text), spec)
        length_checks = [c for c in checks if c.name.startswith("length_")]
        assert length_checks
        assert (length_checks[0].status is CheckStatus.PASS) is should_pass

    def test_unverifiable_constraint_is_skipped_not_passed(self) -> None:
        """A check that always passes is worse than no check: it looks like evidence."""
        spec = make_spec(constraints=[Constraint(kind="tone", value="make it persuasive")])
        checks = deterministic.run_all(make_candidate("some text"), spec)
        skipped = [c for c in checks if c.name == "constraint_not_machine_checkable"]
        assert skipped and skipped[0].status is CheckStatus.SKIPPED

    def test_detects_placeholder_code(self) -> None:
        spec = make_spec(task_type=TaskType.CODING, output_format=OutputFormat.CODE)
        text = "```python\ndef main():\n    # ... rest of implementation\n```"
        checks = deterministic.run_all(make_candidate(text), spec)
        assert next(c for c in checks if c.name == "no_placeholder_content").status is CheckStatus.FAIL

    def test_prose_request_rejects_headings(self) -> None:
        spec = make_spec(output_format=OutputFormat.PROSE)
        checks = deterministic.run_all(make_candidate("# Heading\n\nSome text"), spec)
        assert next(c for c in checks if c.name == "is_prose").status is CheckStatus.FAIL

    def test_empty_response_fails(self) -> None:
        checks = deterministic.run_all(make_candidate("   "), make_spec())
        assert next(c for c in checks if c.name == "non_empty").status is CheckStatus.FAIL


class TestAggregation:
    def test_deterministically_invalid_candidate_never_wins(self) -> None:
        """The single most important rule in the evaluation engine.

        A judge loves the broken candidate; it must still lose to the valid one.
        """
        spec = make_spec(
            output_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        )
        good = make_candidate('{"name": "Ada"}', cid="good")
        bad = make_candidate("definitely not json", cid="bad")

        det = {c.id: deterministic.run_all(c, spec) for c in (good, bad)}
        judged = [
            CandidateEvaluation(
                candidate_id="bad",
                scores=[
                    CriterionScore(criterion=Criterion.CORRECTNESS, score=1.0, source=ScoreSource.JUDGE),
                    CriterionScore(criterion=Criterion.CLARITY, score=1.0, source=ScoreSource.JUDGE),
                ],
            ),
            CandidateEvaluation(
                candidate_id="good",
                scores=[
                    CriterionScore(criterion=Criterion.CORRECTNESS, score=0.25, source=ScoreSource.JUDGE),
                    CriterionScore(criterion=Criterion.CLARITY, score=0.25, source=ScoreSource.JUDGE),
                ],
            ),
        ]
        report = aggregate.build_report(
            [good, bad], det, judged, spec, status=EvaluationStatus.COMPLETE
        )
        assert report.selected_candidate_id == "good"

    def test_deterministic_format_score_overrides_judge(self) -> None:
        spec = make_spec(
            output_schema={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
        )
        broken = make_candidate("not json", cid="x")
        judged = [
            CandidateEvaluation(
                candidate_id="x",
                scores=[
                    CriterionScore(
                        criterion=Criterion.FORMAT_COMPLIANCE, score=1.0, source=ScoreSource.JUDGE
                    )
                ],
            )
        ]
        merged = aggregate.merge_evaluations(
            [broken], {broken.id: deterministic.run_all(broken, spec)}, judged, spec
        )
        fmt = next(s for s in merged[0].scores if s.criterion is Criterion.FORMAT_COMPLIANCE)
        assert fmt.source is ScoreSource.DETERMINISTIC
        assert fmt.score == 0.0

    def test_shorter_answer_wins_a_tie(self) -> None:
        """Counter-pressure against verbosity bias (RESEARCH 5.1)."""
        spec = make_spec()
        short = make_candidate("Concise answer.", cid="short")
        padded = make_candidate("Concise answer. " + ("filler " * 200), cid="padded")
        equal_scores = [
            CandidateEvaluation(
                candidate_id=cid,
                scores=[
                    CriterionScore(criterion=Criterion.RELEVANCE, score=0.75, source=ScoreSource.JUDGE)
                ],
            )
            for cid in ("short", "padded")
        ]
        report = aggregate.build_report(
            [padded, short],
            {c.id: deterministic.run_all(c, spec) for c in (short, padded)},
            equal_scores,
            spec,
            status=EvaluationStatus.COMPLETE,
        )
        assert report.selected_candidate_id == "short"

    def test_all_candidates_failing_still_returns_one(self) -> None:
        """Least-broken beats nothing: the user gets an answer plus the caveat."""
        spec = make_spec(output_schema={"type": "object", "required": ["z"], "properties": {}})
        a, b = make_candidate("nope", cid="a"), make_candidate("also nope", cid="b")
        report = aggregate.build_report(
            [a, b],
            {c.id: deterministic.run_all(c, spec) for c in (a, b)},
            None,
            spec,
            status=EvaluationStatus.DETERMINISTIC_ONLY,
        )
        assert report.selected_candidate_id in ("a", "b")
        assert "failed fewest" in report.selection_reason

    def test_model_agreement_is_none_for_single_candidate(self) -> None:
        assert aggregate.model_agreement([make_candidate("x")]) is None

    def test_model_agreement_detects_convergence(self) -> None:
        same = [make_candidate("identical text", cid="a"), make_candidate("identical text", cid="b")]
        different = [make_candidate("aaaa", cid="a"), make_candidate("zzzz bbbb cccc", cid="b")]
        assert aggregate.model_agreement(same) > aggregate.model_agreement(different)


class TestConfidenceBands:
    def test_high_risk_is_capped_below_high(self) -> None:
        assert (
            ConfidenceBand.from_score(0.95, deterministic_ok=True, risk_high=True)
            is ConfidenceBand.MODERATE
        )

    def test_failed_checks_force_low_confidence(self) -> None:
        assert (
            ConfidenceBand.from_score(0.95, deterministic_ok=False, risk_high=False)
            is ConfidenceBand.LOW
        )

    def test_deterministic_only_evaluation_cannot_report_high(self) -> None:
        spec = make_spec()
        cand = make_candidate("fine")
        report = aggregate.build_report(
            [cand],
            {cand.id: [
                deterministic.DeterministicCheck(
                    name="fmt", status=CheckStatus.PASS, criterion=Criterion.FORMAT_COMPLIANCE
                )
            ]},
            None,
            spec,
            status=EvaluationStatus.DETERMINISTIC_ONLY,
        )
        assert aggregate.confidence_band(report, spec) is not ConfidenceBand.HIGH


class TestRubric:
    def test_high_risk_request_adds_safety_criteria(self) -> None:
        rubric = build_rubric(make_spec(risk_level=RiskLevel.HIGH))
        assert Criterion.SAFETY in rubric.criteria

    def test_structured_output_adds_format_criterion(self) -> None:
        rubric = build_rubric(make_spec(output_schema={"type": "object"}))
        assert Criterion.FORMAT_COMPLIANCE in rubric.criteria

    def test_rubric_is_bounded(self) -> None:
        """Too many criteria dilutes judge attention and costs tokens for noise."""
        rubric = build_rubric(
            make_spec(
                risk_level=RiskLevel.HIGH,
                needs_current_info=True,
                output_schema={"type": "object"},
                constraints=[Constraint(kind="length", value="under 10 words")],
                complexity=Complexity.VERY_HIGH,
            )
        )
        assert len(rubric.criteria) <= 5


class TestJudgeMitigations:
    def test_self_preference_risk_is_recorded(self) -> None:
        """An un-mitigated score must be visibly un-mitigated (RESEARCH 5.1)."""
        m = JudgeMitigations(judge_family="claude", generator_families=["claude"], self_preference_risk=True)
        assert m.self_preference_risk

    def test_permutation_is_recorded_for_audit(self) -> None:
        m = JudgeMitigations(order_randomized=True, permutation=[2, 0, 1])
        assert m.permutation == [2, 0, 1]
