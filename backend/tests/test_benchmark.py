"""Benchmark regression tests.

The policy benchmark runs in CI as a gate: if a change makes the orchestrator spend more calls on a
trivial request, or stop detecting a high-risk topic, the build fails.

It also asserts the harness's own honesty property — that it refuses to emit a quality figure when only
the offline double is configured (rules 47, 60).
"""

from __future__ import annotations

from app.benchmark import dataset
from app.benchmark.runner import (
    evaluate_case_output,
    run_policy_benchmark,
    run_quality_benchmark,
)


class TestDataset:
    def test_covers_the_required_scenario_types(self) -> None:
        """Rule 47 lists the scenario types a credible benchmark must include."""
        categories = {c.category for c in dataset.CASES}
        required = {
            "simple_question", "coding", "debugging", "writing", "research", "summarization",
            "structured_output", "ambiguous", "high_risk", "current_information", "document",
            "adversarial",
        }
        assert required.issubset(categories), f"missing: {required - categories}"

    def test_has_enough_cases_to_be_meaningful(self) -> None:
        assert len(dataset.CASES) >= 20

    def test_case_ids_are_unique(self) -> None:
        ids = [c.id for c in dataset.CASES]
        assert len(ids) == len(set(ids))

    def test_contains_no_recorded_scores(self) -> None:
        """The dataset holds inputs and expectations only.

        If it ever grows a 'baseline_score' field, someone is about to publish a number nobody measured.
        """
        for case in dataset.CASES:
            assert not hasattr(case, "baseline_score")
            assert not hasattr(case, "expected_score")

    def test_subjective_cases_are_marked_not_faked(self) -> None:
        """A case with no machine-checkable expectation must say so rather than invent one."""
        for case in dataset.CASES:
            has_checkable = bool(
                case.output_schema or case.must_include or case.max_words or case.max_chars
            )
            assert has_checkable or case.subjective_only, (
                f"{case.id} has no checkable expectation and is not marked subjective_only"
            )

    def test_adversarial_cases_exist_for_injection_defence(self) -> None:
        adversarial = [c for c in dataset.CASES if c.category == "adversarial"]
        assert len(adversarial) >= 2


class TestPolicyBenchmark:
    async def test_policy_conformance_passes(self, orchestrator) -> None:  # noqa: ANN001
        """CI gate: the orchestration policy must behave as designed.

        Runs entirely offline. A failure here means a real regression in mode selection, risk detection,
        call budgeting or prompt minimality.
        """
        report = await run_policy_benchmark(orchestrator)
        assert report.total > 0
        assert not report.failed, "Policy regressions:\n" + "\n".join(str(f) for f in report.failed)

    async def test_trivial_cases_stay_within_their_call_budget(self, orchestrator) -> None:  # noqa: ANN001
        """The product's central claim, asserted over the dataset rather than one example."""
        from app.pipeline.orchestrator import ExecutionRequest

        trivial = [c for c in dataset.CASES if c.expect_max_llm_calls == 1]
        assert trivial
        for case in trivial:
            result = await orchestrator.run(ExecutionRequest(input=case.input))
            assert result.trace is not None
            assert result.trace.llm_call_count() == 1, f"{case.id} used more than one call"


class TestQualityBenchmarkHonesty:
    async def test_refuses_to_score_without_a_real_provider(self, orchestrator) -> None:  # noqa: ANN001
        """The offline double returns echoes.

        Scoring them would produce a number that looks like a quality measurement and is not one. The
        harness must decline rather than report it.
        """
        report = await run_quality_benchmark(orchestrator, provider_backed=False)
        assert report.provider_backed is False
        assert report.arms == {}
        assert "NOT RUN" in report.summary()
        assert "meaningless" in report.summary()

    def test_empty_arm_reports_none_not_zero(self) -> None:
        """An unrun arm is 'no data', not '0%'. Reporting 0.0 would be a fabricated result."""
        from app.benchmark.runner import ArmResult

        assert ArmResult("raw").pass_rate is None
        assert ArmResult("raw").case_pass_rate is None


class TestCaseScoring:
    def test_must_include_is_checked_case_insensitively(self) -> None:
        case = dataset.BenchmarkCase(id="t", input="x", category="c", must_include=["Paris"])
        assert evaluate_case_output(case, "The capital is paris.") == [True]
        assert evaluate_case_output(case, "The capital is Lyon.") == [False]

    def test_word_limit_is_enforced(self) -> None:
        case = dataset.BenchmarkCase(id="t", input="x", category="c", max_words=3)
        assert evaluate_case_output(case, "one two three") == [True]
        assert evaluate_case_output(case, "one two three four") == [False]

    def test_char_limit_is_enforced(self) -> None:
        case = dataset.BenchmarkCase(id="t", input="x", category="c", max_chars=5)
        assert evaluate_case_output(case, "abcde") == [True]
        assert evaluate_case_output(case, "abcdef") == [False]

    def test_case_with_no_expectations_scores_nothing(self) -> None:
        case = dataset.BenchmarkCase(id="t", input="x", category="c", subjective_only=True)
        assert evaluate_case_output(case, "anything at all") == []
