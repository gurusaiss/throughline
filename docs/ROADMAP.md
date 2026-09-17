# Roadmap

Every deferred feature is tested against rule 53 before being listed: does it solve a real problem, does it
differentiate, does it improve retention or monetisation, is it architecturally required now, what does it
cost to maintain, can it wait. Nothing below was cut for lack of ideas — each row states the reason.

## Shipped (this build)

Auth + multi-tenancy · provider abstraction (Anthropic verified, OpenAI/Google/OpenRouter unverified
pricing, offline test provider) · model registry with operator-updatable pricing and staleness flags ·
heuristic + LLM intent analysis · prompt compiler/optimiser with technique-level provenance · rule-based
router (single/fallback/parallel/ensemble) · adaptive orchestrator (FAST/BALANCED/DEEP modes; EXPERT
defined, not yet exercised by a real multi-specialist workload) · deterministic evaluators · LLM judge
with position/self-preference/verbosity mitigations · raw-vs-optimised and model-vs-model compare · prompt
versioning with non-destructive rollback · execution tracing · usage ledger with pre-flight budget
enforcement · exact-match caching · feedback capture · REST API v1 · Next.js UI (playground, compare,
prompts, models, history, analytics, settings) · offline benchmark harness with an honest refusal to score
without a real provider · CI (lint, types, tests, migration check, frontend build).

## Phase 2 — next

| Item | Why now, why not yet |
|---|---|
| Datasets & experiments UI | The A/B harness exists (`app/benchmark/`) but has no UI for user-authored datasets. Real differentiator once there's traffic to learn from; premature before then. |
| GEPA-style offline prompt optimisation | RESEARCH 2.1: reflective evolution beats RL at far lower cost, and is the natural next step for the Prompt Lab. Needs a dataset per task, which users don't have on day one — hence Phase 2, not MVP. |
| File upload + retrieval (RAG) | Real user need (document analysis, PDF Q&A) but needs chunking/retrieval infra and storage; not required to prove the core orchestration thesis. |
| Semantic cache — shadow mode | RESEARCH 7.1: a false hit is invisible and confidently wrong. Ships logging would-be hits only, measured against real traffic, before it ever serves an answer (ADR-005). |
| Rate limiting / abuse prevention | Needed before public unauthenticated exposure; not needed for authenticated MVP usage. |
| Postgres Row-Level Security | Defence in depth once the schema is stable; the repository-scoping guarantee is the MVP-level control. |
| Stripe billing integration | `UsageRecordRow` is the system of record and exporter-ready; wiring an actual payment processor is a business decision requiring a live Stripe account (see `MANUAL_STEPS.md`). |
| Team RBAC UI | Roles already exist in the schema (`memberships.role`); only the UI for inviting/managing teammates is missing. |

## Phase 3 — later

Enterprise SSO (RESEARCH 8.1: not required for the targeted developer/startup segment) · advanced learned
routing (ADR-007: needs preference data this MVP is the collection phase for) · public SDKs · browser
extension (rule 39: only after determining which integration actually retains users) · prompt marketplace ·
advanced cohort analytics.

## Explicitly rejected, and why

- **A global "X% cheaper" marketing claim** — RESEARCH 4.1 shows a 35–85% spread depending on workload; a
  single number would misrepresent most tenants. Savings are shown per-tenant or not at all.
- **A hosted vendor eval/observability dependency** (e.g. building on a provider's own eval API) — RESEARCH
  1.2: OpenAI's own Evals product is reportedly being wound down after acquiring a competitor. Evaluation
  is in-process (ADR-006).
- **Semantic caching as a default** — see Phase 2 row above; the risk profile (RESEARCH 7.1) doesn't
  justify shipping it as anything but shadow-mode-first.
- **A background job queue in MVP** (ADR-012) — nothing in the MVP path runs long enough to need one;
  revisit when offline experiments (Phase 2) need it.

## Immediate follow-ups tracked but not yet done in this build

- Retention-deletion scheduled job (the schema and flags exist; the job that actually deletes past
  `retention_days` is not yet implemented).
- Rate limiting middleware.
- API key issuance/revocation: the `api_keys` table, hashing (`generate_api_key`/`verify_api_key`), and
  the `X-API-Key` auth path (`get_scope`) are implemented and tested, but there is no HTTP route to create
  or revoke a key yet — today a key can only be inserted directly. A `POST /api-keys` /
  `DELETE /api-keys/{id}` router plus a Settings-page panel is the concrete next task.
