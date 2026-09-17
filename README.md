# Throughline

*An adaptive AI execution layer — intent → optimised request → routed model → evaluated result.*

> Name is provisional; trademark not cleared. See `docs/RESEARCH.md` §10.4.

## What this is

Not a prompt generator. Throughline turns a plain-language request into an optimised, execution-ready
prompt; routes it to whichever model actually fits the job; generates one answer or several candidates
depending on how much the request warrants; and evaluates what comes back before returning it — with the
evaluation's provenance and confidence attached, never a bare "accuracy" claim.

The core product principle: **the system decides how hard to try.** A trivial request ("translate this")
costs exactly one model call. A hard one ("design a system for 10M users") gets compared candidates, a
judge pass, and a visible evaluation. See `docs/PRODUCT.md` for the full positioning and
`docs/ARCHITECTURE.md` for how the orchestration policy actually works.

## Status

MVP, built and verified locally against real Postgres, Redis and a live browser session (not just unit
tests) — see the "What was verified" section below. **Not yet deployed anywhere.** No API keys are
configured in this build. See `MANUAL_STEPS.md` for exactly what a human needs to do next.

## Architecture at a glance

```
User → Next.js frontend → FastAPI (/api/v1) → Orchestrator
                                                  ├── Signal extraction (free, heuristic)
                                                  ├── Intent analysis (cheap model, conditional)
                                                  ├── Prompt compiler / optimiser
                                                  ├── Router (rule-based, explainable)
                                                  ├── Provider adapters (Anthropic / OpenAI / Google / OpenRouter / offline)
                                                  ├── Evaluation (deterministic + LLM judge)
                                                  └── Trace + cost ledger
                                                → PostgreSQL + Redis
```

Full diagrams and rationale: `docs/ARCHITECTURE.md`, `docs/AI_PIPELINE.md`, `docs/DECISIONS.md` (ADRs).

## Repository layout

```
backend/       FastAPI + SQLAlchemy + Alembic. Python 3.11, uv-managed.
frontend/      Next.js 16 (App Router) + TypeScript + Tailwind.
docs/          Product, architecture, research, security, API, database, evaluation, roadmap docs.
.github/       CI workflow (lint, types, tests, migration check, frontend build).
docker-compose.yml   Full local stack: Postgres + Redis + backend + frontend.
```

## Local development

### Backend

```bash
cd backend
uv venv && uv pip install -e ".[dev]"
cp .env.example .env   # fill in SECRET_KEY at minimum; see MANUAL_STEPS.md
alembic upgrade head
uvicorn app.main:app --reload
```

Runs fully with **zero provider API keys** — the offline deterministic provider handles every request, so
the whole pipeline (routing, evaluation, tracing, cost accounting) is exercisable and testable without
spending money. Add a real key to `.env` to get real model output.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

### Or the whole stack at once

```bash
docker compose up --build
docker compose run --rm backend alembic upgrade head   # once, after the first `up`
```

## Testing

```bash
cd backend
pytest tests/ -q              # 173 tests, no API keys or network required
ruff check app/ tests/
mypy app/
python -m app.benchmark.runner   # policy conformance (free) + quality A/B (needs a real provider key)
```

```bash
cd frontend
npm run lint
npx tsc --noEmit
npm run build
```

All of the above pass clean in this build (see the QA summary in the session record / PR description).

## Providers

Only **Anthropic's** pricing in `backend/app/registry/catalog.yaml` is verified against primary
documentation as of this build. OpenAI, Google and OpenRouter entries are marked `verified: false` because
their pricing pages were unreachable from the research environment's network policy — see
`docs/RESEARCH.md` §3 for the full account, including a documented case where a third-party pricing source
was wrong by 50% on a flagship model. **Verify before relying on non-Anthropic cost figures in
production.**

## Security

`docs/SECURITY.md` — authentication, tenant isolation (tested, not just designed), secret handling, prompt
injection defences with cited effectiveness numbers, and an explicit list of what is deferred and why.
This is an engineering description, not an independent audit — see that document's final section.

## Documentation index

| Doc | Contents |
|---|---|
| `docs/RESEARCH.md` | Competitive/provider/security research, each finding tagged by how it was verified |
| `docs/PRODUCT.md` | Positioning, product principle, MVP scope |
| `docs/ARCHITECTURE.md` | System diagrams, orchestration policy, failure handling |
| `docs/DECISIONS.md` | ADRs — every major choice, alternatives considered, consequences |
| `docs/AI_PIPELINE.md` | Stage-by-stage walkthrough of the orchestrator |
| `docs/EVALUATION.md` | Evaluation methodology, judge bias mitigations, the A/B harness |
| `docs/DATABASE.md` | Schema, indexing, migrations, privacy-by-design |
| `docs/API.md` | Endpoint reference |
| `docs/SECURITY.md` | Threat model, controls, deferred items |
| `docs/ROADMAP.md` | What's shipped, what's next, what was rejected and why |
| `MANUAL_STEPS.md` | **What a human must do** — keys, deployment, GitHub, legal review |

## GitHub

This repository has **no remote configured**. Per the task instructions, nothing is pushed anywhere until
you provide a GitHub repository URL and explicitly authorise the push. See `MANUAL_STEPS.md`.
