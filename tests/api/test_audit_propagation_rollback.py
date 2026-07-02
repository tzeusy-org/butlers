"""Integration test: swallowed ``AuditTableNotAvailableError`` sites now
propagate as HTTP 503 ``{"error": "audit_unavailable"}`` and roll back the
state change they accompanied (bu-6exf0).

Background
----------
The dashboard-audit-log spec (``openspec/specs/dashboard-audit-log``) requires
that when ``audit.append()`` cannot find ``public.audit_log`` it raises
``AuditTableNotAvailableError`` and mutation endpoints let it propagate — never
catch-log-and-continue. Prior to this change, ``memory.py``,
``butler_management.py``, ``approvals.py``, and ``oauth.py`` all swallowed it
(logged a warning and returned 200 as if nothing happened), silently hiding a
missing-migration condition and never surfacing the required 503 envelope.

This module proves, against a real (migrated) Postgres instance, that:

* ``PUT /api/memory/retention-policies`` returns HTTP 503
  ``{"error": "audit_unavailable"}`` when the audit append fails.
* The retention-policy row that was being upserted in the SAME request is
  rolled back — it does not persist despite the earlier ``INSERT`` in the
  request handler having "succeeded" before the audit append raised.
* On a normal, successful request, both the policy row and the audit row
  commit together.

A mocked-pool unit test cannot prove the rollback half of this (mocks don't
model real transaction rollback) — see ``tests/api/test_audit_txn_atomicity.py``
for the established real-Postgres pattern this module follows.
"""

from __future__ import annotations

import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers import memory as memory_module
from butlers.api.routers.audit import AuditTableNotAvailableError
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE_URL = "http://test"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain (public.memory_retention_policies/audit_log)."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.memory_retention_policies CASCADE")
    await p.execute("TRUNCATE TABLE public.audit_log CASCADE")
    yield p
    await p.close()


@pytest.fixture
def rollback_app(pool: asyncpg.Pool) -> FastAPI:
    """FastAPI app whose memory router is wired to the real pool.

    Uses the real ``create_app()`` (not a bare router mount) so the
    ``AuditTableNotAvailableError`` -> 503 app-level exception handler
    (registered in ``butlers.api.middleware``) is active.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["chronicler"]
    mock_db.pool.return_value = pool

    application = create_app()
    application.dependency_overrides[memory_module._get_db_manager] = lambda: mock_db
    return application


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


async def test_retention_policy_update_rolls_back_on_audit_unavailable(
    pool: asyncpg.Pool, rollback_app: FastAPI
) -> None:
    """When audit.append() raises AuditTableNotAvailableError, the request
    returns 503 {"error": "audit_unavailable"} and the retention-policy row
    that was being upserted in the same request does NOT persist."""
    with patch(
        "butlers.api.routers.memory._audit.append",
        new_callable=AsyncMock,
        side_effect=AuditTableNotAvailableError("public.audit_log is not available"),
    ):
        async with await _client(rollback_app) as client:
            resp = await client.put(
                "/api/memory/retention-policies",
                json={"policies": [{"kind": "event", "ttl_days": 30, "max_rows": 1000}]},
            )

    assert resp.status_code == 503
    assert resp.json() == {"error": "audit_unavailable"}

    row = await pool.fetchrow(
        "SELECT * FROM public.memory_retention_policies WHERE kind = $1", "event"
    )
    assert row is None, "retention-policy row must roll back when the audit write fails"

    audit_count = await pool.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE action = 'memory.retention_policy'"
    )
    assert audit_count == 0, "audit row must not survive the rolled-back transaction"


async def test_retention_policy_update_commits_state_and_audit_together(
    pool: asyncpg.Pool, rollback_app: FastAPI
) -> None:
    """On success, the retention-policy row and the audit row both commit."""
    async with await _client(rollback_app) as client:
        resp = await client.put(
            "/api/memory/retention-policies",
            json={"policies": [{"kind": "fact", "ttl_days": 60, "max_rows": 2000}]},
        )

    assert resp.status_code == 200

    row = await pool.fetchrow(
        "SELECT ttl_days, max_rows FROM public.memory_retention_policies WHERE kind = $1", "fact"
    )
    assert row is not None, "retention-policy row must be persisted on success"
    assert row["ttl_days"] == 60
    assert row["max_rows"] == 2000

    audit_row = await pool.fetchrow(
        "SELECT target, note FROM public.audit_log WHERE action = 'memory.retention_policy'"
    )
    assert audit_row is not None, "audit row must be persisted alongside the state change"
    assert audit_row["target"] == "kind:fact"
