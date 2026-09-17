# Product Definition

> **Name:** Throughline *(provisional — trademark not cleared; see RESEARCH.md §10.4)*
> The line that runs from what a person meant to what they actually got.

## 1. Positioning

Positioning follows from evidence, not aspiration. Three research findings constrain it:

1. The "prompt manager with an eval tab" shape is being absorbed by infrastructure vendors and model labs
   (RESEARCH 1.1, 1.2). Do not build there.
2. LLM gateways route a request the developer *already wrote*. They never change the request (RESEARCH 1.3).
3. Eval platforms improve prompts *offline*, operated by an engineer who already knows what a rubric is.

**The gap neither side covers:** deciding, *at request time*, how much machinery a specific request deserves —
and proving the decision was right.

> **Throughline is an adaptive AI execution layer.**
> It turns intent into an optimised request, spends only as much orchestration as the request justifies,
> and evaluates what comes back before returning it.

**The one-line differentiator:** *the system decides how hard to try.*

### What we are explicitly not

| Not | Why |
|---|---|
| A prompt generator | Commoditised; a text box that adds "You are an expert…" has no defensible moat |
| An LLM gateway | Plumbing; already commoditised on price and SLA (RESEARCH 1.3) |
| An offline eval platform | Braintrust's home turf, well funded, and adjacent to OpenAI's |
| "100% accurate AI" | Forbidden. We ship measurable signals with provenance, never accuracy claims |

## 2. The core product principle

**Longer prompt ≠ better prompt.** The system must be able to conclude "this needs nothing from me" and pass a
request through nearly untouched.

Every additional LLM call costs money, adds latency, adds a failure mode, and adds complexity. The orchestrator
is therefore a *spending decision*, and the headline engineering metric is not output quality alone but
**quality per unit of orchestration spent**.

This has a sharp consequence we accept: **the optimiser must be allowed to lose.** If A/B shows optimisation
does not help for a task type, the system routes around its own optimiser (RESEARCH 2.2). A product that cannot
measure its own failure cannot improve.

## 3. Users

| Segment | Job to be done | Why they pay |
|---|---|---|
| **Non-expert end user** | "Write the email / build the page / analyse this PDF" without learning prompt engineering | It just works; they see *why* it worked |
| **Developer (primary buyer)** | Stop hand-tuning prompts and model choices per feature | One API call replaces a hand-built orchestration layer |
| **Team / startup** | Shared prompt library, budgets, visibility into AI spend | Governance + cost control |
| **API customer** | Embed the optimisation engine in their own product | Usage-based revenue, highest margin |

Research (8.1) indicates enterprises pay for quality and governance while startups optimise for cost and
self-hostability. The developer/startup segment is the MVP target: they feel the pain daily, evaluate fast, and
do not need a procurement cycle. Enterprise features are *architected for* but not *built* in MVP.

## 4. Product modes

| Mode | Behaviour |
|---|---|
| **Auto** | System decides everything (default) |
| **Prompt only** | Optimise, return the prompt, do not execute |
| **Execute** | Optimise + execute + evaluate |
| **Compare** | Run raw vs optimised, or model vs model, side by side |
| **Advanced** | User pins model, temperature, format, depth, budget, candidate count |

Compare mode is strategically important: it is the feature that *proves the product works* to a sceptical
developer, and it is the honest alternative to publishing a benchmark number we cannot substantiate.

## 5. Orchestration modes

| Mode | Extra LLM calls | Used when |
|---|---|---|
| **FAST** | 0 beyond generation | Simple, low-ambiguity, low-risk, heuristically confident |
| **BALANCED** | 1 (intent) + generation + deterministic checks | The common case |
| **DEEP** | intent + N candidates + judge | High complexity or high stakes |
| **EXPERT** | decomposition + specialists + judge + synthesis + verification | Genuinely hard, multi-part work |

Mode is chosen by policy from request signals, overridable by the user. **FAST must be reachable without any
classifier LLM call** — a heuristic pre-classifier decides whether the system even needs to think about
thinking. This is the mechanism that makes "don't overbuild" real rather than aspirational.

## 6. What the user sees

Progress is shown as safe, high-level metadata only — never private chain-of-thought:

```
Understanding request      ✓  (low ambiguity, no clarification needed)
Optimising instructions    ✓  (added output format, audience)
Selecting model            ✓  (Sonnet 5 — structured output + cost)
Generating                 ✓
Evaluating                 ✓  (schema valid, 4 criteria passed)
```

**"Why this prompt?"** exposes the *transformation decision* — which techniques were applied and which signal
triggered each — not the model's reasoning. This is a deliberate boundary: it is the difference between
explaining our system (good, builds trust) and leaking the model's internal deliberation (forbidden).

## 7. Honesty requirements (product-level, non-negotiable)

- Never claim accuracy percentages. Surface evaluation score, confidence, grounding, sources, verification
  status, model agreement, judge agreement, user feedback — each with provenance.
- Cost figures are **estimates** until provider usage is reported back; labelled accordingly.
- Savings claims are computed per tenant from that tenant's own executions, or not shown (RESEARCH 4.1).
- AI-generated output is labelled as AI-generated (EU AI Act Art. 50, in force 2026-08-02).
- Uncertainty is stated explicitly on high-risk or low-confidence results.

## 8. MVP scope

**In:** auth + multi-tenancy · provider abstraction (Anthropic, OpenAI, Google, OpenRouter, offline test
provider) · model registry with updatable pricing · heuristic + LLM intent analysis · prompt compiler and
optimiser · rule-based router · adaptive orchestrator (4 modes) · deterministic evaluators · LLM judge with
bias mitigations · raw-vs-optimised compare · prompt versioning · execution tracing · usage/cost metering with
pre-flight budget enforcement · feedback capture · API v1 · playground/compare/prompts/analytics UI.

**Deferred (Phase 2+):** datasets & experiments UI · GEPA-style offline optimisation · file upload + RAG ·
semantic cache (shadow mode first) · Stripe billing · team RBAC · browser extension · SSO.

Each deferral is recorded in `ROADMAP.md` against the rule-53 test (does it solve a real problem / differentiate
/ improve retention / improve monetisation / is it architecturally required / can it be added later).
