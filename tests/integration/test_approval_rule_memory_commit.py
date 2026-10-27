"""Real owner HTTP transaction versus independently committed standing-rule facts.

REQ-module-approvals-002: descriptive memory cannot survive a rolled-back owner
approval/rule/audit. These are database tests, not authentication or daemon
execution evidence. Only roster settings, embeddings and the unavailable MCP
peer are fixture collaborators; route, SQL operations, audit and memory storage
are the production implementations.
"""

from __future__ import annotations

import asyncio
import shutil
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.routers import approvals as routes
from butlers.db import register_jsonb_codec
from butlers.modules.memory import tools as memory_tools
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import _DomainOwnerState
from tests.audit_helpers import clear_audit_log_for_test

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_KEY = "synthetic-rule-memory-domain-key"
_TOOL_ARGS = {"chat_id": "test-recipient", "text": "A synthetic approval"}


class _EmbeddingEngine:
    model_name = "approval-rule-commit-test"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, _text: str) -> list[float]:
        self.calls += 1
        return [0.1] * 384


class _DbManager:
    butler_names = ["home"]

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def pool(self, butler_name: str) -> asyncpg.Pool:
        assert butler_name == "home"
        return self._pool

    def butlers_with_module(self, module_name: str) -> list[str]:
        return self.butler_names if module_name in {"approvals", "memory"} else []


class _UnavailableDaemon:
    """Exercise the actual dispatch refusal without opening a peer connection."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_client(self, butler_name: str):
        assert butler_name == "home"
        self.calls += 1
        raise ConnectionError("synthetic owning daemon unavailable")


@pytest.fixture(autouse=True)
def admitted_standing_rule_boundary(monkeypatch):
    """Retain this owner's non-auth invariants after explicit D13 admission.

    No factor/nonce/role evidence is claimed here; the dedicated migrated
    dashboard-stepup owner executes that authority with real signed assertions.
    """
    from tests.approval_stepup_helpers import admit_rule_boundary_for_non_auth_owner

    admit_rule_boundary_for_non_auth_owner(monkeypatch)


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "approvals"],
        schemas={"memory": "home_mem"},
    )


async def _close_pool(pool: asyncpg.Pool) -> None:
    await asyncio.wait_for(pool.close(), timeout=10)


@pytest.fixture
async def database(migrated_db_url: str):
    async with AsyncExitStack() as cleanup:
        owner = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
        )
        cleanup.push_async_callback(_close_pool, owner)
        observer = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=1, init=register_jsonb_codec
        )
        cleanup.push_async_callback(_close_pool, observer)
        try:
            await clear_audit_log_for_test(owner)
            await owner.execute(
                "TRUNCATE home_mem.memory_links, home_mem.facts, "
                "approval_events, pending_actions, approval_rules, "
                "autonomy_approval_history, autonomy_suggestions CASCADE"
            )
            # Simultaneously checked out connections prove an independent backend,
            # rather than a read through the request's uncommitted transaction.
            async with owner.acquire() as first, observer.acquire() as second:
                assert await first.fetchval("SELECT pg_backend_pid()") != await second.fetchval(
                    "SELECT pg_backend_pid()"
                )
            yield owner, observer
        finally:
            # Fault injectors belong only to this disposable migrated database.
            # ExitStack still closes both pools if fault cleanup itself fails.
            await owner.execute(
                "DROP TRIGGER IF EXISTS fixture_rule_audit_fault ON public.audit_log"
            )
            await owner.execute(
                "DROP TRIGGER IF EXISTS fixture_rule_memory_fault ON home_mem.facts"
            )
            await owner.execute("DROP FUNCTION IF EXISTS public.fixture_rule_audit_fault()")
            await owner.execute("DROP FUNCTION IF EXISTS public.fixture_rule_memory_fault()")


async def _inject_fault(pool: asyncpg.Pool, boundary: str) -> None:
    if boundary in {"audit_failure", "commit_failure"}:
        # The deferred variant raises at COMMIT, after append returned. Both
        # require that the real approval and standing-rule SQL already ran.
        code = "P4501" if boundary == "audit_failure" else "P4502"
        await pool.execute(
            """
            CREATE FUNCTION public.fixture_rule_audit_fault() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pending_actions
                               WHERE id = NEW.target::uuid AND status = 'approved')
                   OR NOT EXISTS (SELECT 1 FROM approval_rules
                                  WHERE created_from = NEW.target::uuid) THEN
                    RAISE EXCEPTION USING ERRCODE = 'P4500',
                        MESSAGE = 'fixture owner transition did not reach audit';
                END IF;
                RAISE EXCEPTION USING ERRCODE = '"""
            + code
            + """',
                    MESSAGE = 'synthetic owning transaction refusal';
            END $$
            """
        )
        trigger = (
            "CREATE TRIGGER fixture_rule_audit_fault BEFORE INSERT"
            if boundary == "audit_failure"
            else "CREATE CONSTRAINT TRIGGER fixture_rule_audit_fault AFTER INSERT"
        )
        deferred = " DEFERRABLE INITIALLY DEFERRED" if boundary == "commit_failure" else ""
        await pool.execute(
            trigger
            + " ON public.audit_log"
            + deferred
            + " FOR EACH ROW WHEN (NEW.action = 'approval.approve') "
            "EXECUTE FUNCTION public.fixture_rule_audit_fault()"
        )
    elif boundary == "memory_failure":
        await pool.execute(
            """
            CREATE FUNCTION public.fixture_rule_memory_fault() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION USING ERRCODE = 'P4503',
                    MESSAGE = 'synthetic memory insert refusal';
            END $$;
            CREATE TRIGGER fixture_rule_memory_fault BEFORE INSERT ON home_mem.facts
                FOR EACH ROW WHEN (NEW.predicate = 'decision:standing_rule')
                EXECUTE FUNCTION public.fixture_rule_memory_fault()
            """
        )


