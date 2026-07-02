"""Integration test: audit.append() participates in the SAME transaction as the
state-change write at the write-path endpoints (bu-h4ifq).

Background
----------
``audit.append()`` (``src/butlers/api/routers/audit.py``) accepts either an
asyncpg pool or an already-acquired connection precisely so callers can run the
audit INSERT inside the same SQL transaction as the state change being audited
(the spec's §D17 atomicity requirement). Prior to this change, the write-path
endpoints in ``permissions.py`` and ``webhooks.py`` called ``audit.append()``
with the bare *pool*, so the state-change write and the audit write committed
independently — a crash/error between the two could leave one persisted
without the other.

This module exercises the real (migrated) Postgres write paths for
``PUT /api/permissions/{butler}/{perm}`` and ``POST /api/webhooks`` and proves
atomicity directly:

* When ``audit.append()`` fails partway through the request, the surrounding
  ``conn.transaction()`` rolls back — the state-change row (permissions /
  webhooks) must NOT be persisted either.
* On a normal, successful request, both rows commit together.

A mocked-pool unit test cannot prove this (mocks don't model real transaction
rollback), hence the real-Postgres integration fixture — see
``tests/api/test_audit_log_union_db.py`` for the established pattern this
module follows.
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
from butlers.api.routers import permissions as permissions_module
from butlers.api.routers import webhooks as webhooks_module
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE_URL = "http://test"
_TEST_KEY_HEX = bytes(range(32)).hex()


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain (public.permissions/webhooks/audit_log)."""
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
    await p.execute("TRUNCATE TABLE public.permissions CASCADE")
    await p.execute("TRUNCATE TABLE public.webhooks CASCADE")
    await p.execute("TRUNCATE TABLE public.audit_log CASCADE")
    yield p
    await p.close()


@pytest.fixture
def atomicity_app(pool: asyncpg.Pool) -> FastAPI:
    """FastAPI app whose permissions/webhooks routers are wired to the real pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool

    application = create_app()
    application.dependency_overrides[permissions_module._get_db_manager] = lambda: mock_db
    application.dependency_overrides[webhooks_module._get_db_manager] = lambda: mock_db
    return application


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL)


# ---------------------------------------------------------------------------
# PUT /api/permissions/{butler}/{perm}
# ---------------------------------------------------------------------------


async def test_permission_set_rolls_back_state_and_audit_together(
    pool: asyncpg.Pool, atomicity_app: FastAPI
) -> None:
    """When audit.append() fails mid-transaction, the permissions UPSERT is
    rolled back too — neither row is persisted."""
    with (
        patch(
            "butlers.api.routers.permissions.audit.append",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated audit failure"),
        ),
        patch("butlers.api.routers.permissions.dispatch_event"),
    ):
        async with await _client(atomicity_app) as client:
            resp = await client.put(
                "/api/permissions/chronicler/spawn",
                json={"granted": True, "reason": "atomicity rollback test"},
            )

    # The route does not catch this failure — it surfaces as HTTP 500.
    assert resp.status_code == 500

    perm_count = await pool.fetchval(
        "SELECT count(*) FROM public.permissions WHERE butler = $1 AND permission = $2",
        "chronicler",
        "spawn",
    )
    assert perm_count == 0, "permissions row must roll back when the audit write fails"

    audit_count = await pool.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE action = 'permission.set'"
    )
    assert audit_count == 0, "audit row must not survive the rolled-back transaction"


async def test_permission_set_commits_state_and_audit_together(
    pool: asyncpg.Pool, atomicity_app: FastAPI
) -> None:
    """On success, the permissions UPSERT and the audit row both commit."""
    with patch("butlers.api.routers.permissions.dispatch_event"):
        async with await _client(atomicity_app) as client:
            resp = await client.put(
                "/api/permissions/chronicler/spawn",
                json={"granted": True, "reason": "atomicity commit test"},
            )

    assert resp.status_code == 200

    perm_row = await pool.fetchrow(
        "SELECT granted, reason FROM public.permissions WHERE butler = $1 AND permission = $2",
        "chronicler",
        "spawn",
    )
    assert perm_row is not None, "permissions row must be persisted on success"
    assert perm_row["granted"] is True
    assert perm_row["reason"] == "atomicity commit test"

    audit_row = await pool.fetchrow(
        "SELECT actor, target, note FROM public.audit_log WHERE action = 'permission.set'"
    )
    assert audit_row is not None, "audit row must be persisted alongside the state change"
    assert audit_row["target"] == "chronicler.spawn"
    assert audit_row["note"] == "atomicity commit test"


# ---------------------------------------------------------------------------
# POST /api/webhooks
# ---------------------------------------------------------------------------


async def test_webhook_create_rolls_back_state_and_audit_together(
    pool: asyncpg.Pool, atomicity_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When audit.append() fails mid-transaction, the webhooks INSERT is
    rolled back too — neither row is persisted."""
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", _TEST_KEY_HEX)

    with (
        patch(
            "butlers.api.routers.webhooks.audit.append",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated audit failure"),
        ),
        patch("butlers.api.routers.webhooks.dispatch_event"),
    ):
        async with await _client(atomicity_app) as client:
            resp = await client.post(
                "/api/webhooks",
                json={"endpoint": "https://example.com/rollback-hook", "events": ["data.export"]},
            )

    assert resp.status_code == 500

    webhook_count = await pool.fetchval(
        "SELECT count(*) FROM public.webhooks WHERE endpoint = $1",
        "https://example.com/rollback-hook",
    )
    assert webhook_count == 0, "webhooks row must roll back when the audit write fails"

    audit_count = await pool.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE action = 'webhook.create'"
    )
    assert audit_count == 0, "audit row must not survive the rolled-back transaction"


async def test_webhook_create_commits_state_and_audit_together(
    pool: asyncpg.Pool, atomicity_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On success, the webhooks INSERT and the audit row both commit."""
    monkeypatch.setenv("WEBHOOK_SECRET_KEY", _TEST_KEY_HEX)

    with patch("butlers.api.routers.webhooks.dispatch_event"):
        async with await _client(atomicity_app) as client:
            resp = await client.post(
                "/api/webhooks",
                json={"endpoint": "https://example.com/commit-hook", "events": ["data.export"]},
            )

    assert resp.status_code == 201
    webhook_id = resp.json()["data"]["id"]

    webhook_row = await pool.fetchrow(
        "SELECT endpoint FROM public.webhooks WHERE id = $1::uuid", webhook_id
    )
    assert webhook_row is not None, "webhooks row must be persisted on success"
    assert webhook_row["endpoint"] == "https://example.com/commit-hook"

    audit_row = await pool.fetchrow(
        "SELECT target FROM public.audit_log WHERE action = 'webhook.create'"
    )
    assert audit_row is not None, "audit row must be persisted alongside the state change"
    assert audit_row["target"] == webhook_id
