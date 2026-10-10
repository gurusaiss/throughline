# Security

This is an engineering description of what is implemented and why, not a compliance attestation. Treat it
as the input to a real security review before production launch — see "What this document is not," below.

## 1. Threat model summary

| Actor | Capability | Primary defence |
|---|---|---|
| Anonymous internet user | Hits public endpoints | Rate limiting (`RateLimitMiddleware`, §7), input validation, auth required for all engine/data routes |
| Authenticated user, wrong org | Has a valid token for org A, tries org B's resources | Tenant scoping in every repository query; cross-org access returns 404 (ARCHITECTURE §7) |
| Malicious request content | Text/documents that try to steer the model or the pipeline | Datamarking (ADR-008), heuristic risk detection running before any model, deterministic output validation |
| Compromised database dump | Reads the Postgres data files directly | Passwords: Argon2id. API keys: SHA-256, plaintext shown once. Provider credentials: Fernet-encrypted (ADR credential handling) |
| Malicious/compromised dependency or provider | Supply-chain risk | Provider adapters isolated behind one interface; no provider SDK has access to secrets outside its own adapter |

## 2. Authentication

- **Passwords**: Argon2id via `argon2-cffi` (`app/core/security.py`). Minimum 10 characters, enforced at
  the schema layer. Rehashing on login if parameters have since been strengthened.
- **Sessions**: JWT (HS256), short-lived access token (default 60 min) + longer refresh token (default 30
  days), each carrying an explicit `type` claim so a refresh token cannot be replayed as an access token.
- **API keys**: `tl_sk_` prefix, 256 bits of randomness, stored as a SHA-256 hash with constant-time
  comparison (`hmac.compare_digest`). The plaintext is shown exactly once at creation.
- **Account enumeration**: signup and login return identical error shapes for "already exists" /
  "wrong password" / "no such user" — verified by test (`tests/test_api.py::TestAuth`).

## 3. Authorization and multi-tenancy

- Every tenant-owned table carries `org_id`. Every query in `app/services/persistence.py` and the API
  routers filters by the caller's `org_id` from `TenantScope`.
- Cross-tenant access returns **404, not 403** — confirming a resource exists to a caller who cannot see
  it is itself a leak. Tested explicitly (`TestTenantIsolation` in `tests/test_api.py`).
- Roles (`owner`, `admin`, `member`, `viewer`) gate write and admin actions via `TenantScope.require_write()`
  / `require_admin()`.
- **Known gap**: isolation currently relies on every query going through the scoped repository layer,
  not on database-enforced Row-Level Security. RLS is the Phase 2 hardening step (§8).

## 4. Secrets handling

- Provider API keys supplied by a tenant are encrypted at rest with Fernet (`CredentialCipher`), never
  stored or logged in plaintext.
- The platform's own provider keys (from `.env`) are read once into `ProviderPool` and never logged.
- `Settings.validate_production()` refuses to boot in production with: the default dev secret key, a
  missing encryption key, `DEBUG=true`, `LOG_RAW_PROMPTS=true`, or a wildcard CORS origin. This is
  enforced in `main.py`'s startup path, not merely documented.
- `.gitignore` blocks every `.env*` file except `.env.example`.

## 5. Prompt injection (OWASP LLM Top 10 #1)

Per RESEARCH 6.1, OWASP is explicit that **no defence fully solves this**; the architecture treats it as
defence in depth, not a solved problem:

1. **Datamarking** (ADR-008, `app/security/spotlight.py`): untrusted content (uploaded text, retrieved
   content, another user's project context) is wrapped with a random per-request marker interleaved
   through the text, with an explicit instruction that marked content is data. Quantified in RESEARCH 6.2:
   delimiting alone leaves ~30% attack success; datamarking drops it below 3%.
2. **Heuristic risk detection runs before any model sees the input.** A request cannot argue its own risk
   level down, because the risk signal is computed by regex, not by asking a model to self-report
   (tested in `tests/test_security.py::TestInjectionResistanceInPipeline`).
3. **Deterministic output validation is authoritative over judge/model opinion** (`aggregate.py`) — a
   candidate cannot talk its way past a failed schema or constraint check.
4. **Least-privilege tooling**: the MVP has no tool execution and no filesystem/network access from
   generated content, so there is no privileged action for an injection to escalate into yet. This must be
   re-evaluated before Phase 2 tool execution ships.

## 6. Input/output validation

- Every request body is a Pydantic schema with explicit bounds (`app/api/v1/schemas.py`): max lengths,
  enums, numeric ranges. A blank/whitespace-only `input` is rejected (422).
- Structured model output is **always** re-validated locally against the full JSON Schema regardless of
  what the provider's constrained decoding promises (ADR-010) — provider-reported 98–100% compliance still
  leaves a real failure rate at volume.

## 7. Transport, rate limiting, and dependencies

- CORS is an explicit allow-list (`CORS_ORIGINS_RAW`), never `*` in production (enforced, §4).
- **Rate limiting** (`app/core/middleware.py::RateLimitMiddleware`): Redis-backed fixed-window counters,
  keyed by client IP (or `X-Forwarded-For` only when `TRUST_PROXY_HEADERS=true`, so the identity can't be
  forged by an untrusted caller) and by auth-user when authenticated. Falls back to an approximate
  in-process counter if Redis is unreachable, and fails *open* rather than blocking all traffic on a Redis
  outage. Separate thresholds for auth endpoints, general endpoints, and expensive (execute/compare)
  endpoints — see `backend/.env.example`. `GET /health` and `/health/ready` are never rate limited.
- **Security response headers** (`SecurityHeadersMiddleware`): `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `X-XSS-Protection`, `Permissions-Policy`, and HSTS in production. Content-Security-Policy
  is looser on `/docs`/`/redoc` (which need inline scripts to render Swagger UI) than on the rest of the API.
- Middleware registration order matters and is commented in `app/main.py`: rate limiting and security
  headers are added *before* CORS so CORS stays outermost and even a 429 or an error response still carries
  correct CORS headers.
- Dependencies are pinned via `pyproject.toml` / `package-lock.json`; Dependabot or equivalent should be
  enabled by the operator (not configured in this repo — see `docs/ROADMAP.md`).
- No secrets are ever included in client-side (frontend) code or bundles; the frontend only ever holds a
  user's own session token, in `localStorage`.

## 8. What is deferred, and why (rule 53 test)

| Control | Deferred to | Reason |
|---|---|---|
| Postgres Row-Level Security | Phase 2 | Repository-layer scoping is the MVP guarantee; RLS is defence in depth once the schema has stabilised |
| SSO (SAML/OIDC) | Phase 3 | RESEARCH 8.1: not required for the developer/startup segment targeted at MVP |
| File upload malware scanning | Phase 2, with file support | No file upload exists yet in MVP |
| Automated dependency scanning in CI | Operator setup | Requires the operator's own GitHub security settings; documented in `MANUAL_STEPS.md` |

## 9. What this document is not

This is an engineering description written by the team that built the system, not an independent audit,
a penetration test, or a compliance certification (SOC 2, ISO 27001, HIPAA, etc.). None of those exist for
this codebase. Before handling real user data or regulated data, commission an independent security review
and, if applicable, legal review of EU AI Act / GDPR posture (RESEARCH 6.3) — this is listed explicitly in
`MANUAL_STEPS.md` as something only the operator can authorise and pay for.
