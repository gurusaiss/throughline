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

### API keys

```
POST   /api-keys          { name }         -> { id, name, key, key_prefix, created_at, warning }
GET    /api-keys                           -> [{ id, name, key_prefix, created_at, last_used_at, is_active }]
DELETE /api-keys/{key_id}                  -> 204
```
Admin role required. The plaintext `key` is returned exactly once, at creation, and cannot be retrieved
again. An API key cannot be used to create or revoke other API keys, or to change a password — only an
interactive (JWT) session can manage credentials, so a leaked key can't escalate into more keys or account
takeover.

## Engine — the product

```
POST /optimize   { input, mode?, project_id?, output_schema? }
```
Compiles and optimises the prompt, routes to a model — but never generates. Costs at most one cheap
classification call, never a generation call. Returns the compiled prompt, the routing plan, and the
"why" explanation.

```
POST /execute    { input, mode?, model_id?, candidate_count?, temperature?,
                    max_output_tokens?, enable_judge?, max_cost_usd?, output_schema?, project_id?,
                    save_as_prompt? }
```
The full pipeline: optimise → route → generate → evaluate. `mode` is one of
`auto | fast | balanced | deep | expert`; omit or pass `auto` to let the orchestrator decide.
Response includes `output`, `confidence`, `why`, `routing`, `candidates`, `evaluation`, `cost`, `warnings`.
Every response labels AI-generated content (`"generated_by": "ai"`) per EU AI Act Article 50.
Pass `save_as_prompt: "<name>"` to also save the compiled (optimised) prompt as a new versioned prompt in
the library in the same call — the response's `saved_prompt_id` is set when this succeeds.

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

## Account (self-service)

```
PATCH  /account/profile            { display_name?, memory_enabled? }      -> current user
POST   /account/change-password    { current_password, new_password }     -> 204
GET    /account/export                                                    -> { user, memberships, execution_count, note }
DELETE /account                                                           -> 204
```
Acts only on the caller's own record. Password changes require an interactive session (not an API key).
`DELETE /account` refuses if the caller is the sole owner of a workspace that still has other members —
transfer ownership or remove the other members first.

## Admin (requires `owner` or `admin` role)

```
GET    /admin/members                              -> [{ user_id, email, display_name, role, joined_at }]
POST   /admin/members           { email, role? }   -> member (the user must already have an account —
                                                        there is no email-invitation flow yet)
PATCH  /admin/members/{user_id} { role }           -> member
DELETE /admin/members/{user_id}                    -> 204
```
Changing or removing the last `owner` of an organization is refused — an org must always retain at least
one owner able to administer it.

```
GET   /admin/organization                                                 -> org settings
PATCH /admin/organization  { name?, monthly_budget_usd?, retention_days?, store_raw_prompts? }
```
Every change is audit-logged with explicit before/after values, since `store_raw_prompts` in particular is
a privacy-significant toggle.

```
GET /admin/audit-logs   ?limit=&offset=&action=    -> [{ id, occurred_at, action, user_id, resource_type, resource_id, ip_address, detail }]
```

```
GET    /admin/provider-accounts                                   -> [{ id, provider, label, key_last_four, is_active, created_at }]
POST   /admin/provider-accounts   { provider, label?, api_key }   -> provider account
DELETE /admin/provider-accounts/{account_id}                      -> 204
```
Bring-your-own provider keys, scoped to one organization. The key is encrypted at rest (Fernet) and never
returned after creation; the server refuses to create one at all if `ENCRYPTION_KEY` isn't configured,
rather than ever falling back to storing it in plaintext.

## Errors

Every error is `{ code, message, detail? }` with an appropriate HTTP status
(`app/core/errors.py`). Notable codes: `budget_exceeded` (402), `no_viable_model` (503),
`provider_rate_limited` (429), `content_filtered` (422), `not_found` (404 — used for cross-tenant access
too, so existence is never confirmed to an unauthorized caller).

## Versioning

The API is versioned in the path (`/api/v1`). Breaking changes will ship as `/api/v2`; `/api/v1` will not
change shape under callers without a version bump.
