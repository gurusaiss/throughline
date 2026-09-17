"""API integration tests.

Runs against a real (SQLite) database and the real app, with the offline provider. No network, no keys.

The tenant-isolation tests are the ones that matter most: a cross-org leak is the failure that ends a
B2B product.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.core.config import Settings
from app.db.models import Base
from app.db.session import get_session
from app.main import create_app
from app.pipeline.cache import ExactMatchCache, InMemoryCacheBackend
from app.pipeline.orchestrator import Orchestrator
from app.providers import ProviderPool
from app.registry.registry import ModelRegistry


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    settings = Settings(
        environment="test",
        secret_key="test-secret-key-long-enough-for-hs256-signing",
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="memory://",
        anthropic_api_key="",
        openai_api_key="",
        google_api_key="",
        openrouter_api_key="",
    )

    engine = create_async_engine(settings.database_url)

    # SQLite does not enforce foreign keys unless explicitly told to. Production runs on Postgres, which
    # always enforces them — running tests without this pragma let a real insert-order bug (Evaluation
    # flushed before its parent Execution) pass 173 green tests and only surface against a real Postgres
    # instance during manual smoke testing. Enabling it here closes that gap for every test in this file.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()
    registry = ModelRegistry.from_catalog()
    pool = ProviderPool(settings, credentials={})
    app.state.settings = settings
    app.state.registry = registry
    app.state.pool = pool
    app.state.orchestrator = Orchestrator(
        settings=settings,
        registry=registry,
        pool=pool,
        cache=ExactMatchCache(InMemoryCacheBackend(), tenant_id="test"),
    )

    async def _session_override() -> AsyncIterator:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[deps.get_settings] = lambda: settings

    # lifespan is skipped: state is injected above so no engine/Redis connection is attempted.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    await engine.dispose()


async def signup(client: AsyncClient, email: str = "ada@example.com") -> dict[str, str]:
    resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "correct-horse-battery", "display_name": "Ada"},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


class TestHealth:
    async def test_health_reports_configuration(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["models"] > 0


class TestAuth:
    async def test_signup_returns_tokens(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/signup",
            json={"email": "new@example.com", "password": "correct-horse-battery"},
        )
        assert resp.status_code == 201
        assert resp.json()["access_token"]

    async def test_login_succeeds_with_correct_password(self, client: AsyncClient) -> None:
        await signup(client, "login@example.com")
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@example.com", "password": "correct-horse-battery"},
        )
        assert resp.status_code == 200

    async def test_login_fails_with_wrong_password(self, client: AsyncClient) -> None:
        await signup(client, "wrong@example.com")
        resp = await client.post(
            "/api/v1/auth/login", json={"email": "wrong@example.com", "password": "not-the-password"}
        )
        assert resp.status_code == 401

    async def test_unknown_email_and_wrong_password_are_indistinguishable(
        self, client: AsyncClient
    ) -> None:
        """Account enumeration defence: both paths must return the same status and message."""
        await signup(client, "exists@example.com")
        wrong_pw = await client.post(
            "/api/v1/auth/login", json={"email": "exists@example.com", "password": "bad-password-here"}
        )
        no_user = await client.post(
            "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "bad-password-here"}
        )
        assert wrong_pw.status_code == no_user.status_code == 401
        assert wrong_pw.json()["message"] == no_user.json()["message"]

    async def test_duplicate_signup_does_not_confirm_existing_account(
        self, client: AsyncClient
    ) -> None:
        await signup(client, "dupe@example.com")
        resp = await client.post(
            "/api/v1/auth/signup",
            json={"email": "dupe@example.com", "password": "correct-horse-battery"},
        )
        assert resp.status_code == 422
        assert "already" not in resp.json()["message"].lower()

    async def test_protected_route_requires_auth(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/auth/me")).status_code == 401

    async def test_weak_password_is_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/signup", json={"email": "weak@example.com", "password": "short"}
        )
        assert resp.status_code == 422


class TestEngine:
    async def test_optimize_returns_prompt_without_executing(self, client: AsyncClient) -> None:
        headers = await signup(client, "opt@example.com")
        resp = await client.post(
            "/api/v1/optimize",
            json={"input": "write a professional email asking for an internship"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "prompt_only"
        assert body["why"]["mode"]
        assert body["why"]["rationale"]

    async def test_execute_returns_answer_with_provenance(self, client: AsyncClient) -> None:
        headers = await signup(client, "exec@example.com")
        resp = await client.post(
            "/api/v1/execute", json={"input": "What is 2+2?"}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["output"]
        # EU AI Act Art. 50: AI-generated content must be labelled as such.
        assert body["generated_by"] == "ai"
        assert body["confidence"] in ("low", "moderate", "high")
        assert body["confidence_note"]
        assert body["cost"]["llm_calls"] >= 1

    async def test_execute_explains_its_own_decisions(self, client: AsyncClient) -> None:
        headers = await signup(client, "why@example.com")
        resp = await client.post(
            "/api/v1/execute",
            json={"input": "Design a distributed system for 10M users and plan the migration"},
            headers=headers,
        )
        why = resp.json()["why"]
        assert why["mode_reason"]
        assert why["applied"]
        for technique in why["applied"]:
            # Every applied technique must justify itself, for the UI and for offline audit.
            assert technique["reason"]
            assert technique["trigger"]

    async def test_compare_returns_both_arms_and_declines_to_overclaim(
        self, client: AsyncClient
    ) -> None:
        headers = await signup(client, "cmp@example.com")
        resp = await client.post(
            "/api/v1/compare",
            json={"input": "write a blog post about remote work", "compare": "raw_vs_optimized"},
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert {a["label"] for a in body["arms"]} == {"raw", "optimized"}
        assert body["verdict"]
        assert "one sample" in body["caveat"]

    async def test_evaluate_endpoint_checks_external_output(self, client: AsyncClient) -> None:
        headers = await signup(client, "eval@example.com")
        resp = await client.post(
            "/api/v1/evaluate",
            params={"output": "not json at all"},
            json={
                "input": "extract the name as JSON",
                "output_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
            headers=headers,
        )
        assert resp.status_code == 200
        checks = resp.json()
        assert any(c["name"] == "json_valid" and c["status"] == "fail" for c in checks)

    async def test_blank_input_is_rejected(self, client: AsyncClient) -> None:
        headers = await signup(client, "blank@example.com")
        resp = await client.post("/api/v1/execute", json={"input": "   "}, headers=headers)
        assert resp.status_code == 422


class TestCatalog:
    async def test_models_expose_pricing_provenance(self, client: AsyncClient) -> None:
        """A price the operator cannot trust must say so at the point of use (ADR-004)."""
        headers = await signup(client, "cat@example.com")
        resp = await client.get("/api/v1/models", headers=headers)
        assert resp.status_code == 200
        models = {m["id"]: m for m in resp.json()}

        verified = models["claude-sonnet-5"]["pricing"]
        assert verified["verified"] is True
        assert verified["source"].startswith("https://")
        assert verified["staleness_warning"] == ""

        unverified = models["gemini-3.8-flash"]["pricing"]
        assert unverified["verified"] is False
        assert "unverified" in unverified["staleness_warning"].lower()

    async def test_stale_pricing_endpoint_lists_unverified_models(
        self, client: AsyncClient
    ) -> None:
        headers = await signup(client, "stale@example.com")
        resp = await client.get("/api/v1/models-status/stale", headers=headers)
        assert resp.status_code == 200
        assert any(not m["pricing"]["verified"] for m in resp.json())


class TestPrompts:
    async def test_create_and_version_a_prompt(self, client: AsyncClient) -> None:
        headers = await signup(client, "pr@example.com")
        created = await client.post(
            "/api/v1/prompts",
            json={"name": "Support reply", "system_text": "You are helpful.", "user_text": "{{query}}"},
            headers=headers,
        )
        assert created.status_code == 201
        prompt_id = created.json()["id"]
        assert created.json()["active_version"]["version"] == 1

        v2 = await client.post(
            f"/api/v1/prompts/{prompt_id}/versions",
            json={"system_text": "You are concise.", "user_text": "{{query}}", "notes": "tighter"},
            headers=headers,
        )
        assert v2.status_code == 201
        assert v2.json()["version"] == 2
        assert v2.json()["is_active"] is True

    async def test_rollback_activates_an_earlier_version_without_mutating_it(
        self, client: AsyncClient
    ) -> None:
        headers = await signup(client, "rb@example.com")
        created = await client.post(
            "/api/v1/prompts", json={"name": "P", "system_text": "v1"}, headers=headers
        )
        prompt_id = created.json()["id"]
        v1_id = created.json()["active_version"]["id"]
        await client.post(
            f"/api/v1/prompts/{prompt_id}/versions", json={"system_text": "v2"}, headers=headers
        )

        rolled = await client.post(
            f"/api/v1/prompts/{prompt_id}/activate/{v1_id}", headers=headers
        )
        assert rolled.status_code == 200
        assert rolled.json()["system_text"] == "v1"

        versions = await client.get(f"/api/v1/prompts/{prompt_id}/versions", headers=headers)
        # Both versions survive; rollback is a pointer change, not an edit.
        assert len(versions.json()) == 2


class TestHistoryAndFeedback:
    async def test_execution_is_recorded_and_retrievable(self, client: AsyncClient) -> None:
        headers = await signup(client, "hist@example.com")
        run = await client.post("/api/v1/execute", json={"input": "What is 2+2?"}, headers=headers)
        execution_id = run.json()["execution_id"]

        listed = await client.get("/api/v1/executions", headers=headers)
        assert any(e["id"] == execution_id for e in listed.json())

        detail = await client.get(f"/api/v1/executions/{execution_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["trace"]["spans"]

    async def test_raw_content_is_not_retained_by_default(self, client: AsyncClient) -> None:
        """Privacy default: metadata yes, user content no (rule 28)."""
        headers = await signup(client, "priv@example.com")
        run = await client.post(
            "/api/v1/execute", json={"input": "my secret business plan"}, headers=headers
        )
        detail = await client.get(
            f"/api/v1/executions/{run.json()['execution_id']}", headers=headers
        )
        body = detail.json()
        assert body["content_retained"] is False
        assert body["input"] is None
        assert body["output"] is None

    async def test_feedback_is_recorded_but_not_acted_on(self, client: AsyncClient) -> None:
        headers = await signup(client, "fb@example.com")
        run = await client.post("/api/v1/execute", json={"input": "What is 2+2?"}, headers=headers)
        resp = await client.post(
            "/api/v1/feedback",
            json={
                "execution_id": run.json()["execution_id"],
                "rating": "down",
                "reason": "incorrect",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert "controlled experiments" in resp.json()["note"]

    async def test_analytics_separates_orchestration_from_generation(
        self, client: AsyncClient
    ) -> None:
        headers = await signup(client, "an@example.com")
        await client.post("/api/v1/execute", json={"input": "What is 2+2?"}, headers=headers)
        resp = await client.get("/api/v1/analytics", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["executions"] >= 1
        assert "orchestration_cost_usd" in body
        assert body["budget_limit_usd"] > 0


class TestTenantIsolation:
    """The failure that ends a B2B product. Tested explicitly, not assumed."""

    async def test_one_org_cannot_read_another_orgs_execution(self, client: AsyncClient) -> None:
        alice = await signup(client, "alice@example.com")
        bob = await signup(client, "bob@example.com")

        run = await client.post(
            "/api/v1/execute", json={"input": "alice's private question"}, headers=alice
        )
        execution_id = run.json()["execution_id"]

        resp = await client.get(f"/api/v1/executions/{execution_id}", headers=bob)
        # 404 not 403: a 403 would confirm the resource exists (ARCHITECTURE §7).
        assert resp.status_code == 404

    async def test_one_org_cannot_see_another_orgs_prompts(self, client: AsyncClient) -> None:
        alice = await signup(client, "alice2@example.com")
        bob = await signup(client, "bob2@example.com")
        await client.post("/api/v1/prompts", json={"name": "Alice secret"}, headers=alice)

        listed = await client.get("/api/v1/prompts", headers=bob)
        assert listed.json() == []

    async def test_one_org_cannot_version_another_orgs_prompt(self, client: AsyncClient) -> None:
        alice = await signup(client, "alice3@example.com")
        bob = await signup(client, "bob3@example.com")
        created = await client.post("/api/v1/prompts", json={"name": "P"}, headers=alice)
        prompt_id = created.json()["id"]

        resp = await client.post(
            f"/api/v1/prompts/{prompt_id}/versions", json={"system_text": "hijacked"}, headers=bob
        )
        assert resp.status_code == 404

    async def test_analytics_are_scoped_to_the_callers_org(self, client: AsyncClient) -> None:
        alice = await signup(client, "alice4@example.com")
        bob = await signup(client, "bob4@example.com")
        for _ in range(2):
            await client.post("/api/v1/execute", json={"input": "What is 2+2?"}, headers=alice)

        assert (await client.get("/api/v1/analytics", headers=bob)).json()["executions"] == 0
        assert (await client.get("/api/v1/analytics", headers=alice)).json()["executions"] == 2
