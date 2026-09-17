# Evaluation Methodology

## What "evaluation" means here

Every evaluation signal in this product ships with its **source** and a **confidence band** — never a bare
number, and never phrased as accuracy. This follows directly from RESEARCH 5.1: an LLM judge has named,
measured biases (position, verbosity, self-preference), and a score without provenance is indistinguishable
from a score that "looks reasonable and is wrong."

| Source | Authority | What it can tell you |
|---|---|---|
| Deterministic check | Authoritative | Machine-verifiable facts: schema validity, length limits, required content, format shape |
| LLM judge | Advisory | An opinion against a rubric, from one model, with recorded mitigations |
| Human feedback | Authoritative but sparse | What a real user actually thought |

A deterministically invalid candidate never wins a selection, however well a judge scores it
(`evaluation/aggregate.py::select_best` — tested explicitly in
`tests/test_evaluation.py::TestAggregation::test_deterministically_invalid_candidate_never_wins`).

## Confidence bands

Three bands (`ConfidenceBand`), deliberately coarse — a two-decimal score implies precision the underlying
judge does not have:

- **High**: deterministic checks passed, a judge ran, score is strong, and the request is not high-risk.
- **Moderate**: either no judge ran (deterministic-only), or the request is high-risk (capped regardless of
  score — RESEARCH 5.1's warning about uncalibrated confidence applies hardest exactly where it matters
  most).
- **Low**: a deterministic check failed, or evidence is weak.

## Judge bias mitigations, and how each is verified

| Bias | Evidence | Mitigation | Test |
|---|---|---|---|
| Position | arXiv 2602.02219, 2604.23178 | Candidate order shuffled per call; permutation recorded | `JudgeMitigations.permutation` |
| Self-preference | judges inflate their own family | Cross-family judge preferred; `self_preference_risk` flagged, never hidden, when unavailable | `test_self_preference_risk_is_recorded` |
| Verbosity | longer answers score higher regardless | Length delta recorded; shorter answer wins ties | `test_shorter_answer_wins_a_tie` |
| Rubric vagueness | practitioner guidance | Criteria decomposed, anchored 0-4 scale, max 5 per call | `TestRubric` |

## The A/B harness (`app/benchmark/`)

Two questions, kept structurally separate because they need different evidence:

**A. Does the orchestration policy behave as designed?** — `run_policy_benchmark`. Offline, free, runs in
CI on every push. Asserts task classification, complexity/risk detection, mode selection, and — the
product's central claim — call-count budgets per request difficulty.

**B. Does optimisation actually improve outputs?** — `run_quality_benchmark`. Requires a real, priced
provider. Scores raw vs optimised over the dataset's deterministically-checkable cases only (schema
validity, required content, length limits) — **not** factual accuracy, which the harness does not claim to
measure. Subjective cases are excluded rather than scored by a proxy.

**The harness refuses to produce a number it cannot back.** With only the offline provider configured,
`QualityReport.summary()` returns "NOT RUN... any quality number produced from it would be meaningless" —
tested explicitly (`test_refuses_to_score_without_a_real_provider`). This is the direct, load-bearing
consequence of rule 47/60 (don't manufacture benchmark numbers).

### Running it

```bash
cd backend
python -m app.benchmark.runner
```

Without provider keys: reports policy conformance only (this is what CI checks). With at least one real
provider key configured: also runs and reports the quality A/B, with real cost attached to every arm.

### The dataset (`app/benchmark/dataset.py`)

28 cases across the scenario types rule 47 requires: simple questions, coding, debugging, writing,
research, summarization, structured output, ambiguous requests, high-risk topics, current-information
requests, document tasks, and adversarial (prompt-injection) cases. Every case either carries a
machine-checkable expectation or is explicitly marked `subjective_only` — a case with neither is a test
failure (`test_subjective_cases_are_marked_not_faked`), so the dataset cannot silently grow a fake check.

## Honesty rules enforced in code, not just prose

- No field anywhere in the API response can express "accuracy" as a percentage of truth.
- `model_agreement` (candidate similarity) is labelled a weak convergence signal, explicitly not evidence
  of correctness — models sharing training data agree on the same mistakes.
- Cost figures distinguish `is_estimate` from provider-reported actuals, and flag when unverified pricing
  was used (RESEARCH 3.2).
- Savings/comparison claims are computed per-tenant from that tenant's own executions, or not shown —
  RESEARCH 4.1 showed routing savings vary 35–85% by workload, so a single global number would be a lie
  for most tenants.
