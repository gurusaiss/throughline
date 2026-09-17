# Research Report

**Research window:** 2026-09-17
**Method:** live web search + direct fetch of vendor documentation.
**Honesty rule:** every finding below is tagged with how it was verified. Nothing here is invented. Where a
claim could not be verified against a primary source, it is marked `[SECONDARY]` or `[UNVERIFIED]` and the
architecture is designed so the claim does not need to be true.

### Verification tags

| Tag | Meaning |
|---|---|
| `[PRIMARY]` | Fetched directly from the vendor's own documentation during this session. |
| `[SECONDARY]` | From search-result summaries of third-party articles. Directionally useful, individually unreliable. |
| `[UNVERIFIED]` | Could not be checked at all. Treated as a hypothesis, never as a design guarantee. |

### Research constraint encountered (important)

This session's network egress proxy **blocked** the following hosts, so their primary documentation could not
be read:

- `platform.openai.com`, `openai.com`, `developers.openai.com`
- `ai.google.dev`
- `openrouter.ai` (including its public `/api/v1/models` catalogue endpoint)
- `langfuse.com`

This was not routed around (the environment's policy explicitly forbids that). The consequence is recorded
honestly: **OpenAI, Google and OpenRouter model/pricing facts in this document are `[SECONDARY]`.** The
architecture treats this as the normal condition rather than a defect — see Finding 3 and `DECISIONS.md`
(ADR-004): model metadata is *operator-updatable data*, never source code, and every price carries a
`verified_at` and `source` field so stale entries are visible rather than silently wrong.

---

## 1. Competitive landscape

### Finding 1.1 — The category consolidated hard in 2025–2026 `[SECONDARY]`

Multiple independent 2026 articles report the same set of events:

| Company | Reported outcome |
|---|---|
| Langfuse | Acquired by ClickHouse |
| Promptfoo | Acquired by OpenAI (reported March 2026) |
| Humanloop | Wound down (2025) |
| Helicone | Folded into Mintlify, development frozen |
| Vellum | Pivoted away from developer tooling to a consumer AI assistant (May 2026) |
| Braintrust | Raised an $80M Series B; evaluation-centric |
| PromptLayer | Independent; "prompt CMS" positioning |

Sources: [PromptLayer field guide](https://www.promptlayer.com/blog/best-prompt-management-tools-2026-field-guide/),
[PromptLayer 7 best](https://blog.promptlayer.com/the-7-best-prompt-management-tools-in-2026-tested-and-compared/),
[Confident AI comparison](https://www.confident-ai.com/knowledge-base/compare/best-ai-prompt-management-tools-with-llm-observability-2026).

**Caveat:** the most detailed of these sources is PromptLayer's own blog, i.e. a competitor describing its
competitors. The *pattern* (consolidation, absorption by larger platforms) is corroborated across several
independent articles and is treated as reliable. Individual company claims are not.

**Architectural impact:** building "another prompt manager with an eval tab" is a bad bet — that exact product
shape is being absorbed by infrastructure vendors (ClickHouse) and model labs (OpenAI). Differentiation must
sit somewhere those players are structurally unlikely to go. See `PRODUCT.md` §Positioning.

### Finding 1.2 — Independent confirmation that OpenAI is absorbing the eval layer `[SECONDARY]`

Search results state OpenAI's own Evals product becomes read-only for existing users on **2026-10-31** and
shuts down **2026-11-30**. Source: search summaries of
[OpenAI evals guide](https://developers.openai.com/api/docs/guides/evals) (page itself egress-blocked).

**Architectural impact:** do **not** build the evaluation engine on a vendor's hosted eval API. Evaluation is
implemented in-process, provider-agnostic, and persisted in our own Postgres. A vendor eval service can later
be added as an *exporter*, never as the system of record. Confirms ADR-006.

### Finding 1.3 — Two adjacent categories exist, and neither covers this product's core `[SECONDARY]`

**LLM gateways / routers** (Requesty, Portkey, LiteLLM, OpenRouter). Sources:
[Requesty comparison](https://www.requesty.ai/blog/litellm-vs-portkey-vs-openrouter-best-llm-gateway-2026),
[wavect](https://wavect.io/blog/llm-gateway-router-comparison-2026/),
[Braintrust routers article](https://www.braintrust.dev/articles/best-llm-routers-2026).
These route a request the developer already wrote, to a model. They do not change the request. Reported
differentiators are now reliability/SLA, cost via caching, and governance — i.e. the category has commoditised
into plumbing.

**Prompt/eval platforms** (Braintrust, PromptLayer, Langfuse). These improve prompts *offline*, at development
time, operated by an engineer who already knows what a rubric is.

**The gap:** nothing in either category takes an untrained user's natural-language intent and decides, *at
request time*, how much machinery that specific request deserves. Gateways assume the prompt is fixed; eval
platforms assume a human in the loop. This gap is the product. See `PRODUCT.md`.

### Finding 1.4 — Automated prompt optimisation is production-real, not research-only `[SECONDARY]`

Braintrust's "Loop" reportedly auto-optimises prompts from eval results, with adopter teams citing 30%+
accuracy improvements. Source:
[market overview](https://www.promptquorum.com/blog/prompt-optimization-market-overview-2026).

**Caveat:** "30%+ within weeks" is a vendor-attributed marketing figure with no published methodology. It is
recorded as evidence that *the category is validated*, and explicitly **not** used as a performance target or
quoted anywhere in our product surface.

**Architectural impact:** offline prompt optimisation driven by datasets is table stakes for the eval
competitors, so our Prompt Lab must exist — but as Phase 2, because our differentiator is the runtime path.

---

## 2. Prompt optimisation literature

### Finding 2.1 — GEPA: reflective prompt evolution beats RL at a fraction of the rollouts `[PRIMARY-ish]`

[GEPA (arXiv 2507.19457)](https://arxiv.org/pdf/2507.19457) reports up to **20% absolute gain over GRPO with up
to 35× fewer rollouts** across six tasks, and **>10% over MIPROv2**. It samples full trajectories, uses natural
language reflective critiques as the mutation operator, and maintains a Pareto frontier of complementary
prompts. Presented at ICLR 2026 per
[FutureAGI's optimizer explainer](https://futureagi.com/blog/dspy-optimizers-explained/).

**Architectural impact:** this is the correct long-term engine for our offline Prompt Lab, and it validates the
central mechanism we use online — *a critique-driven loop beats blind generation*. Our runtime evaluator →
retry path is a bounded, single-step version of the same idea. Full GEPA-style evolution is deferred to Phase 2
because it needs a dataset per task, which new users do not have on day one.

### Finding 2.2 — Prompt optimisation does not always help `[SECONDARY]`

[MAS-PromptBench (arXiv 2606.23664)](https://arxiv.org/pdf/2606.23664) is titled, literally, *"When Does Prompt
Optimization Improve Multi-Agent LLM Systems?"* — the existence of the question is the finding.

**Architectural impact:** directly supports the product's core principle that longer ≠ better, and mandates the
A/B harness in `EVALUATION.md`. We must be able to answer "did optimisation help *for this task type*" with
data, and route around our own optimiser where it does not. This is why `OptimizationDecision` records which
techniques were applied and why — so the decision itself is measurable.

---

## 3. Model providers

### Finding 3.1 — Anthropic pricing `[PRIMARY]`

Fetched from [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing).

| Model | Input /MTok | Output /MTok | Cache hit /MTok |
|---|---|---|---|
| Claude Fable 5.1 | $10 | $50 | $0.25 (0.025×) |
| Claude Opus 5 | $5 | $25 | $0.50 |
| Claude Sonnet 5 | $2 | $10 | $0.20 |
| Claude Haiku 4.5 | $1 | $5 | $0.10 |

Also confirmed: Batch API = 50% off both directions; 5-minute cache write 1.25×, 1-hour cache write 2×, cache
read 0.1× (0.025× on Fable/Mythos 5.1); Claude 4.6+ includes the full 1M context at standard pricing with no
long-context surcharge; `inference_geo: "us"` applies a 1.1× multiplier; web search $10/1,000 searches; web
fetch free beyond tokens.

### Finding 3.2 — Secondary pricing sources were materially WRONG `[PRIMARY beats SECONDARY]`

A secondary source stated Claude Sonnet 5 "held $3/$15 once its intro $2/$10 window closed August 31, 2026."
The primary documentation explicitly contradicts this:

> "The $2/$10 per million input/output token pricing for Claude Sonnet 5, announced at launch as introductory
> pricing through August 31, 2026, is now the standard price. The previously scheduled increase to $3/$15 per
> million input/output tokens on September 1, 2026 will not occur."

**This is the single most important research finding in this document.** A pricing aggregator was confidently
wrong by 50% about a headline model, two weeks after the change. Any system that hard-codes pricing, or trusts
scraped pricing, will mis-bill its customers and mis-route on false cost signals.

**Architectural impact (decisive):**
1. Pricing lives in a database table seeded from a versioned YAML catalogue — never in code. (ADR-004)
2. Every price row carries `source_url`, `verified_at`, `verified_by`.
3. The registry exposes staleness: prices older than a configurable threshold are flagged in the admin UI and
   attached to cost estimates as a confidence caveat.
4. Cost *estimates* are labelled estimates. Billing uses provider-reported token usage, not our guesses.

### Finding 3.3 — Other providers `[SECONDARY]`

Egress-blocked, so recorded as hypotheses to be verified by the operator before production billing:

- **OpenAI:** GPT-5.6 family (Sol $4/$20, Terra $2/$12, Luna $0.20/$1.20); GPT-6 Astra (released 2026-09-03)
  $10/$50. Cached input ~10% of input; Batch 50% off.
  Sources: [BenchLM](https://benchlm.ai/openai/api-pricing), [CloudZero](https://www.cloudzero.com/blog/openai-pricing/).
- **Google:** Gemini 3.1 Pro ~$2/$12 (≤200K prompts); Gemini 3.x Flash introductory $0.75/$3.75 through
  2026-12-31, rising to $1.50/$7.50 on 2027-01-01.
  Sources: [BenchLM](https://benchlm.ai/google/api-pricing), [Kunavo](https://kunavo.com/guides/gemini-api-pricing-2026).

Note the Gemini entry contains a *scheduled future price change*, which is exactly the class of fact that
silently rots. The catalogue schema therefore supports `effective_from` / `effective_until` windows.

**Architectural impact:** ships with these seeded and clearly marked `verified: false`. The system is fully
functional with only the one `[PRIMARY]`-verified provider configured.

### Finding 3.4 — Structured output support differs per provider in a way that matters `[SECONDARY]`

All three major providers now constrain sampling to schema-valid output, but
"each provider honors a different subset of JSON Schema" — OpenAI via native constrained decoding, Anthropic via
tool-use, Gemini via `response_json_schema`. Sources:
[Logic guide](https://logic.inc/resources/structured-outputs-guide),
[DevTk](https://devtk.ai/en/blog/ai-structured-output-guide-2026/).

**Architectural impact:** two rules. (1) The provider adapter owns schema translation, so the core never
branches on provider. (2) **We always validate the returned JSON locally against the full schema regardless of
what the provider promises.** Reported compliance rates of 98–100% mean 1-in-50 to 1-in-∞ failures; at SaaS
volume the difference between 99% and 100% is a support queue. The deterministic validator is not redundant
with constrained decoding — it is the thing that makes constrained decoding safe to depend on.

---

## 4. Model routing

### Finding 4.1 — Routing works, and its headline numbers are not portable `[PRIMARY]`

[RouteLLM (arXiv 2406.18665)](https://arxiv.org/pdf/2406.18665) /
[repo](https://github.com/lm-sys/routellm): a lightweight classifier trained on Chatbot Arena preference data
routes between a strong and a weak model, reporting **85% cost reduction on MT Bench at 95% of GPT-4 quality**,
with the strong model needed on only ~14% of queries. Per-benchmark savings varied widely: >85% (MT Bench),
45% (MMLU), 35% (GSM8K).

Critically, a secondary analysis states the caveat plainly: savings are "benchmark- and task-dependent, and a
single blended 'expected savings' number should be treated skeptically without knowing the query mix."
Source: [digitalapplied](https://www.digitalapplied.com/blog/llm-model-routing-2026-cost-quality-optimization-engineering-guide).

**Architectural impact:**
1. Routing is justified as a subsystem — the effect size is real and large.
2. The same paper shows a 2.4× spread (35%→85%) across task mixes, so we will **never** display a global
   "save X%" claim. Savings are computed per tenant from that tenant's own recorded executions, or not shown.
3. MVP router is explainable rules over the model registry, not a learned classifier: a learned router needs
   preference data we will not have until we have traffic. The interface is designed so a learned policy drops
   in behind it later (ADR-007).

---

## 5. Evaluation

### Finding 5.1 — LLM judges have specific, named, measurable biases `[PRIMARY + SECONDARY]`

Primary research: [Judging the Judges (arXiv 2604.23178)](https://arxiv.org/pdf/2604.23178) systematically
evaluates bias-mitigation strategies; [position bias in rubric-based judging (arXiv 2602.02219)](https://arxiv.org/pdf/2602.02219);
[The Coin Flip Judge (arXiv 2606.13685)](https://arxiv.org/pdf/2606.13685) on reliability.

Named biases: **position bias** (favouring first/last regardless of quality), **verbosity bias** (rewarding
length), **self-preference bias** (inflating scores for a judge's own model family).

Practitioner guidance: decompose rubrics into discrete checks, validate against human baselines until
correlation exceeds ~0.85, rotate answer positions, and use a multi-family judge ensemble for launch decisions.
Sources: [FutureAGI best practices](https://futureagi.com/blog/llm-as-judge-best-practices-2026/),
[Openlayer guide](https://www.openlayer.com/blog/llm-as-judge-evaluation-guide).

**Architectural impact — every one of these becomes a concrete implementation requirement:**

| Bias | Our mitigation | Where |
|---|---|---|
| Position | Randomise candidate order per judging call; persist the permutation for audit | `evaluation/judge.py` |
| Self-preference | Prefer a judge from a different model family than the generator; **record a `self_preference_risk` flag when violated** | `evaluation/judge.py` |
| Verbosity | Record length delta alongside every score so length-correlated scoring is detectable | `evaluation/aggregate.py` |
| Rubric vagueness | Decompose into discrete named criteria, each scored separately with an anchored scale | `evaluation/rubric.py` |
| Overtrust | Judges are advisory; deterministic validators are authoritative where both apply | `EVALUATION.md` |

The phrase "uncalibrated judges produce scores that look reasonable and are wrong" is the design warning that
justifies never surfacing a judge score without its provenance.

### Finding 5.2 — Judges are not free `[SECONDARY]`

Cost control is listed among the four core 2026 best practices.

**Architectural impact:** evaluation is **sampled**, not universal. FAST mode runs deterministic checks only
(near-zero cost, no LLM call). Judge invocation is a policy decision with a budget, configurable per tenant.

---

## 6. Security

### Finding 6.1 — Prompt injection is #1 and is not solvable, only mitigable `[SECONDARY]`

OWASP ranks prompt injection #1 in the 2025 Top 10 for LLM Applications, for the second consecutive edition.
OWASP's own guidance notes **neither RAG nor fine-tuning fully mitigates it**, recommending defence in depth,
least-privilege tooling, I/O filtering, human approval for high-risk actions, and adversarial testing.
Sources: [Checkmarx](https://checkmarx.com/learn/breaking-down-the-owasp-top-10-for-llm-applications/),
[Mend](https://www.mend.io/blog/2025-owasp-top-10-for-llm-applications-a-quick-guide/),
[Promptfoo OWASP docs](https://www.promptfoo.dev/docs/red-team/owasp-llm-top-10/).

### Finding 6.2 — Spotlighting: datamarking >> delimiting, quantified `[PRIMARY]`

[Microsoft spotlighting paper (arXiv 2403.14720)](https://www.alphaxiv.org/abs/2403.14720) /
[CEUR version](https://ceur-ws.org/Vol-3920/paper03.pdf):

- **Delimiting** alone: attack success ~60% → ~30%. Modest.
- **Datamarking** (interleaving a special token throughout untrusted text): ~50% → **below 3%**; on one model
  40% → 0.00%.
- **Encoding** (e.g. base64) on high-capacity models: often 0%.

The same literature is explicit that spotlighting "does not hold up against determined adaptive adversaries."
Source: [limits of spotlighting](https://theneuralbase.com/prompt-injection/learn/advanced/limits-of-spotlighting/).

**Architectural impact:** this is directly actionable and unusually well-quantified, so we implement it rather
than hand-waving "input validation":
1. All untrusted content (uploaded documents, retrieved web content, project context supplied by a *different*
   user) is wrapped by a `spotlight()` function using **datamarking**, not bare delimiters — the research shows
   delimiters alone leave ~30% attack success, which is not a defence.
2. Datamarking is layered with least-privilege tool scoping and output validation, never relied on alone.
3. The benchmark suite includes adversarial injection cases so the defence is regression-tested.

### Finding 6.3 — Regulatory obligations are live now, not hypothetical `[SECONDARY]`

EU AI Act Article 50 transparency obligations **have applied since 2026-08-02**: EU users must know they are
interacting with AI or seeing AI-generated content. The Act sits alongside GDPR rather than replacing it, and
obligations attach by *role* (provider vs deployer) — a SaaS commonly being both.
Sources: [Secure Privacy](https://secureprivacy.ai/blog/eu-ai-act-2026-compliance),
[PrivacyForge](https://privacyforge.io/resources/blog/gdpr-eu-ai-act-interplay-2026),
[GDPR Register checklist](https://www.gdprregister.eu/articles/eu-ai-act-compliance-checklist-dpos/).

**Architectural impact:** AI-generated output is labelled as such in the UI and in API responses
(`"generated_by": "ai"`); the provider data-flow (which third party received which content) is recorded per
execution and surfaced to the user; retention is configurable per tenant with real deletion.
**This is engineering groundwork, not legal advice** — a lawyer must review before EU launch (`SECURITY.md`).

---

## 7. Cost and caching

### Finding 7.1 — Semantic caching is a quality risk sold as a cost win `[SECONDARY]`

2026 production thresholds are reported at 0.92–0.97 cosine similarity. Below 0.90 false positives degrade
quality; above 0.98 the cache rarely hits. The decisive observation:

> "a bad cache hit returns an incorrect answer with full confidence and a 200 OK status, and without explicit
> instrumentation, you cannot tell whether the system is working or silently degrading."

Guidance: keep tenant ID, system prompt and model out of the semantic comparison; tune in shadow mode.
Sources: [Portkey thresholds](https://portkey.ai/blog/semantic-caching-thresholds/),
[NHI](https://nhimg.org/faq/how-should-security-teams-implement-semantic-caching-for-llm-applications-withou/),
[hit-rate myth](https://dev.to/gauravdagde/llm-semantic-caching-the-95-hit-rate-myth-and-what-production-data-actually-shows-8ga).

**Architectural impact:** MVP ships **exact-match caching only**, keyed on
`(tenant, model, params, normalized_prompt_hash)` — zero correctness risk, real savings on retries and
comparisons. Semantic caching is deferred behind a feature flag in shadow mode (log would-be hits, serve the
real answer, measure false-hit rate offline) before it is ever allowed to serve traffic. This is a deliberate
application of rule 53 (do not overbuild) reinforced by evidence.

### Finding 7.2 — Prompt caching is a large, safe, provider-native win `[PRIMARY]`

Per Anthropic's documentation (Finding 3.1): cache reads cost 0.1× base input; a 5-minute cache write costs
1.25×, so **caching pays for itself after a single cache read**.

**Architectural impact:** the optimiser deliberately orders the compiled prompt **stable-prefix-first**
(system role → project context → task instructions → volatile user input) so the cacheable prefix is maximal.
This is a case where a prompt-construction decision is driven by billing mechanics, and it is free quality-wise.

### Finding 7.3 — Metering must be aggregated, and deferred billing carries float risk `[SECONDARY]`

Stripe guidance: adopt a meter-first design with an internal usage ledger; aggregate events (e.g. in Redis)
and flush periodically rather than one event per action; note that "usage happens now, payment happens later"
creates real financial exposure if an invoice fails.
Sources: [Stripe credits](https://stripe.com/blog/introducing-credits-for-usage-based-billing),
[Stripe UBB for AI](https://stripe.com/resources/more/ai-companies-and-usage-based-billing).

**Architectural impact:** an internal `usage_records` ledger is the system of record; Stripe is an *exporter*
behind an interface, not a dependency (and is not wired in the MVP — see Known Limitations). Hard budget caps
are enforced **pre-flight**, before the provider call, because post-hoc metering cannot prevent the spend.

---

## 8. Market

### Finding 8.1 — Segments split on what they will pay for `[SECONDARY]`

Enterprises prioritise quality and governance and pay for leading performance; startups optimise for
cost-effective and self-hostable, valuing free-tier generosity and zero-seat-cost self-hosting. Market sizing
for "LLM prompt generation tools" is quoted at $456M (2024) → $1,018M (2031), 12% CAGR.
Sources: [FutureAGI startups](https://futureagi.com/blog/best-prompt-management-tools-for-startups-in-2026/),
[market overview](https://www.promptquorum.com/blog/prompt-optimization-market-overview-2026).

**Caveat:** third-party market-sizing figures for a category this young are close to worthless for planning, and
a 12% CAGR is implausibly low for AI tooling in this period — quoted for completeness, **not** relied upon.

**Architectural impact:** self-hostability is a genuine acquisition lever for the developer segment, so the
whole system runs from `docker compose up` with no managed-service dependency. Governance features
(audit logs, budgets, RBAC) are architected in from day one because they gate the segment that actually pays,
even though only the foundations ship in MVP.

---

## 9. Summary: research → architecture

| # | Finding | Impact |
|---|---|---|
| 3.2 | A pricing aggregator was wrong by 50% on a flagship model | Pricing is versioned data with `verified_at`; estimates labelled as estimates; billing uses provider-reported usage |
| 6.2 | Datamarking cuts injection ~50%→<3%; delimiters only →30% | `spotlight()` uses datamarking for all untrusted content |
| 7.1 | Bad semantic cache hits are invisible and confident | Exact-match cache only in MVP; semantic behind a shadow-mode flag |
| 7.2 | Cache read 0.1×, write 1.25× → pays back after one read | Prompt compiled stable-prefix-first |
| 5.1 | Position/verbosity/self-preference bias are real and named | Order randomisation, cross-family judges, length-delta recording, rubric decomposition |
| 4.1 | Routing savings vary 35%–85% by task mix | Per-tenant measured savings only; never a global claim |
| 1.2 | OpenAI's own eval product is shutting down | Evaluation is in-process and provider-agnostic; vendors are exporters |
| 2.2 | Prompt optimisation does not always help | A/B harness is mandatory; the optimiser must be able to lose |
| 3.4 | Providers honour different JSON Schema subsets | Always validate locally; adapters own translation |
| 6.3 | EU AI Act Art. 50 is in force since 2026-08-02 | AI content labelled; provider data flow recorded; retention configurable |

---

## 10. Open questions requiring the operator (not inferable)

1. **Provider API keys.** None present in this environment. Live execution cannot be verified without them.
2. **Pricing verification** for OpenAI/Google/OpenRouter — blocked by egress policy here.
3. **Legal review** of EU AI Act / GDPR posture before any EU launch.
4. **Trademark clearance** for the product name (see `PRODUCT.md`; the name is provisional).
5. **Final pricing strategy** — depends on 1–2 and on real per-request cost data, which requires traffic.
