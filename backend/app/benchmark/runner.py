"""Benchmark harness.

Answers two different questions, and keeps them strictly separate because they have different evidence
requirements:

**A. Does the orchestration policy behave as designed?**
    Verifiable offline with the scripted provider, no API keys, no cost. Asserts call counts, mode
    selection, risk detection, prompt minimality. This runs in CI.

**B. Does optimisation actually improve outputs?**
    Requires real providers and real money. Runs raw vs optimised over the dataset and reports
    deterministic pass rates per arm.

The distinction is the whole point. (A) passing tells you nothing about (B), and any system that blurs them
is manufacturing impressive numbers (rules 47, 60). The harness therefore refuses to emit a quality figure
when only the offline provider is configured.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.benchmark.dataset import CASES, BenchmarkCase, pipeline_checkable
from app.evaluation import deterministic
from app.evaluation.types import CheckStatus
from app.pipeline.orchestrator import ExecutionRequest, ExecutionResult, Orchestrator
from app.pipeline.types import Candidate, OrchestrationMode


# ======================================================================================
# A. Policy conformance — offline, free, CI-safe
# ======================================================================================
@dataclass(slots=True)
class PolicyFinding:
    case_id: str
    check: str
    passed: bool
    expected: str
    actual: str

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        return f"[{mark}] {self.case_id}: {self.check} expected={self.expected} actual={self.actual}"


@dataclass
class PolicyReport:
    findings: list[PolicyFinding] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return len([f for f in self.findings if f.passed])

    @property
    def failed(self) -> list[PolicyFinding]:
        return [f for f in self.findings if not f.passed]

    @property
    def total(self) -> int:
        return len(self.findings)

    def summary(self) -> str:
        lines = [
            "Policy conformance (offline, no provider cost)",
            f"  {self.passed}/{self.total} checks passed",
        ]
        for f in self.failed:
            lines.append(f"  {f}")
        return "\n".join(lines)


async def run_policy_benchmark(orchestrator: Orchestrator) -> PolicyReport:
    """Verify the orchestration policy against the dataset. No provider quality is measured here."""
    report = PolicyReport()

    for case in pipeline_checkable():
        result = await orchestrator.run(
            ExecutionRequest(input=case.input, output_schema=case.output_schema)
        )
        _check_case(case, result, report)

    return report


def _check_case(case: BenchmarkCase, result: ExecutionResult, report: PolicyReport) -> None:
    add = report.findings.append
    signals = result.signals

    if case.expect_task is not None and signals is not None:
        # Asserted against the HEURISTIC classification, not the final spec, for the same reason as risk
        # below: it is the provider-independent part of the policy. A real intent analyser may
        # legitimately override the heuristic (that is what it is for), so asserting on the merged spec
        # would make this benchmark measure the provider rather than our policy — and with the offline
        # double it would measure nothing at all.
        add(
            PolicyFinding(
                case.id, "task_type",
                signals.likely_task is case.expect_task,
                case.expect_task.value, signals.likely_task.value,
            )
        )

    if case.expect_min_complexity is not None and signals is not None:
        add(
            PolicyFinding(
                case.id, "min_complexity",
                signals.complexity.rank >= case.expect_min_complexity.rank,
                f">={case.expect_min_complexity.value}", signals.complexity.value,
            )
        )

    if case.expect_max_complexity is not None and signals is not None:
        add(
            PolicyFinding(
                case.id, "max_complexity",
                signals.complexity.rank <= case.expect_max_complexity.rank,
                f"<={case.expect_max_complexity.value}", signals.complexity.value,
            )
        )

    if case.expect_risk is not None and signals is not None:
        # Risk is asserted against the HEURISTIC signal, because that is what runs before any model
        # sees the input and therefore what hostile input cannot argue away.
        add(
            PolicyFinding(
                case.id, "risk", signals.risk is case.expect_risk,
                case.expect_risk.value, signals.risk.value,
            )
        )

    if case.expect_mode is not None:
        add(
            PolicyFinding(
                case.id, "mode", result.mode is case.expect_mode,
                case.expect_mode.value, result.mode.value,
            )
        )

    if case.expect_max_llm_calls is not None and result.trace is not None:
        calls = result.trace.llm_call_count()
        add(
            PolicyFinding(
                case.id, "max_llm_calls", calls <= case.expect_max_llm_calls,
                f"<={case.expect_max_llm_calls}", str(calls),
            )
        )

    if case.expect_needs_current_info is not None and signals is not None:
        add(
            PolicyFinding(
                case.id, "needs_current_info",
                signals.needs_current_info == case.expect_needs_current_info,
                str(case.expect_needs_current_info), str(signals.needs_current_info),
            )
        )

    if case.expect_minimal_prompt is not None and result.prompt is not None:
        is_minimal = not result.prompt.decision.applied and not result.prompt.system
        add(
            PolicyFinding(
                case.id, "minimal_prompt", is_minimal == case.expect_minimal_prompt,
                str(case.expect_minimal_prompt), str(is_minimal),
            )
        )


# ======================================================================================
# B. Quality A/B — requires real providers
# ======================================================================================
@dataclass(slots=True)
class ArmResult:
    label: str
    checks_run: int = 0
    checks_passed: int = 0
    cases_fully_passing: int = 0
    cases_run: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    llm_calls: int = 0

    @property
    def pass_rate(self) -> float | None:
        """None rather than 0.0 when nothing ran — an empty run is not a 0% score."""
        return round(self.checks_passed / self.checks_run, 4) if self.checks_run else None

    @property
    def case_pass_rate(self) -> float | None:
        return round(self.cases_fully_passing / self.cases_run, 4) if self.cases_run else None


@dataclass
class QualityReport:
    arms: dict[str, ArmResult] = field(default_factory=dict)
    provider_backed: bool = False
    cases_evaluated: int = 0
    note: str = ""

    def summary(self) -> str:
        if not self.provider_backed:
            return (
                "Quality A/B NOT RUN.\n"
                "  Only the offline deterministic provider is configured, and it returns echoes rather "
                "than model output.\n"
                "  Any quality number produced from it would be meaningless. Configure a real provider "
                "API key to measure this."
            )
        lines = [
            f"Quality A/B over {self.cases_evaluated} deterministically-checkable cases",
            f"{'arm':<12} {'checks':>8} {'pass':>8} {'rate':>8} {'cases':>8} {'cost $':>10} {'calls':>7}",
        ]
        for name, arm in self.arms.items():
            rate = f"{arm.pass_rate:.1%}" if arm.pass_rate is not None else "n/a"
            cases = f"{arm.case_pass_rate:.1%}" if arm.case_pass_rate is not None else "n/a"
            lines.append(
                f"{name:<12} {arm.checks_run:>8} {arm.checks_passed:>8} {rate:>8} "
                f"{cases:>8} {arm.cost_usd:>10.4f} {arm.llm_calls:>7}"
            )
        if self.note:
            lines.append(f"\n{self.note}")
        return "\n".join(lines)


async def run_quality_benchmark(
    orchestrator: Orchestrator,
    *,
    provider_backed: bool,
    cases: list[BenchmarkCase] | None = None,
    concurrency: int = 4,
) -> QualityReport:
    """Raw vs optimised over the dataset, scored by deterministic checks only.

    Deterministic scoring is used rather than a judge because a judge introduces the very biases we are
    trying to measure around (RESEARCH 5.1). The trade-off is coverage: only the subset of cases with
    machine-checkable expectations is scored, and `subjective_only` cases are excluded rather than
    guessed at.
    """
    from app.benchmark.dataset import deterministically_checkable

    report = QualityReport(provider_backed=provider_backed)
    if not provider_backed:
        return report

    selected = cases if cases is not None else deterministically_checkable()
    report.cases_evaluated = len(selected)
    report.arms = {"raw": ArmResult("raw"), "optimized": ArmResult("optimized")}

    semaphore = asyncio.Semaphore(concurrency)

    async def run_case(case: BenchmarkCase) -> None:
        async with semaphore:
            # Arm "raw": FAST mode sends the user's text essentially untouched.
            raw = await orchestrator.run(
                ExecutionRequest(
                    input=case.input,
                    mode=OrchestrationMode.FAST,
                    output_schema=case.output_schema,
                )
            )
            # Arm "optimized": full adaptive pipeline.
            opt = await orchestrator.run(
                ExecutionRequest(input=case.input, output_schema=case.output_schema)
            )
            for label, result in (("raw", raw), ("optimized", opt)):
                _score_arm(report.arms[label], case, result)

    await asyncio.gather(*(run_case(c) for c in selected))

    report.note = (
        "Scored by deterministic checks only (schema validity, required content, length limits). "
        "This measures instruction-following and format compliance, NOT factual accuracy. "
        "Cases requiring subjective judgement are excluded rather than estimated."
    )
    return report


def _score_arm(arm: ArmResult, case: BenchmarkCase, result: ExecutionResult) -> None:
    arm.cases_run += 1
    arm.cost_usd += result.cost.total_usd
    arm.latency_ms += result.trace.total_duration_ms if result.trace else 0
    arm.llm_calls += result.trace.llm_call_count() if result.trace else 0

    checks = evaluate_case_output(case, result.output, result)
    arm.checks_run += len(checks)
    passed = len([c for c in checks if c])
    arm.checks_passed += passed
    if checks and passed == len(checks):
        arm.cases_fully_passing += 1


def evaluate_case_output(
    case: BenchmarkCase, output: str, result: ExecutionResult | None = None
) -> list[bool]:
    """Deterministic checks for one case's output. Returns one bool per check."""
    results: list[bool] = []

    if case.output_schema is not None and result is not None and result.spec is not None:
        candidate = Candidate(id="bench", model_id="bench", provider="bench", text=output)
        spec = result.spec
        spec.output_schema = case.output_schema
        checks = deterministic.run_all(candidate, spec)
        results.extend(c.status is not CheckStatus.FAIL for c in checks)

    for token in case.must_include:
        results.append(token.lower() in output.lower())

    if case.max_words is not None:
        results.append(len(output.split()) <= case.max_words)

    if case.max_chars is not None:
        results.append(len(output) <= case.max_chars)

    return results


# ======================================================================================
# CLI entry point
# ======================================================================================
async def main() -> int:
    """`python -m app.benchmark.runner`"""
    from app.core.config import get_settings
    from app.providers import ProviderPool
    from app.registry.registry import ModelRegistry

    settings = get_settings()
    registry = ModelRegistry.from_catalog()
    pool = ProviderPool(settings)
    orchestrator = Orchestrator(
        settings=settings,
        registry=registry,
        pool=pool,
        # Caching is disabled for benchmarking: a cache hit would report a cost of zero and make the
        # second arm look free, which would be a fabricated saving.
        cache=None,
    )

    configured = pool.configured_providers()
    real_providers = configured - {"scripted"}

    print(f"Dataset: {len(CASES)} cases across {len({c.category for c in CASES})} categories")
    print(f"Configured providers: {sorted(configured) or 'none'}\n")

    policy = await run_policy_benchmark(orchestrator)
    print(policy.summary())
    print()

    quality = await run_quality_benchmark(orchestrator, provider_backed=bool(real_providers))
    print(quality.summary())

    await pool.aclose()
    return 0 if not policy.failed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
