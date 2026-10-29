"""REQ-module-approvals-002: refused owner decisions cannot commit call edits.

Real migrated SQL, HTTP handler and approval/expiry operation; the unavailable
owning daemon and synthetic owner session are transport collaborators. Separate
backends observe committed state. No actual tool execution is claimed.
"""

from __future__ import annotations

import asyncio
import shutil
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.routers import approvals as routes
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import _DomainOwnerState

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_ARGS = {"chat_id": "test-recipient", "text": "Original stored message"}


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "approvals"]
    )


async def _close_pool(pool):
    await asyncio.wait_for(pool.close(), timeout=10)


@pytest.fixture
async def databases(migrated_db_url):
    async with AsyncExitStack() as cleanup:
        pools = []
        for _ in range(2):
            pool = await asyncpg.create_pool(
                migrated_db_url, min_size=1, max_size=2, init=register_jsonb_codec
            )
            cleanup.push_async_callback(_close_pool, pool)
            pools.append(pool)
        owner, observer = pools
        async with owner.acquire() as first, observer.acquire() as second:
            assert await first.fetchval("SELECT pg_backend_pid()") != await second.fetchval(
                "SELECT pg_backend_pid()"
            )
        yield owner, observer


class _DbManager:
    butler_names = ["home"]

    def __init__(self, pool):
        self._pool = pool

    def pool(self, name):
        assert name == "home"
        return self._pool

    def butlers_with_module(self, module):
        return self.butler_names if module == "approvals" else []


class _UnavailableDaemon:
    def __init__(self):
        self.calls = 0

    async def get_client(self, name):
        assert name == "home"
        self.calls += 1
        raise ConnectionError("synthetic owning daemon unavailable")


@pytest.mark.parametrize("boundary", ["already_approved", "expired", "successful_edit"])
async def test_owner_edit_requires_successful_decision(databases, monkeypatch, boundary):
    owner, observer = databases
    action_id = uuid4()
    now = datetime.now(UTC)
    initial_status = "approved" if boundary == "already_approved" else "pending"
    expires = now - timedelta(minutes=1) if boundary == "expired" else now + timedelta(hours=1)
    await owner.execute(
        "INSERT INTO pending_actions (id, tool_name, tool_args, status, requested_at, expires_at) "
        "VALUES ($1, 'telegram_send_message', $2, $3, $4, $5)",
        action_id,
        _ARGS,
        initial_status,
        now,
        expires,
    )
    # No memory collaborator can hide the route's actual owning SQL transaction.
    monkeypatch.setattr(routes, "_decision_memory_writer_for", lambda *_: None)
    daemon = _UnavailableDaemon()
    key = "synthetic-refused-edit-owner-key"
    app = FastAPI()
    app.state.owner_auth_service = _DomainOwnerState(key)
    app.add_middleware(
        OwnerAuthMiddleware, config=OwnerAuthConfig(origin=None, rp_id=None, api_key=key)
    )
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: _DbManager(owner)
    app.dependency_overrides[routes.get_mcp_manager] = lambda: daemon
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": key},
    ) as client:
        view = await client.get(f"/api/approvals/{action_id}")
        assert view.status_code == 200
        payload = {
            "displayed_call": view.json()["data"]["call_preview"]["displayed_call"],
            "edits": {"text": "Explicit owner edit"},
        }
        response = await client.post(f"/api/approvals/{action_id}/approve", json=payload)
    assert (
        response.status_code
        == {
            "already_approved": 409,
            "expired": 400,
            "successful_edit": 200,
        }[boundary]
    )
    # This acquisition occurs after HTTP completion and owning transaction exit.
    async with observer.acquire() as connection:
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            row = await connection.fetchrow(
                "SELECT tool_args, status, decided_by FROM pending_actions WHERE id = $1",
                action_id,
            )
            events = await connection.fetch(
                "SELECT event_type FROM approval_events WHERE action_id = $1", action_id
            )
            audits = await connection.fetch(
                "SELECT actor, result FROM public.audit_log "
                "WHERE action = 'approval.approve' AND target = $1",
                str(action_id),
            )
    if boundary == "successful_edit":
        assert row["tool_args"] == {**_ARGS, "text": "Explicit owner edit"}
        assert row["status"] == "approved"
        assert row["decided_by"] == "human:dashboard:rest-api"
        assert [event["event_type"] for event in events] == ["action_approved"]
        assert len(audits) == 1 and audits[0]["result"] == "success"
        assert daemon.calls == 1
        assert response.json()["data"]["dispatched"] is False
    else:
        assert row["tool_args"] == _ARGS
        assert row["status"] == ("expired" if boundary == "expired" else "approved")
        assert [event["event_type"] for event in events] == (
            ["action_expired"] if boundary == "expired" else []
        )
        assert audits == []
        assert daemon.calls == 0
        if boundary == "expired":
            assert row["decided_by"] == "system:expiry"