async def _committed_snapshot(observer: asyncpg.Pool, action_id: UUID) -> dict:
    async with observer.acquire() as connection:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            return {
                "action": await connection.fetchrow(
                    "SELECT status, decided_by FROM pending_actions WHERE id = $1", action_id
                ),
                "rules": await connection.fetch(
                    "SELECT id, tool_name, arg_constraints, active FROM approval_rules "
                    "WHERE created_from = $1",
                    action_id,
                ),
                "events": await connection.fetch(
                    "SELECT event_type FROM approval_events WHERE action_id = $1 "
                    "ORDER BY event_type",
                    action_id,
                ),
                "audit": await connection.fetch(
                    "SELECT actor, result FROM public.audit_log "
                    "WHERE action = 'approval.approve' AND target = $1",
                    str(action_id),
                ),
                "facts": await connection.fetch(
                    "SELECT metadata, scope, validity FROM home_mem.facts "
                    "WHERE predicate = 'decision:standing_rule'"
                ),
            }


@pytest.mark.parametrize(
    "boundary", ["audit_failure", "commit_failure", "committed", "memory_failure"]
)
async def test_rule_memory_requires_real_owning_commit(database, monkeypatch, boundary) -> None:
    owner, observer = database
    action_id = uuid4()
    await owner.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at) "
        "VALUES ($1, 'telegram_send_message', $2, 'pending', now())",
        action_id,
        _TOOL_ARGS,
    )
    await _inject_fault(owner, boundary)
    engine, daemon = _EmbeddingEngine(), _UnavailableDaemon()
    # Keep the real dashboard writer factory and store_fact. This only replaces
    # deployment configuration/model loading, never the transaction or sink.
    monkeypatch.setattr(
        routes,
        "_decision_memory_settings_for",
        lambda _name: routes._DecisionMemorySettings("home_mem", engine.model_name, {}),
    )
    monkeypatch.setattr(memory_tools, "get_embedding_engine", lambda _model: engine)
    app = FastAPI()
    app.state.owner_auth_service = _DomainOwnerState(_KEY)
    app.add_middleware(OwnerAuthMiddleware, config=OwnerAuthConfig.from_env(_KEY))
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: _DbManager(owner)
    app.dependency_overrides[routes.get_mcp_manager] = lambda: daemon
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": _KEY},
    ) as client:
        view = await client.get(f"/api/approvals/{action_id}")
        assert view.status_code == 200
        displayed_call = view.json()["data"]["call_preview"]["displayed_call"]
        payload = {"create_rule": True, "displayed_call": displayed_call, "actor": "untrusted"}
        path = f"/api/approvals/actions/{action_id}/approve"
        if boundary in {"audit_failure", "commit_failure"}:
            with pytest.raises(asyncpg.PostgresError) as refused:
                await client.post(path, json=payload)
            assert refused.value.sqlstate == ("P4501" if boundary == "audit_failure" else "P4502")
        else:
            response = await client.post(path, json=payload)
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "approved"
            assert response.json()["data"]["dispatched"] is False
            # A second request must not emit another fact or create another rule.
            repeated = await client.post(path, json=payload)
            assert repeated.status_code == 409

    durable = await _committed_snapshot(observer, action_id)
    if boundary in {"audit_failure", "commit_failure"}:
        assert dict(durable["action"]) == {"status": "pending", "decided_by": None}
        assert durable["rules"] == durable["events"] == durable["audit"] == durable["facts"] == []
        assert engine.calls == daemon.calls == 0
    else:
        assert dict(durable["action"]) == {
            "status": "approved",
            "decided_by": "human:dashboard:rest-api",
        }
        assert len(durable["rules"]) == 1
        rule = durable["rules"][0]
        assert rule["active"] is True and rule["tool_name"] == "telegram_send_message"
        assert [row["event_type"] for row in durable["events"]] == [
            "action_approved",
            "rule_created",
        ]
        assert [dict(row) for row in durable["audit"]] == [
            {"actor": "dashboard:rest-api", "result": "success"}
        ]
        assert engine.calls == daemon.calls == 1
        if boundary == "memory_failure":
            assert durable["facts"] == []
        else:
            assert len(durable["facts"]) == 1
            fact = durable["facts"][0]
            assert fact["validity"] == "active"
            assert fact["scope"] == f"home:decision:rule:{rule['id']}"
            assert fact["metadata"] == {
                "rule_id": str(rule["id"]),
                "tool_name": rule["tool_name"],
                "arg_constraints": rule["arg_constraints"],
                "state": "active",
            }
