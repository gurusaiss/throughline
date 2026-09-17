# Architecture Decision Records

Each ADR: context → decision → rejected alternatives → consequences. Decisions trace to `RESEARCH.md` findings
where evidence drove them.

---

## ADR-001 — Python/FastAPI backend, Next.js frontend

**Context.** Needs async fan-out to providers, first-class typing, and a mature AI ecosystem.

**Decision.** FastAPI + Pydantic v2 + SQLAlchemy 2.0 (async) + Alembic. Next.js App Router + TypeScript +
Tailwind on the frontend.

**Rejected.** *Node for everything* — Python's provider SDKs and eval tooling are further ahead and Pydantic
gives us runtime-validated IR for free. *Django* — heavier than needed, ORM async story weaker. *Go* — fastest
runtime, smallest AI ecosystem; the bottleneck here is provider latency, not our CPU.

**Consequences.** Two languages to maintain. Accepted: the boundary is a clean versioned HTTP API we need
anyway for the developer product.

---

## ADR-002 — `ExecutionSpec` as the central intermediate representation

**Context.** Intent analysis, optimisation, routing, generation, evaluation and synthesis all need a shared
understanding of the request. Passing raw strings between stages would couple every stage to every other.

**Decision.** One typed IR — `ExecutionSpec` — produced by the compiler and consumed by every downstream stage.
Stages are pure functions over it where possible.

**Consequences.** Stages are independently testable without LLM calls. Adding a stage does not change existing
signatures. The IR is the thing we persist, so a trace can be replayed. This is the single highest-leverage
structural decision in the codebase.

---

## ADR-003 — Heuristic pre-classifier before any LLM call

**Context.** The product principle forbids unnecessary LLM calls, but "decide if this is simple" is itself
usually implemented as an LLM call — which defeats the purpose for exactly the cheap requests that matter most.

**Decision.** A deterministic, zero-cost signal extractor (length, structure, question form, imperative verbs,
format cues, freshness markers, risk lexicon, ambiguity markers) runs first and emits signals with a confidence.
Above a confidence threshold on a simple request, FAST mode is chosen and **no classifier LLM call is ever
made**. Below threshold, the LLM intent analyser runs.

**Rejected.** *Always call an LLM to classify* — costs a call on every request including trivial ones, which is
the exact anti-pattern the product exists to avoid. *Pure heuristics* — too brittle for genuinely ambiguous
input.

**Consequences.** FAST path adds ~0ms and $0. The heuristic is fully unit-testable offline with no API keys,
which also makes the orchestration policy itself measurable. Risk: heuristics misclassify; mitigated by a
conservative threshold that prefers escalation, and by recording the decision for offline audit.

---

## ADR-004 — Model metadata and pricing are operator-updatable data, never code

**Context.** RESEARCH 3.2 — a pricing aggregator was wrong by **50%** on Claude Sonnet 5 two weeks after the
change, because a scheduled increase was cancelled. Our own priors were also wrong.

**Decision.** A versioned YAML catalogue seeds a `models` table. Every price row carries `source_url`,
`verified_at`, `verified_by`, and optional `effective_from`/`effective_until`. Staleness is surfaced, not
hidden. Cost *estimates* are labelled; *billing* uses provider-reported token usage.

**Rejected.** *Hard-coded pricing constants* — guaranteed to be silently wrong. *Live scraping* — the exact
mechanism that produced the 50% error.

**Consequences.** Operators must maintain the catalogue; the UI makes staleness visible so this is a prompt
rather than a trap. Pricing changes require no deploy.

---

## ADR-005 — Exact-match caching only in MVP; semantic caching behind shadow mode

**Context.** RESEARCH 7.1 — a false semantic cache hit returns a confidently wrong answer with a 200 OK, and is
invisible without explicit instrumentation.

**Decision.** MVP caches on `(tenant, model, params, normalized_prompt_hash)`. Zero correctness risk, real
savings on retries, regenerations and comparison runs. Semantic caching is implemented behind a flag that
initially only *logs* would-be hits while serving the real answer, so the false-hit rate is measured before it
is trusted.

**Consequences.** Lower hit rate than competitors advertise. Accepted — we would rather report a smaller honest
saving than a larger silent quality regression.

---

