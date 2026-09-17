# API Reference

Base path: `/api/v1`. Interactive OpenAPI docs are served at `/docs` when the backend is running.
Full request/response schemas: `backend/app/api/v1/schemas.py`.

## Authentication

Two mechanisms, resolving to the same `TenantScope` (`app/api/deps.py`):

- **Bearer JWT** — `Authorization: Bearer <access_token>`, obtained from `/auth/login` or `/auth/signup`.
- **API key** — `X-API-Key: tl_sk_...`, for programmatic/integration use.

```
POST /auth/signup   { email, password, display_name?, organization_name? } -> tokens
POST /auth/login    { email, password }                                   -> tokens
POST /auth/refresh  ?refresh_token=...                                     -> tokens
GET  /auth/me                                                              -> current user
```

## Engine — the product

```
POST /optimize   { input, mode?, project_id?, output_schema? }
```
Compiles and optimises the prompt, routes to a model — but never generates. Costs at most one cheap
classification call, never a generation call. Returns the compiled prompt, the routing plan, and the
"why" explanation.

```
POST /execute    { input, mode?, model_id?, candidate_count?, temperature?,
                    max_output_tokens?, enable_judge?, max_cost_usd?, output_schema?, project_id? }
```
The full pipeline: optimise → route → generate → evaluate. `mode` is one of
`auto | fast | balanced | deep | expert`; omit or pass `auto` to let the orchestrator decide.
Response includes `output`, `confidence`, `why`, `routing`, `candidates`, `evaluation`, `cost`, `warnings`.
Every response labels AI-generated content (`"generated_by": "ai"`) per EU AI Act Article 50.

```
POST /compare    { input, compare: "raw_vs_optimized" | "models", model_ids?, project_id? }
```
Runs two or more arms side by side. Returns a `verdict` derived only from automated checks, and a
`caveat` explicitly stating that one run is one sample, not proof (PRODUCT §4).

```
POST /route      { input, mode? }
```
Routing decision only, no execution — useful for integrators evaluating the router in isolation.

```
POST /evaluate?output=<text>   { input, output_schema? }
```
Runs the deterministic checks against output you already have. Free, no LLM call.

## Model catalogue

```
GET /models                       -> all models, with pricing provenance
GET /models/{model_id}
GET /models-status/stale          -> models with unverified or stale pricing (ADR-004)
```

## Prompts (versioned library)

```
GET  /prompts
POST /prompts                                  { name, description?, system_text?, user_text?, task_type?, output_schema? }
GET  /prompts/{id}/versions
POST /prompts/{id}/versions                    { system_text?, user_text?, output_schema?, notes? }
POST /prompts/{id}/activate/{version_id}       -- rollback/rollforward: a pointer change, nothing is edited or lost
```

## History, feedback, analytics

```
GET  /executions            ?limit=&offset=&task_type=
GET  /executions/{id}
POST /feedback               { execution_id, rating: "up"|"down", reason?, comment?, edited_output? }
GET  /analytics              ?days=30
```

Feedback is recorded, never acted on automatically (rule 23) — a single interaction cannot change
production behaviour; it becomes material for a controlled experiment instead.

## Errors

Every error is `{ code, message, detail? }` with an appropriate HTTP status
(`app/core/errors.py`). Notable codes: `budget_exceeded` (402), `no_viable_model` (503),
`provider_rate_limited` (429), `content_filtered` (422), `not_found` (404 — used for cross-tenant access
too, so existence is never confirmed to an unauthorized caller).

## Versioning

The API is versioned in the path (`/api/v1`). Breaking changes will ship as `/api/v2`; `/api/v1` will not
change shape under callers without a version bump.
