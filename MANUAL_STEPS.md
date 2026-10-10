# Manual Steps — What You Need To Do

Everything in this file requires a decision, a credential, a payment method, or an account that only you
can provide. Nothing here was skipped out of laziness — each item is something the system correctly
refused to fabricate or guess on your behalf.

The code is complete — including the admin console, user self-service account/API-key management, security
hardening (rate limiting, security headers, encrypted bring-your-own provider keys, audit logging) and a
free-tier model fallback (Groq) — and verified running end-to-end (real Postgres, real Redis, real browser
session) with **zero paid model API keys required to see real model output**. Everything below is what
turns it from "correct and running" into "live for real users."

---

## 0. Before anything else: where is the code right now?

It is **only on this machine**, at `/home/user/throughline`, as a local git repository with **no remote
configured**. Per your instruction, I have not pushed it anywhere and will not until you:

1. Give me a GitHub repository URL (new or existing).
2. Explicitly say to push.

When you do, I will: inspect the repo, verify the branch, confirm you own/can write to it, review exactly
what will be committed (checking no secrets are included — `.gitignore` already excludes all `.env*`
files), run the full test suite, show you the diff, then commit and push only after you confirm.

**Until then, back this up yourself if you care about not losing it** — e.g. `tar` the directory, or copy
it off this machine. It will not persist if this session/environment is torn down.

---

## 1. Required before ANY real (non-offline) usage

### 1.1 Generate secrets