## ADR-006 — Evaluation is in-process and provider-agnostic

**Context.** RESEARCH 1.2 — OpenAI's hosted eval product is reportedly shutting down 2026-11-30, having
acquired Promptfoo. Building on a vendor's eval API is building on sand.

**Decision.** Deterministic validators and the LLM judge run in our process; results persist in our Postgres.
Third-party eval/observability platforms are **exporters** behind an interface, never the system of record.

**Consequences.** More code to own. In exchange, no vendor can deprecate our core capability, and self-hosting
stays possible — which RESEARCH 8.1 identifies as a real acquisition lever.

---

## ADR-007 — Rule-based router in MVP, learned router behind the same interface

**Context.** RESEARCH 4.1 — RouteLLM's learned router works well but was trained on Chatbot Arena preference
data. We have no preference data until we have traffic.

**Decision.** MVP router scores candidate models against `ExecutionSpec` requirements using explainable rules
over registry capabilities, with weights for quality/cost/latency. `RoutingPolicy` is an interface; a learned
policy drops in later and can be A/B'd against the rules.

**Consequences.** Every routing decision carries a human-readable reason (needed for the "why" UI anyway). We
collect the feedback and evaluation data a learned router will later need — the MVP is the data-collection
phase for its own successor.

---

## ADR-008 — Datamarking for untrusted content, not delimiters

**Context.** RESEARCH 6.2 quantifies it: delimiters alone reduce attack success ~60%→~30%; datamarking
~50%→<3%.

**Decision.** All untrusted content (uploaded docs, retrieved content, cross-user project context) passes
through `spotlight()`, which interleaves a per-request random marker token and instructs the model that marked
text is data, never instructions. Layered with least-privilege tooling and output validation; never relied on
alone, since the literature is explicit it fails against adaptive adversaries.

**Consequences.** Slight token overhead and mild readability cost in traces. Justified by a >10× reduction in
attack success. Adversarial cases are regression-tested in the benchmark suite.

---

## ADR-009 — Own trace store with an exporter interface, not a hosted observability dependency

**Context.** Langfuse is a natural fit but was reportedly acquired (RESEARCH 1.1), its site was unreachable from
this environment, and it would add a hard external dependency to a self-hostable product.

**Decision.** Executions, steps and model calls persist as a first-class trace tree in Postgres, modelled on
OpenTelemetry span semantics. A `TraceExporter` interface allows Langfuse/OTLP export later.

**Consequences.** We own the trace UI. Acceptable because the "why this prompt / what did it cost" view is a
*product* surface here, not just ops telemetry — it is the feature that builds user trust.

---

## ADR-010 — Always validate structured output locally

**Context.** RESEARCH 3.4 — providers honour different JSON Schema subsets, at 98–100% reported compliance.

**Decision.** Adapters translate schemas to each provider's mechanism; the core *always* re-validates the
returned payload against the full schema, with a bounded repair-retry on failure.

**Consequences.** Small CPU cost per structured call. At volume, the gap between 99% and 100% is a support
queue, so this is cheap insurance — and it makes constrained decoding safe to depend on.

---

## ADR-011 — Pre-flight budget enforcement

**Context.** RESEARCH 7.3 — usage happens now, payment happens later; post-hoc metering cannot prevent spend.

**Decision.** Estimated cost is checked against the tenant's remaining budget **before** the provider call. Over
budget → refuse or downgrade to a cheaper plan, per tenant policy. Usage is written to an internal ledger that
is the system of record; Stripe is an exporter (not wired in MVP).

**Consequences.** A cost estimate is required before every call, which the registry provides anyway for routing.
Estimates can be wrong, so a safety margin is configurable.

---

## ADR-012 — No background job queue in MVP

**Context.** Rule 53. A queue (Celery/Arq) is the reflexive choice but adds a broker, workers, deployment
surface and failure modes.

**Decision.** Request-scoped async orchestration with `asyncio.gather` for parallel fan-out. No queue.

**Rejected.** *Celery/Arq now* — nothing in the MVP path is long-running enough to need it; parallel candidate
generation is exactly what asyncio is for.

**Consequences.** Long-running batch experiments (Phase 2) will need one. The `ExperimentRunner` interface is
written so it can move behind a queue without changing callers. Documented rather than pre-built.
