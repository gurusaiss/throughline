# Roadmap

Every deferred feature is tested against rule 53 before being listed: does it solve a real problem, does it
differentiate, does it improve retention or monetisation, is it architecturally required now, what does it
cost to maintain, can it wait. Nothing below was cut for lack of ideas — each row states the reason.

## Shipped (this build)

Auth + multi-tenancy · provider abstraction (Anthropic verified pricing, Groq free-tier fallback,
OpenAI/Google/OpenRouter unverified pricing, offline test provider) · model registry with
operator-updatable pricing and staleness flags · heuristic + LLM intent analysis · prompt
compiler/optimiser with technique-level provenance · rule-based router (single/fallback/parallel/ensemble)
· adaptive orchestrator (FAST/BALANCED/DEEP modes; EXPERT defined, not yet exercised by a real
multi-specialist workload) · deterministic evaluators · LLM judge with position/self-preference/verbosity
mitigations · raw-vs-optimised and model-vs-model compare · prompt versioning with non-destructive rollback
(including saving a compiled prompt directly from an execution) · execution tracing · usage ledger with
pre-flight budget enforcement · exact-match caching · feedback capture · REST API v1 · Next.js UI
(playground, compare, prompts, models, history, analytics, settings, admin) · offline benchmark harness
with an honest refusal to score without a real provider · CI (lint, types, tests, migration check, frontend
build).

**Production hardening (added after the initial MVP):** API key issuance/revocation over HTTP with a
Settings-page panel · an Admin page (member management with a last-owner guard, organization settings,
audit log, encrypted bring-your-own provider keys) · account self-service (profile editing, password
change, data export, account deletion) · Redis-backed rate limiting with an in-process fallback · security
response headers · a retention-purge job that deletes expired execution content while never touching the
billing ledger or audit log · tenant-supplied provider keys encrypted at rest (Fernet).

## Phase 2 — next

| Item | Why now, why not yet |
|---|---|
| Datasets & experiments UI | The A/B harness exists (`app/benchmark/`) but has no UI for user-authored datasets. Real differentiator once there's traffic to learn from; premature before then. |
| GEPA-style offline prompt optimisation | RESEARCH 2.1: reflective evolution beats RL at far lower cost, and is the natural next step for the Prompt Lab. Needs a dataset per task, which users don't have on day one — hence Phase 2, not MVP. |
| File upload + retrieval (RAG) | Real user need (document analysis, PDF Q&A) but needs chunking/retrieval infra and storage; not required to prove the core orchestration thesis. |
| Semantic cache — shadow mode | RESEARCH 7.1: a false hit is invisible and confidently wrong. Ships logging would-be hits only, measured against real traffic, before it ever serves an answer (ADR-005). |
| Postgres Row-Level Security | Defence in depth once the schema is stable; the repository-scoping guarantee is the MVP-level control. |
| Stripe billing integration | `UsageRecordRow` is the system of record and exporter-ready; wiring an actual payment processor is a business decision requiring a live Stripe account (see `MANUAL_STEPS.md`). |
| Email-based team invitations | The Admin page can add an existing user to a workspace by email, but there's no invite-a-stranger flow yet — that needs a transactional email provider, which is a business decision (see `MANUAL_STEPS.md` §3.5). |

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

- Email-based invitations for teammates who don't yet have an account (see Phase 2 table above).
- The retention-purge job (`app/services/retention.py`) runs via a manual CLI invocation
  (`python -m app.services.retention`); it is not yet wired to a scheduler (cron/systemd timer/platform
  scheduled job) — that's an operator decision for the deployment target (see `MANUAL_STEPS.md`).