```bash
# SECRET_KEY (JWT signing) — put in backend/.env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# ENCRYPTION_KEY (encrypts tenant-supplied provider API keys at rest) — put in backend/.env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Without `ENCRYPTION_KEY`, the app still runs, but any feature that stores a tenant's own provider key
(bring-your-own-key, via the Admin page's "Provider accounts" panel) will refuse to save a key rather than
store it unencrypted — fine for MVP if you're only using platform-level keys (§1.2).

### 1.2 Get at least one AI provider API key

The platform works with **zero** keys (offline provider only — good for demos of the mechanics, useless
for real answers). To get real model output fastest and for free, use Groq:

| Provider | Where | Cost | Verified pricing in this build? |
|---|---|---|---|
| **Groq** (recommended first — genuinely free) | https://console.groq.com/keys | Free tier, no credit card, ~30 req/min & ~14,400 req/day | ⚠️ Paid-tier price unverified, but the free tier itself was confirmed live during this build |
| Anthropic | https://console.anthropic.com | Paid | ✅ Yes — checked against primary docs during this build |
| OpenAI | https://platform.openai.com | Paid | ⚠️ No — their pricing page was unreachable from the build environment's network policy |
| Google (Gemini) | https://aistudio.google.com | Paid | ⚠️ No — same reason |
| OpenRouter | https://openrouter.ai | Paid | ⚠️ No — same reason |

Groq is wired as a fallback-tier provider (not primary for production volume, given its rate limits) but
it's the fastest path to seeing real model output with zero spend — sign up, copy a key, and you're done.

**Action required for anything except Anthropic:** before you rely on cost figures for Groq/OpenAI/Google/
OpenRouter models in production, open `backend/app/registry/catalog.yaml` and check each `pricing:` block
against that provider's current, official pricing page, then set `verified: true` and update
`verified_at`. This isn't optional busywork — `docs/RESEARCH.md` §3.2 documents a real case where a
third-party pricing source was wrong by 50% on a flagship model two weeks after a price change. The system
already surfaces every unverified price in the UI (Models page) and via `GET /models-status/stale`, so you
will see exactly which ones need this.

Put the key(s) in `backend/.env`:
```
GROQ_API_KEY=gsk_...
ANTHROPIC_API_KEY=sk-ant-...
```

Alternatively, once you've signed in as an org owner/admin, you can add provider keys per-workspace from
the **Admin page → Provider accounts** panel in the running app instead of editing `.env` — those keys are
encrypted at rest with `ENCRYPTION_KEY` (§1.1) and scoped to that one organization.

### 1.3 Decide your data-retention default

`backend/.env`: `LOG_RAW_PROMPTS` and each organization's `store_raw_prompts` column control whether user
prompt/response content is stored at all (default: **not stored** — only metadata, cost, and evaluation
results). Decide this before launch; changing it later is a policy decision your users should know about.

---

## 2. Required before deploying anywhere public

### 2.1 Pick a deployment target

Nothing is deployed anywhere yet. Reasonable options, roughly cheapest/simplest → most control:

- **Railway / Render / Fly.io** — point at `docker-compose.yml` or the two Dockerfiles directly; managed
  Postgres/Redis add-ons available. Fastest path to a public URL.
- **A VPS you control** (Hetzner, DigitalOcean, etc.) — run `docker compose up -d` there directly.
- **AWS/GCP/Azure** — most control, most setup (RDS/Cloud SQL for Postgres, ElastiCache/Memorystore for
  Redis, ECS/Cloud Run/App Service for the containers).

Whichever you pick, you will need to:
- Set `ENVIRONMENT=production` in the backend's environment. **The app will refuse to start** if
  `SECRET_KEY` is still the dev default, `DEBUG=true`, `ENCRYPTION_KEY` is unset, `LOG_RAW_PROMPTS=true`,
  or `CORS_ORIGINS_RAW` is `*` — this is enforced in code (`Settings.validate_production()`), not just
  documented, so a misconfigured production deploy fails loudly at boot rather than silently.
- Set `CORS_ORIGINS_RAW` to your real frontend domain(s).
- Set `NEXT_PUBLIC_API_URL` (frontend build arg) to your real backend URL — this is baked in at **build
  time** for Next.js, so it must be set before `npm run build` / the Docker image build, not just at
  runtime.
- Provision managed Postgres + Redis, or run them yourself with proper backups.
- Run `alembic upgrade head` against the production database **once**, before traffic hits it — the
  container does not auto-migrate on start (deliberately: an auto-migrating web process under concurrent
  rollout is how schemas get corrupted).
- Put a TLS-terminating reverse proxy (Caddy, nginx, or your platform's built-in HTTPS) in front of both
  services. Neither Dockerfile does TLS itself.
- If that reverse proxy is one you control and it strips any client-supplied `X-Forwarded-For` header
  before setting its own, set `TRUST_PROXY_HEADERS=true` so rate limiting identifies real client IPs
  instead of the proxy's. Leave it `false` (the default) if you're not certain — a wrong `true` here lets
  any caller forge their rate-limit identity and bypass limits entirely.
- Rate limiting (`ENABLE_RATE_LIMITING` and the three `RATE_LIMIT_*_PER_MINUTE` settings in
  `backend/.env.example`) is on by default and backed by Redis when reachable. Review the default numbers
  (10/min auth, 120/min general, 20/min expensive) against your expected traffic before launch — they're
  reasonable defaults, not numbers tuned to your actual usage.

### 2.2 Domain name and DNS

You'll need one if you want a real URL instead of a platform-assigned subdomain. Buy it wherever, point DNS
at your deployment target per that provider's instructions.

### 2.3 Set a real monthly budget default

`DEFAULT_ORG_MONTHLY_BUDGET_USD` in `backend/.env` currently defaults to $25. The pre-flight budget check
(`Budget.check`) is real and tested — a request that would exceed budget is refused **before** any provider
is called — but the number itself is a placeholder. Set it to whatever you're actually willing to spend per
tenant before opening signups.

---

## 3. Business / legal — only you can decide these

### 3.1 Product name

"Throughline" is used throughout this build as a **provisional, uncleared** name (see
`docs/RESEARCH.md` §10.4 and `docs/PRODUCT.md`). Before any public launch:
- Search USPTO (or your jurisdiction's equivalent) and check domain availability.
- If you want to keep it, register the trademark and the domain.
- If you want to change it, tell me the new name and I'll do a global rename across code, docs, and UI.

### 3.2 Terms of Service / Privacy Policy

Not written — this requires legal judgment about your specific business (what data you collect, which
providers see user content, your jurisdiction, refund policy, etc.). The architecture supports honest
disclosure (AI-generated content is labelled, data flow to third-party providers is recorded per
execution, retention is configurable) but the actual legal documents need a lawyer or at minimum a
template service (e.g. Termly, Ironclad) reviewed by one.

### 3.3 EU AI Act / GDPR review, if you'll have EU users

`docs/RESEARCH.md` §6.3 and `docs/SECURITY.md` §9 flag this explicitly: Article 50 transparency
obligations have applied since 2026-08-02. The engineering groundwork exists (AI-content labelling,
per-execution provider data-flow recording, configurable retention) but **this is not a substitute for
legal review** before processing EU personal data at any real scale.

### 3.4 Payment processing (when you're ready to charge)

No payment integration exists yet (deliberately deferred — see `docs/ROADMAP.md`). When you want to
charge:
1. Create a Stripe (or equivalent) account.
2. Decide your pricing tiers (research in `docs/RESEARCH.md` §8 and `docs/PRODUCT.md` §8 gives you a
   starting point, not a final answer — provider costs and your margin need real traffic data first).
3. Tell me and I'll wire `UsageRecordRow` (already the system of record, already exporter-shaped) to
   Stripe's metered billing.

### 3.5 Inviting teammates (current limitation)

The Admin page's "Members" panel can add a teammate to your workspace by email, but only if that person
**already has a Throughline account** — there is no email-invitation system yet (that needs a configured
transactional email service, which is a decision only you can make: which provider, what the email looks
like, from what address). Until then, the flow is: ask them to sign up first, then add them by email from
the Admin page. If you want a real invite flow (a link that lets someone join without signing up first),
tell me and I'll build it once you've picked an email provider (e.g. Resend, Postmark, SES).

### 3.6 A support/contact email and monitoring

You'll want an email address for user support and an error-monitoring service (Sentry or similar) wired
into both the backend and frontend before real users hit it — neither is configured in this build.

---

## 4. Nice to verify yourself before trusting this document blindly

- I ran the full backend test suite (224 tests), lint (ruff), and type-check (mypy) — all clean on
  `backend/app`. (The only ruff findings anywhere in the repo are long `sa.Column(...)` lines in the
  auto-generated initial Alembic migration file, which is machine-generated and not hand-edited code.)
- I ran the offline policy benchmark (37/37 checks) proving the orchestration spends 1 call on a trivial
  request and up to 5 on a hard one, exactly as designed.
- I stood up real PostgreSQL 16 and Redis 7 locally, ran a real Alembic migration against Postgres, and
  hit the live API end-to-end with `curl` and a real browser. Along the way this caught and let me fix two
  real bugs: an insert-ordering issue that only manifests against a database that enforces foreign keys
  (SQLite doesn't by default — I hardened the test suite to enforce them too, so this class of bug can't
  recur silently), and an ORM cascade-delete bug where deleting a user or organization failed against real
  Postgres because SQLAlchemy's default behavior tried to null out a child row's foreign key instead of
  letting the database's own `ON DELETE CASCADE` handle it.
- I built the frontend for production (`npm run build`, clean) and then drove the **entire new surface**
  with a real headless Chromium browser (Playwright) against the live backend: signup, running a request
  with "save as prompt" checked, confirming it appeared in the prompt library, the Compare page's new
  model-vs-model mode, every Settings panel (profile edit, change password, API key creation showing the
  one-time plaintext key, data export, account deletion), and the entire Admin page (organization settings
  save, adding an existing user as a member, the deliberate error message when adding someone without an
  account, provider accounts, audit log) — zero JavaScript console errors throughout.
- Still true: I have **not** tested against a paid model provider (no API key was available in the build
  environment). I did confirm Groq's free-tier endpoint and request/response shape are wired correctly
  (provider adapter tests mock the exact URL), but getting a `GROQ_API_KEY` (§1.2, free, under a minute) and
  running one real request is the one thing in this entire build I genuinely cannot verify for you myself.

If you want, I can walk through any of the above with you live, or re-run the benchmark the moment you add
a real provider key so you get an actual quality A/B instead of just the policy conformance check.
