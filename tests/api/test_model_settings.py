"""Tests for model catalog and butler model override endpoints.

Condensed from test_model_settings.py (53) + test_model_settings_discretion_tier.py (8)
+ test_model_settings_self_healing_tier.py (9) → ~12 tests (bu-egmz6) → 3 tests (bu-2yw2d).
Keeps: list/503 fallback, create 201 + conflict 409 + invalid-tier 422 (parametrized),
       resolve-model 200.

Phase 2 additions (bu-q2nz3): priority stepper, verify-all rate-limit + concurrency,
server sort order, failures tail.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app as create_production_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from tests.api.auth_helpers import _DomainOwnerState, create_authenticated_domain_app
from tests.api.http_fixtures import ApiDatabase


@pytest.fixture(scope="module")
def app():
    return create_authenticated_domain_app(api_key="owner-key")


pytestmark = pytest.mark.unit


def _make_catalog_row(
    *,
    entry_id=None,
    alias="claude-sonnet",
    runtime_type="claude",
    model_id="claude-sonnet-4-6",
    complexity_tier="workhorse",
    enabled=True,
    priority=0,
    session_timeout_s=1800,
    extra_args=None,
    allowance_account=None,
):
    return {
        "id": entry_id or uuid.uuid4(),
        "alias": alias,
        "runtime_type": runtime_type,
        "model_id": model_id,
        "extra_args": json.dumps(extra_args or []),
        "allowance_account": allowance_account,
        "complexity_tier": complexity_tier,
        # effective_tier mirrors complexity_tier in mock rows (SQL alias for
        # COALESCE(bmo.complexity_tier, mc.complexity_tier) in _RESOLVE_SQL).
        "effective_tier": complexity_tier,
        "enabled": enabled,
        "priority": priority,
        "session_timeout_s": session_timeout_s,
        # Verification columns added by core_093 migration
        "last_verified_at": None,
        "last_verified_latency_ms": None,
        "last_verified_ok": None,
    }


def _mock_record(row: dict[str, Any]) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    for k, v in row.items():
        setattr(m, k, v)
    return m


def _app_with_pool(
    app,
    *,
    fetch_rows=None,
    fetchrow_result=None,
    fetchval_result=None,
    execute_result="DELETE 1",
    pool_raises=None,
    fetchrow_side_effect=None,
):
    mock_pool = AsyncMock(spec=asyncpg.Pool, is_closing=MagicMock(return_value=False))
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])
    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(
            return_value=_mock_record(fetchrow_result) if fetchrow_result else None
        )
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)
    mock_conn = AsyncMock()
    mock_conn.transaction = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=None)
        )
    )
    mock_conn.is_in_transaction = MagicMock(return_value=False)

    async def enter_transaction():
        mock_conn.is_in_transaction.return_value = True

    async def leave_transaction(*exc):
        mock_conn.is_in_transaction.return_value = False
        return False

    mock_conn.transaction.return_value.__aenter__.side_effect = enter_transaction
    mock_conn.transaction.return_value.__aexit__.side_effect = leave_transaction
    mock_conn.fetchrow = mock_pool.fetchrow
    mock_conn.fetchval = AsyncMock(
        side_effect=lambda sql, *args: (
            1 if sql.startswith("INSERT INTO public.audit_log ") else None
        )
    )
    mock_pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn), __aexit__=AsyncMock(return_value=None)
        )
    )
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_raises:
        mock_db.credential_shared_pool.side_effect = pool_raises
    else:
        mock_db.credential_shared_pool.return_value = mock_pool
    app.dependency_overrides[get_db] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# Catalog list + 503 fallback
# ---------------------------------------------------------------------------


async def test_catalog_list_and_503(app):
    rows = [
        _make_catalog_row(alias="claude-haiku", complexity_tier="cheap"),
        _make_catalog_row(
            alias="claude-sonnet", complexity_tier="workhorse", allowance_account="plan-b"
        ),
    ]
    # Happy path. Breaker state (bu-hmdqz.2) and routing score (bu-ep4ks.13)
    # are each fetched via a second, differently-shaped query
    # (get_breaker_states / get_routing_scores) — patched directly here rather
    # than threaded through the generic catalog-row mock rows, which model
    # the model_catalog list shape, not model_dispatch_attempts.
    _app_with_pool(app, fetch_rows=rows)
    breaker_batch = AsyncMock(return_value={})
    with (
        patch(
            "butlers.api.routers.model_settings.get_breaker_states",
            new=breaker_batch,
        ),
        patch(
            "butlers.api.routers.model_settings.get_routing_scores",
            new=AsyncMock(return_value={}),
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/settings/models")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 2
    assert [r["allowance_account"] for r in resp.json()["data"]] == [None, "plan-b"]
    assert resp.json()["data"][0]["breaker_open"] is False
    assert resp.json()["data"][0]["routing_score_insufficient_data"] is True
    breaker_batch.assert_awaited_once()
    assert breaker_batch.await_args.args[1] == [row["id"] for row in rows]

    # 503 when pool unavailable
    _app_with_pool(app, pool_raises=KeyError("No shared pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp_503 = await client.get("/api/settings/models")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Catalog CRUD error paths (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,fetchrow_side_effect,execute_result,expected",
    [
        # Create 201
        (
            {
                "alias": "new-model",
                "runtime_type": "codex",
                "model_id": "gpt-5",
                "complexity_tier": "workhorse",
                "enabled": True,
                "priority": 0,
            },
            None,
            "INSERT 1",
            201,
        ),
        # Create 409 duplicate alias
        (
            {
                "alias": "claude-sonnet",
                "runtime_type": "claude",
                "model_id": "claude-sonnet-4-6",
                "complexity_tier": "workhorse",
            },
            asyncpg.UniqueViolationError("uq_model_catalog_alias"),
            "INSERT 1",
            409,
        ),
        # Create 422 invalid complexity tier
        (
            {
                "alias": "x",
                "runtime_type": "claude",
                "model_id": "y",
                "complexity_tier": "invalid_tier",
            },
            None,
            "INSERT 1",
            422,
        ),
    ],
    ids=["create-201", "create-409-duplicate", "create-422-bad-tier"],
)
async def test_catalog_create_error_paths(
    app, payload, fetchrow_side_effect, execute_result, expected
):
    created_row = _make_catalog_row(alias=payload.get("alias", "x"))
    _app_with_pool(
        app,
        fetchrow_side_effect=fetchrow_side_effect,
        fetchrow_result=created_row if fetchrow_side_effect is None else None,
        execute_result=execute_result,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/settings/models", json=payload)
    assert resp.status_code == expected
    if expected == 201:
        assert resp.json()["data"]["allowance_account"] is None
        invalid = [True, False, 12, 1.5, [], {}, "", " ", "a\n", "a\nb", "Upper", "a.b", "a" * 65]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for account in invalid:
                _, pool = _app_with_pool(app, fetchrow_result=created_row)
                bad = await client.post(
                    "/api/settings/models", json={**payload, "allowance_account": account}
                )
                assert bad.status_code == 422, repr(account)
                pool.fetchrow.assert_not_awaited()


async def test_catalog_create_writes_audit_on_success(app, audit_append_spy, monkeypatch):
    """A successful catalog insert records the server-derived model identity."""
    monkeypatch.setattr(
        "butlers.api.routers.model_settings.authenticated_principal",
        lambda: "server-owner",
    )
    entry_id = uuid.uuid4()
    created_row = _make_catalog_row(
        entry_id=entry_id, alias="new-model", allowance_account="plan-a"
    )
    _, mock_pool = _app_with_pool(app, fetchrow_result=created_row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/settings/models",
            json={
                "alias": "new-model",
                "runtime_type": "codex",
                "model_id": "gpt-5",
                "complexity_tier": "workhorse",
                "allowance_account": "plan-a",
            },
        )

    assert resp.status_code == 201
    assert resp.json()["data"]["allowance_account"] == "plan-a"
    assert mock_pool.fetchrow.await_args.args[-1] == "plan-a"
    route_calls = [
        call
        for call in audit_append_spy.call_args_list
        if len(call.args) >= 3 and call.args[2] == "model.create"
    ]
    assert len(route_calls) == 1
    acquired_connection = mock_pool.acquire.return_value.__aenter__.return_value
    assert route_calls[0].args[:2] == (acquired_connection, "server-owner")
    assert route_calls[0].kwargs == {
        "target": str(entry_id),
        "note": "new-model",
        "result": "success",
    }


async def test_catalog_create_failure_does_not_claim_audit_success(app, audit_append_spy):
    """A failed insert has no successful model.create audit row."""

    async def fail_insert(*_args):
        raise RuntimeError("catalog unavailable")

    _app_with_pool(app, fetchrow_side_effect=fail_insert)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/settings/models",
            json={
                "alias": "failed-model",
                "runtime_type": "codex",
                "model_id": "gpt-5",
                "complexity_tier": "workhorse",
            },
        )

    assert resp.status_code == 500
    assert not [
        call
        for call in audit_append_spy.call_args_list
        if len(call.args) >= 3 and call.args[2] == "model.create"
    ]


async def test_catalog_delete_writes_audit_after_cascade_delete(app, audit_append_spy, monkeypatch):
    """Deleting a catalog row records the cascade-bearing model mutation."""
    monkeypatch.setattr(
        "butlers.api.routers.model_settings.authenticated_principal",
        lambda: "server-owner",
    )
    entry_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(
        app,
        fetchrow_result={"cascade_deleted_overrides": 2},
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/api/settings/models/{entry_id}")

    assert resp.status_code == 200
    delete_sql = mock_pool.fetchrow.await_args.args[0]
    assert "DELETE FROM public.model_catalog" in delete_sql
    assert "public.butler_model_overrides" in delete_sql
    assert mock_pool.fetchrow.await_args.args[1:] == (entry_id,)
    route_calls = [
        call
        for call in audit_append_spy.call_args_list
        if len(call.args) >= 3 and call.args[2] == "model.delete"
    ]
    assert len(route_calls) == 1
    acquired_connection = mock_pool.acquire.return_value.__aenter__.return_value
    assert route_calls[0].args[:2] == (acquired_connection, "server-owner")
    assert route_calls[0].kwargs == {
        "target": str(entry_id),
        "metadata": {"cascade_deleted_overrides": 2},
        "result": "success",
    }


@pytest.fixture
async def catalog_delete_impact_entries(api_db: ApiDatabase) -> tuple[uuid.UUID, uuid.UUID]:
    target_id, unrelated_id, empty_id = (uuid.uuid4() for _ in range(3))
    await api_db.pool.executemany(
        """
        INSERT INTO public.model_catalog (id, alias, runtime_type, model_id, complexity_tier)
        VALUES ($1, $2, 'codex', 'synthetic-impact-model', 'workhorse')
        """,
        [
            (target_id, "impact-target"),
            (unrelated_id, "impact-unrelated"),
            (empty_id, "impact-empty"),
        ],
    )
    await api_db.pool.executemany(
        """
        INSERT INTO public.butler_model_overrides
            (butler_name, catalog_entry_id, enabled, complexity_tier)
        VALUES ($1, $2, $3, 'workhorse')
        """,
        [
            ("general", target_id, True),
            ("finance", target_id, False),
            ("general", unrelated_id, True),
        ],
    )
    return target_id, empty_id


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_catalog_delete_impact_returns_current_server_override_count(
    api: httpx.AsyncClient, catalog_delete_impact_entries: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Count every referencing override and keep a valid empty entry distinct from absence."""
    target_id, empty_id = catalog_delete_impact_entries

    response = await api.get(f"/api/settings/models/{target_id}/delete-impact")

    assert response.status_code == 200
    assert response.json()["data"] == {"id": str(target_id), "override_count": 2}

    empty_response = await api.get(f"/api/settings/models/{empty_id}/delete-impact")

    assert empty_response.status_code == 200
    assert empty_response.json()["data"] == {"id": str(empty_id), "override_count": 0}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.usefixtures("catalog_delete_impact_entries")
async def test_catalog_delete_impact_returns_404_for_missing_model(api: httpx.AsyncClient) -> None:
    """Other stored catalog entries cannot supply a count for an unknown ID."""
    entry_id = uuid.uuid4()

    response = await api.get(f"/api/settings/models/{entry_id}/delete-impact")

    assert response.status_code == 404
    assert response.json()["detail"] == f"Catalog entry not found: {entry_id}"


async def test_catalog_delete_failure_does_not_claim_audit_success(app, audit_append_spy):
    """A missing catalog row does not emit a successful model.delete audit row."""
    _app_with_pool(app, fetchrow_result=None)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/api/settings/models/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert not [
        call
        for call in audit_append_spy.call_args_list
        if len(call.args) >= 3 and call.args[2] == "model.delete"
    ]


# ---------------------------------------------------------------------------
# Resolve-model preview
# ---------------------------------------------------------------------------


async def test_resolve_model_preview_200_and_422_for_invalid(app):
    catalog_row = _make_catalog_row(complexity_tier="workhorse")
    app2, mock_pool = _app_with_pool(app)
    mock_pool.fetchrow = AsyncMock(side_effect=[_mock_record(catalog_row), None])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        r200 = await client.get("/api/butlers/general/resolve-model?complexity=workhorse")
        r422 = await client.get("/api/butlers/general/resolve-model?complexity=invalid")
    assert r200.status_code == 200
    assert r422.status_code == 422


# ---------------------------------------------------------------------------
# §3.4  Priority stepper
# ---------------------------------------------------------------------------


async def test_priority_stepper_200_and_clamp_at_zero(app, audit_append_spy):
    """PUT /api/settings/models/{id}/priority adjusts priority and calls audit.append."""
    entry_id = uuid.uuid4()
    updated_row = _make_catalog_row(entry_id=entry_id, priority=5, allowance_account="plan-a")
    _, mock_pool = _app_with_pool(app, fetchrow_result=updated_row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{entry_id}/priority",
            json={"delta": 5},
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["priority"] == 5
    assert resp.json()["data"]["allowance_account"] == "plan-a"
    # The route emits an explicit audit entry with action "model.priority"; the
    # dashboard_audit_middleware ALSO routes through the same canonical
    # audit.append() spy as a fire-and-forget task, so the total count races
    # between 1 and 2.  Assert on the route's specific call rather than the count.
    route_calls = [
        c
        for c in audit_append_spy.call_args_list
        if len(c.args) >= 3 and c.args[2] == "model.priority"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'model.priority', "
        f"got call list: {audit_append_spy.call_args_list}"
    )
    assert route_calls[0].kwargs["note"] == "5"
    assert route_calls[0].kwargs["result"] == "success"


async def test_priority_stepper_404_on_missing(app, audit_append_spy):
    """PUT /api/settings/models/{id}/priority returns 404 when catalog entry missing."""
    _, mock_pool = _app_with_pool(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{uuid.uuid4()}/priority",
            json={"delta": 1},
        )
    assert resp.status_code == 404
    audit_append_spy.assert_not_awaited()


# ---------------------------------------------------------------------------
# §3.5  Verify-all rate limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "configured,header,expected", [(False, None, 503), (True, None, 401), (True, "wrong", 401)]
)
async def test_verify_all_owner_gate_precedes_run(
    app, monkeypatch: pytest.MonkeyPatch, configured: bool, header: str | None, expected: int
) -> None:
    """REQ-dashboard-model-settings-001: no verification run starts before owner auth."""
    import butlers.api.routers.model_settings as _ms

    monkeypatch.setattr(_ms, "_verify_all_last_run", 0.0)
    if configured:
        monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    else:
        monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    _, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(return_value=[])
    monkeypatch.setenv("DASHBOARD_AUTH_ORIGIN", "https://butlers.example.test")
    monkeypatch.setenv("DASHBOARD_AUTH_RP_ID", "butlers.example.test")
    guarded = create_production_app(api_key="owner-key" if configured else "")
    guarded.dependency_overrides.update(app.dependency_overrides)
    if configured:
        guarded.state.owner_auth_service = _DomainOwnerState("owner-key")
    headers = {"Origin": "https://butlers.example.test"}
    if header is not None:
        headers["X-API-Key"] = header

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=guarded),
        base_url="https://butlers.example.test",
        headers=headers,
    ) as client:
        response = await client.post("/api/settings/models/verify-all")

    assert response.status_code == expected
    mock_pool.fetch.assert_not_awaited()


async def test_verify_all_rate_limit(app, audit_append_spy, monkeypatch):
    """POST /api/settings/models/verify-all returns 429 on second call within 60s."""
    import butlers.api.routers.model_settings as _ms

    # Reset the sentinel to allow the first call
    monkeypatch.setattr(_ms, "_verify_all_last_run", 0.0)
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")

    _, mock_pool = _app_with_pool(app)
    # Return an empty enabled-models list so no actual verification is attempted
    mock_pool.fetch = AsyncMock(return_value=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "owner-key"},
    ) as client:
        r1 = await client.post("/api/settings/models/verify-all")
        r429 = await client.post("/api/settings/models/verify-all")

    assert r1.status_code == 200
    assert r429.status_code == 429
    # The accepted run emits an explicit audit entry with action
    # "models.verify_all".  The dashboard_audit_middleware ALSO routes through
    # the same canonical audit.append() spy as a fire-and-forget task (once per
    # POST, including the 429), so the total count races.  Assert on the route's
    # specific calls rather than the total count.
    route_calls = [
        c
        for c in audit_append_spy.call_args_list
        if len(c.args) >= 3 and c.args[2] == "models.verify_all"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'models.verify_all', "
        f"got call list: {audit_append_spy.call_args_list}"
    )
    assert route_calls[0].kwargs["result"] == "success"


async def test_verify_all_pool_failure_does_not_consume_rate_limit(
    app, audit_append_spy, monkeypatch
):
    """A rejected run remains eligible for retry once the pool is available."""
    import butlers.api.routers.model_settings as _ms

    monkeypatch.setattr(_ms, "_verify_all_last_run", 0.0)
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")

    app, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_db = app.dependency_overrides[get_db]()
    mock_db.credential_shared_pool.side_effect = [KeyError("No shared pool"), mock_pool]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "owner-key"},
    ) as client:
        rejected = await client.post("/api/settings/models/verify-all")
        accepted = await client.post("/api/settings/models/verify-all")

    assert rejected.status_code == 503
    assert accepted.status_code == 200
    assert accepted.json()["data"] == {
        "accepted": True,
        "total": 0,
        "ok": 0,
        "failed": 0,
        "unavailable": 0,
    }


async def test_verify_all_rejects_concurrent_arrival_while_run_is_in_flight(app, monkeypatch):
    """A second request is rejected while the accepted run is still running."""
    import butlers.api.routers.model_settings as _ms

    monkeypatch.setattr(_ms, "_verify_all_last_run", 0.0)
    monkeypatch.setattr(_ms, "_verify_all_in_flight", False)
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    _, mock_pool = _app_with_pool(app)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_verify_all(pool, *, audit_actor):
        assert pool is mock_pool
        assert audit_actor == "owner"
        started.set()
        await release.wait()
        return _ms.VerifyAllResult(
            accepted=True,
            total=0,
            ok=0,
            failed=0,
            unavailable=0,
        )

    monkeypatch.setattr(_ms, "run_verify_all_models", blocked_verify_all)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "owner-key"},
    ) as client:
        # FastAPI builds every route's dependency/validation state on the first request through
        # an app. That one-off cost (seconds on a loaded runner) must not count against the wait
        # below, so pay it here with a request that matches no handler.
        await client.get("/api/settings/models/verify-all")
        first_task = asyncio.create_task(client.post("/api/settings/models/verify-all"))
        try:
            # A hang guard, not a latency assertion: the handler blocks on `release`.
            await asyncio.wait_for(started.wait(), timeout=30)
            concurrent = await client.post("/api/settings/models/verify-all")
        finally:
            release.set()
        first = await first_task

    assert concurrent.status_code == 429
    assert first.status_code == 200


async def test_verify_all_accepted_after_interval_returns_current_result_shape(
    app, audit_append_spy, monkeypatch
):
    """The accepted API response exposes only meaningful verification buckets."""
    import butlers.api.routers.model_settings as _ms

    # Simulate last run well in the past
    monkeypatch.setattr(_ms, "_verify_all_last_run", time.monotonic() - 120.0)
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")

    _, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(return_value=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": "owner-key"},
    ) as client:
        resp = await client.post("/api/settings/models/verify-all")

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "accepted": True,
        "total": 0,
        "ok": 0,
        "failed": 0,
        "unavailable": 0,
    }


# ---------------------------------------------------------------------------
# §3.6  Failures tail — filters by since, graceful on missing table
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# §4.1  PUT /api/settings/models/{id} — full edit endpoint
# ---------------------------------------------------------------------------


async def test_update_catalog_entry_200_writes_audit(app, audit_append_spy):
    """PUT /api/settings/models/{id} returns 200 and calls audit.append('model.update')."""
    entry_id = uuid.uuid4()
    updated_row = _make_catalog_row(
        entry_id=entry_id,
        alias="renamed",
        complexity_tier="cheap",
        priority=3,
        allowance_account="plan-b",
    )
    _, mock_pool = _app_with_pool(app, fetchrow_result=updated_row)

    payload = {
        "alias": "renamed",
        "model_id": "claude-haiku-4",
        "complexity_tier": "cheap",
        "priority": 3,
        "enabled": True,
        "extra_args": [],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(f"/api/settings/models/{entry_id}", json=payload)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["alias"] == "renamed"
    # The route emits an explicit audit entry with action "model.update"; the
    # dashboard_audit_middleware ALSO routes through the same canonical
    # audit.append() spy as a fire-and-forget task, so the total count races
    # between 1 and 2.  Assert on the route's specific call rather than the count.
    route_calls = [
        c
        for c in audit_append_spy.call_args_list
        if len(c.args) >= 3 and c.args[2] == "model.update"
    ]
    assert len(route_calls) == 1, (
        f"expected exactly one route audit.append with action 'model.update', "
        f"got call list: {audit_append_spy.call_args_list}"
    )
    assert route_calls[0].kwargs["target"] == str(entry_id)
    assert route_calls[0].kwargs["result"] == "success"

    # Omission preserves the stored label; a null-only edit explicitly clears it.
    assert data["allowance_account"] == "plan-b"
    assert "allowance_account =" not in mock_pool.fetchrow.await_args.args[0]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for account in ("plan-c", "0_plan-x", "a" * 64, None):
            _, pool = _app_with_pool(
                app, fetchrow_result={**updated_row, "allowance_account": account}
            )
            changed = await client.put(
                f"/api/settings/models/{entry_id}", json={"allowance_account": account}
            )
            assert changed.status_code == 200
            assert changed.json()["data"]["allowance_account"] == account
            sql, *params = pool.fetchrow.await_args.args
            assert "allowance_account = $1" in sql and params == [account, entry_id]
        for account in (
            True,
            False,
            12,
            1.5,
            [],
            {},
            "",
            " ",
            "a\n",
            "a\nb",
            "Upper",
            "a.b",
            "a" * 65,
        ):
            _, pool = _app_with_pool(app, fetchrow_result=updated_row)
            bad = await client.put(
                f"/api/settings/models/{entry_id}", json={"allowance_account": account}
            )
            assert bad.status_code == 422, repr(account)
            pool.fetchrow.assert_not_awaited()


async def test_update_catalog_entry_422_invalid_tier(app, audit_append_spy):
    """PUT /api/settings/models/{id} returns 422 when complexity_tier is not canonical."""
    entry_id = uuid.uuid4()
    _app_with_pool(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{entry_id}",
            json={"complexity_tier": "ultra"},
        )
    assert resp.status_code == 422
    audit_append_spy.assert_not_awaited()


async def test_update_catalog_entry_404_missing_entry(app, audit_append_spy):
    """PUT /api/settings/models/{id} returns 404 when catalog entry does not exist."""
    entry_id = uuid.uuid4()
    # fetchrow returns None → 404
    _app_with_pool(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{entry_id}",
            json={"alias": "ghost"},
        )
    assert resp.status_code == 404
    audit_append_spy.assert_not_awaited()


async def test_update_catalog_entry_422_no_fields(app, audit_append_spy):
    """PUT /api/settings/models/{id} returns 422 when no fields are provided."""
    entry_id = uuid.uuid4()
    _app_with_pool(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(f"/api/settings/models/{entry_id}", json={})
    assert resp.status_code == 422
    audit_append_spy.assert_not_awaited()


async def test_update_catalog_entry_writes_and_returns_capabilities(app, audit_append_spy):
    """PUT capabilities replaces the envelope; the entry echoes it back."""
    entry_id = uuid.uuid4()
    updated_row = {**_make_catalog_row(entry_id=entry_id), "capabilities": {"vision": True}}
    _, mock_pool = _app_with_pool(app, fetchrow_result=updated_row)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/api/settings/models/{entry_id}", json={"capabilities": {"vision": True}}
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["capabilities"] == {"vision": True}
    sql, *params = mock_pool.fetchrow.await_args.args
    assert "capabilities = $1::jsonb" in sql
    assert params[0] == {"vision": True}


@pytest.mark.parametrize("method", ["post", "put"])
async def test_catalog_capabilities_outside_vocabulary_422(app, audit_append_spy, method):
    """A typo'd feature key is rejected before any write, never stored as 'no opinion'."""
    _, mock_pool = _app_with_pool(app)
    body: dict[str, Any] = {"capabilities": {"visoin": True}}
    if method == "post":
        body.update(alias="m", runtime_type="codex", model_id="gpt-6-sol")
        url = "/api/settings/models"
    else:
        url = f"/api/settings/models/{uuid.uuid4()}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await getattr(client, method)(url, json=body)
    assert resp.status_code == 422
    assert "visoin" in resp.json()["detail"]
    mock_pool.fetchrow.assert_not_awaited()
    audit_append_spy.assert_not_awaited()


@pytest.fixture
async def model_failure_tail(api_db: ApiDatabase) -> tuple[uuid.UUID, datetime, uuid.UUID]:
    entry_id, unrelated_id, session_id = (uuid.uuid4() for _ in range(3))
    db_now = await api_db.pool.fetchval("SELECT now()")
    assert isinstance(db_now, datetime)
    cutoff = db_now - timedelta(hours=1)
    await api_db.pool.executemany(
        """
        INSERT INTO public.model_catalog (id, alias, runtime_type, model_id, complexity_tier)
        VALUES ($1, $2, 'codex', 'synthetic-failure-tail-model', 'workhorse')
        """,
        [(entry_id, "failure-tail-target"), (unrelated_id, "failure-tail-unrelated")],
    )
    await api_db.pool.executemany(
        """
        INSERT INTO public.dispatch_failures
            (catalog_entry_id, ts, error_code, error_message, butler, session_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        [
            (entry_id, db_now - timedelta(hours=26), "Expired", None, "general", None),
            (entry_id, cutoff, "ConnectionError", "Synthetic disconnect", "finance", None),
            (
                entry_id,
                cutoff + timedelta(minutes=30),
                "TimeoutError",
                "Synthetic timeout",
                "general",
                session_id,
            ),
            (unrelated_id, db_now - timedelta(minutes=15), "OtherCatalog", None, "finance", None),
            (entry_id, db_now - timedelta(hours=8), "Earlier", None, "general", None),
        ],
    )
    return entry_id, cutoff, session_id


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.usefixtures("model_failure_tail")
async def test_model_failures_404_on_missing_entry(api: httpx.AsyncClient) -> None:
    """Other stored catalog entries do not satisfy a missing entry lookup."""
    entry_id = uuid.uuid4()

    response = await api.get(f"/api/settings/models/{entry_id}/failures")

    assert response.status_code == 404
    assert response.json()["detail"] == f"Catalog entry not found: {entry_id}"


async def test_model_failures_empty_on_missing_table(app):
    """GET /api/settings/models/{id}/failures returns empty list if dispatch_failures absent."""
    import asyncpg.exceptions

    entry_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app, fetchval_result=entry_id)
    # fetchval returns entry exists (first call), then since_ts (second call)
    mock_pool.fetchval = AsyncMock(side_effect=[entry_id, "2026-01-01 00:00:00+00"])
    mock_pool.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError("dispatch_failures")
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/failures?since=24h")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_model_failures_422_on_bad_since(
    api: httpx.AsyncClient, model_failure_tail: tuple[uuid.UUID, datetime, uuid.UUID]
) -> None:
    """A stored catalog entry still rejects a malformed time window."""
    entry_id, _, _ = model_failure_tail

    response = await api.get(
        f"/api/settings/models/{entry_id}/failures", params={"since": "badvalue"}
    )

    assert response.status_code == 422
    assert response.json()["detail"].startswith("Invalid 'since' value 'badvalue'.")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_model_failures_returns_real_rows(
    api: httpx.AsyncClient, model_failure_tail: tuple[uuid.UUID, datetime, uuid.UUID]
) -> None:
    """Check catalog selection, both time windows, descending order and the full count."""
    entry_id, cutoff, session_id = model_failure_tail
    route = f"/api/settings/models/{entry_id}/failures"
    params = {"since": cutoff.isoformat(), "limit": "10"}

    response = await api.get(route, params=params)

    assert response.status_code == 200
    body = response.json()
    rows = [{**row, "ts": datetime.fromisoformat(row["ts"])} for row in body["data"]]
    assert rows == [
        {
            "ts": cutoff + timedelta(minutes=30),
            "error_code": "TimeoutError",
            "error_message": "Synthetic timeout",
            "butler": "general",
            "session_id": str(session_id),
        },
        {
            "ts": cutoff,
            "error_code": "ConnectionError",
            "error_message": "Synthetic disconnect",
            "butler": "finance",
            "session_id": None,
        },
    ]
    assert body["meta"] == {"total": 2, "offset": 0, "limit": 10, "has_more": False}

    limited = await api.get(route, params={**params, "limit": "1"})

    assert limited.status_code == 200
    assert limited.json() == {
        "data": body["data"][:1],
        "meta": {"total": 2, "offset": 0, "limit": 1, "has_more": True},
    }

    default_window = await api.get(route)

    assert default_window.status_code == 200
    default_body = default_window.json()
    assert [row["error_code"] for row in default_body["data"]] == [
        "TimeoutError",
        "ConnectionError",
        "Earlier",
    ]
    assert default_body["meta"] == {"total": 3, "offset": 0, "limit": 50, "has_more": False}


# ---------------------------------------------------------------------------
# GET /api/settings/models/{id}/attempts — failover attempt provenance
# ---------------------------------------------------------------------------


async def test_model_attempts_404_on_missing_entry(app):
    """GET /api/settings/models/{id}/attempts returns 404 when catalog entry absent."""
    _, mock_pool = _app_with_pool(app, fetchval_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{uuid.uuid4()}/attempts")
    assert resp.status_code == 404


async def test_model_attempts_empty_on_missing_table(app):
    """GET /api/settings/models/{id}/attempts returns empty list if table absent."""
    import asyncpg.exceptions

    entry_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app, fetchval_result=entry_id)
    mock_pool.fetchval = AsyncMock(side_effect=[entry_id, "2026-01-01 00:00:00+00"])
    mock_pool.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError("model_dispatch_attempts")
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/attempts?since=24h")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_model_attempts_422_on_bad_since(app):
    """GET /api/settings/models/{id}/attempts returns 422 on unrecognised 'since' value."""
    entry_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app, fetchval_result=entry_id)
    mock_pool.fetchval = AsyncMock(return_value=entry_id)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/attempts?since=badvalue")
    assert resp.status_code == 422


async def test_model_attempts_returns_real_rows(app):
    """Private runtime failures stay content-blind through dispatch evidence and its API."""
    from datetime import UTC, datetime

    from butlers.core.discretion_dispatcher import (
        PURPOSE_LANE_PRIVATE_CONTENT,
        DiscretionDispatcher,
    )
    from butlers.core.model_routing import CeilingStatus, QuotaStatus, SpendRoutingResult

    entry_id = uuid.uuid4()
    attempt_ts = datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)

    local_candidate = ("opencode", "ollama/qwen3.5:9b", [], entry_id, 30)
    resolved = (*local_candidate, "specialty")
    sentinel = (
        "Connection error: SENSITIVE_PROVIDER response_body=private prompt_fragment=do-not-publish"
    )
    adapter = MagicMock()
    adapter.invoke = AsyncMock(side_effect=RuntimeError(sentinel))
    adapter.last_process_info = None
    # The dispatcher owns a fresh worker per call; this factory double has no
    # process metadata, but keeps the original private failure behavior.
    adapter.create_worker.side_effect = lambda: SimpleNamespace(
        invoke=AsyncMock(side_effect=RuntimeError(sentinel)), last_process_info=None
    )
    dispatcher = DiscretionDispatcher(
        pool=MagicMock(),
        purpose_lane=PURPOSE_LANE_PRIVATE_CONTENT,
    )

    with (
        patch(
            "butlers.core.model_call.resolve_model_with_effective_tier",
            AsyncMock(return_value=resolved),
        ),
        patch(
            "butlers.core.model_call.apply_spend_routing_rules",
            AsyncMock(return_value=SpendRoutingResult(resolved=local_candidate)),
        ),
        patch(
            "butlers.core.model_call.check_token_quota",
            AsyncMock(
                return_value=QuotaStatus(
                    allowed=True,
                    usage_24h=0,
                    limit_24h=None,
                    usage_30d=0,
                    limit_30d=None,
                )
            ),
        ),
        patch(
            "butlers.core.model_call.check_monthly_ceiling",
            AsyncMock(return_value=CeilingStatus(allowed=True, mtd_usd=0.0, ceiling_usd=None)),
        ),
        patch.object(dispatcher, "_get_or_create_adapter", return_value=adapter),
        patch(
            "butlers.core.model_call.next_same_tier_candidate",
            AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.model_call.record_dispatch_attempt",
            AsyncMock(),
        ) as record_attempt,
    ):
        with pytest.raises(RuntimeError, match="same_tier_failover_exhausted"):
            await dispatcher.call("synthetic private input")

    evidence = record_attempt.await_args.kwargs
    assert evidence["outcome"] == "runtime_failure"
    assert evidence["purpose_lane"] == PURPOSE_LANE_PRIVATE_CONTENT
    assert evidence["failure_reason"] == "private_content_runtime_failure"
    assert evidence["error_code"] is None
    assert evidence["error_message"] is None
    assert sentinel not in repr(evidence)

    attempt_row = {
        "ts": attempt_ts,
        "butler": evidence["butler"],
        "outcome": evidence["outcome"],
        "attempt_index": evidence["attempt_index"],
        "failure_reason": evidence["failure_reason"],
        "error_code": evidence["error_code"],
        "error_message": evidence["error_message"],
        "tool_call_count": 0,
        "session_id": None,
        "logical_session_id": "req-abc-123",
        "duration_ms": evidence["duration_ms"],
        "purpose_lane": evidence["purpose_lane"],
    }

    _, mock_pool = _app_with_pool(app)
    mock_pool.fetchval = AsyncMock(side_effect=[entry_id, attempt_ts, 1])
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(attempt_row)])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/settings/models/{entry_id}/attempts?since=24h&limit=10")

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 1
    rows = body["data"]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "runtime_failure"
    assert rows[0]["attempt_index"] == 0
    assert rows[0]["butler"] == "__discretion__"
    assert rows[0]["logical_session_id"] == "req-abc-123"
    assert rows[0]["tool_call_count"] == 0
    assert rows[0]["session_id"] is None
    assert rows[0]["purpose_lane"] == "private_content"
    assert rows[0]["failure_reason"] == "private_content_runtime_failure"
    assert rows[0]["error_code"] is None
    assert rows[0]["error_message"] is None
    assert sentinel not in resp.text
    assert "ORDER BY ts DESC, id DESC" in mock_pool.fetch.call_args.args[0]


# ---------------------------------------------------------------------------
# GET /api/dispatch/attempts — session-scoped failover provenance
# ---------------------------------------------------------------------------


async def test_dispatch_attempts_422_when_no_filter(app):
    """GET /api/dispatch/attempts returns 422 when neither session_id nor logical_session_id given."""
    _, _mock_pool = _app_with_pool(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/dispatch/attempts")
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"outcome": "quota_skip", "session_id": str(uuid.uuid4())},
        {"outcome": "quota_skip", "logical_session_id": "req-mixed-mode"},
    ],
)
async def test_dispatch_attempts_rejects_mixed_fleet_and_session_modes_before_query(app, params):
    """Fleet outcome mode cannot be combined with either session-mode selector."""
    _, mock_pool = _app_with_pool(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/dispatch/attempts", params=params)

    assert resp.status_code == 422
    assert "cannot be combined" in resp.json()["detail"]
    mock_pool.fetch.assert_not_awaited()
    mock_pool.fetchval.assert_not_awaited()


@pytest.fixture
async def dispatch_attempts_history(api_db: ApiDatabase) -> tuple[uuid.UUID, str]:
    session_id = uuid.uuid4()
    other_session_id = uuid.uuid4()
    logical_id = "synthetic-dispatch-cycle"
    earliest = datetime(2026, 5, 25, 9, tzinfo=UTC)
    tied = datetime(2026, 5, 25, 10, tzinfo=UTC)
    later = datetime(2026, 5, 25, 11, tzinfo=UTC)
    catalog_id = await api_db.pool.fetchval(
        """
        INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)
        VALUES ('dispatch-fixture', 'codex', 'synthetic-dispatch-model', 'workhorse')
        RETURNING id
        """
    )
    # Each selector sees two rows tied by attempt index and timestamp, followed
    # by a lower ID at a later time and a higher attempt index at an earlier time.
    rows = [
        (90, session_id, logical_id, 1, earliest, "success", None),
        (77, session_id, "other-cycle", 0, tied, "runtime_failure", "session-id-tie"),
        (14, session_id, "other-cycle", 0, later, "quota_skip", "session-later"),
        (62, None, logical_id, 0, tied, "quota_skip", "Token quota exhausted: 24h"),
        (32, session_id, logical_id, 0, tied, "runtime_failure", "cli_missing"),
        (13, other_session_id, logical_id, 0, later, "runtime_failure", "logical-later"),
        (5, other_session_id, f"{logical_id}-extra", 0, earliest, "runtime_failure", "decoy"),
    ]
    await api_db.pool.executemany(
        """
        INSERT INTO public.model_dispatch_attempts
            (id, session_id, logical_session_id, attempt_index, ts, outcome, failure_reason,
             catalog_entry_id, butler)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'general')
        """,
        [(*row, catalog_id) for row in rows],
    )
    return session_id, logical_id


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_attempts_supports_both_session_selectors(
    api: httpx.AsyncClient, dispatch_attempts_history: tuple[uuid.UUID, str]
) -> None:
    """Either selector can match and the intersection appears once."""
    session_id, logical_id = dispatch_attempts_history
    params = {"session_id": str(session_id), "logical_session_id": logical_id}

    response = await api.get("/api/dispatch/attempts", params=params)
    assert response.status_code == 200
    body = response.json()
    assert [row["failure_reason"] for row in body["data"]] == [
        "cli_missing",
        "Token quota exhausted: 24h",
        "session-id-tie",
        "logical-later",
        "session-later",
        None,
    ]
    assert body["meta"] == {"total": 6, "offset": 0, "limit": 100, "has_more": False}

    limited = await api.get("/api/dispatch/attempts", params={**params, "limit": "2"})
    assert limited.status_code == 200
    assert limited.json() == {
        "data": body["data"][:2],
        "meta": {"total": 6, "offset": 0, "limit": 2, "has_more": True},
    }


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"session_id": ""}, id="blank-session-id"),
        pytest.param({"session_id": "not-a-uuid"}, id="invalid-session-id"),
        pytest.param(
            {"outcome": "quota_skip", "session_id": ""},
            id="outcome-with-blank-session-id",
        ),
        pytest.param(
            {"outcome": "quota_skip", "session_id": "not-a-uuid"},
            id="outcome-with-invalid-session-id",
        ),
    ],
)
async def test_dispatch_attempts_rejects_invalid_session_id_before_db_lookup(app, params):
    """Malformed session IDs fail FastAPI validation rather than reaching the SQL UUID cast."""
    _, mock_pool = _app_with_pool(app)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/dispatch/attempts", params=params)

    assert resp.status_code == 422
    mock_pool.fetch.assert_not_awaited()
    mock_pool.fetchval.assert_not_awaited()


async def test_dispatch_attempts_empty_on_missing_table(app):
    """GET /api/dispatch/attempts returns empty list if table absent."""
    import asyncpg.exceptions

    session_id = uuid.uuid4()
    _, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError("model_dispatch_attempts")
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/dispatch/attempts?session_id={session_id}")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_attempts_by_session_id(
    api: httpx.AsyncClient, dispatch_attempts_history: tuple[uuid.UUID, str]
) -> None:
    """The UUID selector orders matching attempts by index, timestamp, then ID."""
    session_id, logical_id = dispatch_attempts_history
    response = await api.get("/api/dispatch/attempts", params={"session_id": str(session_id)})

    assert response.status_code == 200
    body = response.json()
    assert [
        (row["outcome"], row["attempt_index"], row["failure_reason"], row["logical_session_id"])
        for row in body["data"]
    ] == [
        ("runtime_failure", 0, "cli_missing", logical_id),
        ("runtime_failure", 0, "session-id-tie", "other-cycle"),
        ("quota_skip", 0, "session-later", "other-cycle"),
        ("success", 1, None, logical_id),
    ]
    assert body["meta"] == {"total": 4, "offset": 0, "limit": 100, "has_more": False}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_attempts_by_logical_session_id(
    api: httpx.AsyncClient, dispatch_attempts_history: tuple[uuid.UUID, str]
) -> None:
    """An exact opaque logical ID matches across sessions, including quota skips without a UUID."""
    _, logical_id = dispatch_attempts_history
    response = await api.get("/api/dispatch/attempts", params={"logical_session_id": logical_id})

    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert [(row["outcome"], row["failure_reason"]) for row in data] == [
        ("runtime_failure", "cli_missing"),
        ("quota_skip", "Token quota exhausted: 24h"),
        ("runtime_failure", "logical-later"),
        ("success", None),
    ]
    assert data[1]["session_id"] is None
    assert {row["logical_session_id"] for row in data} == {logical_id}
    assert body["meta"] == {"total": 4, "offset": 0, "limit": 100, "has_more": False}


# ---------------------------------------------------------------------------
# GET /api/dispatch/attempts?outcome=... — fleet-wide mode (bu-7o89u.3)
#
# Powers the /spend fleet-halt state: "how many dispatches has the fleet
# denied recently" without a session id in hand. quota_skip rows are written
# both for the monthly-ceiling hard block (spawner.py:1179-1202) AND for
# routine same-tier token-quota failovers (spawner.py:1082,1122) -- the
# latter is normal operation, not a fleet halt -- so reason_prefix is what
# isolates the ceiling-specific denials the dashboard cares about.
# ---------------------------------------------------------------------------


@pytest.fixture
async def fleet_dispatch_attempts(api_db: ApiDatabase) -> tuple[datetime, str]:
    since = datetime(2026, 7, 1, tzinfo=UTC)
    earlier = since - timedelta(days=1)
    later = since + timedelta(days=1)
    first_session = uuid.uuid4()
    second_session = uuid.uuid4()
    prefix = "Monthly spend ceiling reached: fixture_%"
    catalog_id = await api_db.pool.fetchval(
        """
        INSERT INTO public.model_catalog (alias, runtime_type, model_id, complexity_tier)
        VALUES ('fleet-fixture', 'codex', 'synthetic-fleet-model', 'workhorse')
        RETURNING id
        """
    )
    wildcard_decoy = "Monthly spend ceiling reached: fixtureXY"
    rows = [
        (55, first_session, "boundary-high", since, "quota_skip", f"{prefix} high", "general"),
        (90, second_session, "later", later, "quota_skip", f"{prefix} later", "finance"),
        (51, None, "wildcard-decoy", later, "quota_skip", wildcard_decoy, "general"),
        (5, first_session, "older", earlier, "quota_skip", f"{prefix} older", "general"),
        (
            64,
            second_session,
            "wrong-outcome",
            later,
            "success",
            f"{prefix} other outcome",
            "finance",
        ),
        (88, None, "ordinary-denial", since, "quota_skip", "Token quota exhausted: 24h", "finance"),
        (7, None, "boundary-low", since, "quota_skip", f"{prefix} low", "general"),
    ]
    await api_db.pool.executemany(
        """
        INSERT INTO public.model_dispatch_attempts
            (id, session_id, logical_session_id, ts, outcome, failure_reason, butler, catalog_entry_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        [(*row, catalog_id) for row in rows],
    )
    return since, prefix


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.usefixtures("fleet_dispatch_attempts")
async def test_dispatch_attempts_outcome_mode_returns_rows_ordered_desc(
    api: httpx.AsyncClient,
) -> None:
    """Fleet mode selects one outcome across sessions and defaults to descending time and ID."""
    response = await api.get("/api/dispatch/attempts", params={"outcome": "quota_skip"})

    assert response.status_code == 200
    body = response.json()
    assert [(row["logical_session_id"], row["butler"]) for row in body["data"]] == [
        ("later", "finance"),
        ("wildcard-decoy", "general"),
        ("ordinary-denial", "finance"),
        ("boundary-high", "general"),
        ("boundary-low", "general"),
        ("older", "general"),
    ]
    assert {row["outcome"] for row in body["data"]} == {"quota_skip"}
    assert body["meta"] == {"total": 6, "offset": 0, "limit": 100, "has_more": False}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_attempts_outcome_mode_reason_prefix_and_since_filter(
    api: httpx.AsyncClient, fleet_dispatch_attempts: tuple[datetime, str]
) -> None:
    """Fleet filtering uses a literal prefix, inclusive time, and a count before the page limit."""
    since, prefix = fleet_dispatch_attempts
    params = {
        "outcome": "quota_skip",
        "reason_prefix": prefix,
        "since": since.isoformat(),
        "order": "asc",
    }
    response = await api.get("/api/dispatch/attempts", params=params)

    assert response.status_code == 200
    body = response.json()
    assert [(row["logical_session_id"], row["failure_reason"]) for row in body["data"]] == [
        ("boundary-low", f"{prefix} low"),
        ("boundary-high", f"{prefix} high"),
        ("later", f"{prefix} later"),
    ]
    assert body["meta"] == {"total": 3, "offset": 0, "limit": 100, "has_more": False}

    limited = await api.get("/api/dispatch/attempts", params={**params, "limit": "1"})

    assert limited.status_code == 200
    assert limited.json() == {
        "data": body["data"][:1],
        "meta": {"total": 3, "offset": 0, "limit": 1, "has_more": True},
    }


async def test_dispatch_attempts_outcome_mode_empty_on_missing_table(app):
    """Fleet-mode query also falls back to an empty page (not 503) pre-migration."""
    import asyncpg.exceptions

    _, mock_pool = _app_with_pool(app)
    mock_pool.fetch = AsyncMock(
        side_effect=asyncpg.exceptions.UndefinedTableError("model_dispatch_attempts")
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/dispatch/attempts?outcome=quota_skip")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_dispatch_attempts_invalid_order_returns_422(app):
    """order must be 'asc' or 'desc'; anything else is a validation error."""
    _, _mock_pool = _app_with_pool(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/dispatch/attempts?outcome=quota_skip&order=sideways")
    assert resp.status_code == 422


# Transactional model override receipts: the HTTP boundary, production audit.append
# and owning SQL calls execute; this store models commit/rollback, not PostgreSQL.
class _OverrideStore:
    def __init__(self):
        self.rows = {}
        self.audits = []
        self.in_transaction = False
        self.fail = None
        self.audit_entered = asyncio.Event()
        self.audit_release = asyncio.Event()
        self.audit_release.set()

    def acquire(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def acquired():
            from types import SimpleNamespace

            yield SimpleNamespace(
                transaction=self.transaction,
                is_in_transaction=lambda: self.in_transaction,
                fetchrow=self.fetchrow,
                execute=self.execute,
                fetchval=self._append_audit,
            )

        return acquired()

    def transaction(self):
        from contextlib import asynccontextmanager
        from copy import deepcopy

        @asynccontextmanager
        async def transaction():
            before = deepcopy((self.rows, self.audits))
            assert not self.in_transaction
            self.in_transaction = True
            try:
                yield
            except BaseException:
                self.rows, self.audits = before
                raise
            finally:
                self.in_transaction = False

        return transaction()

    async def fetchrow(self, sql, *args):
        assert self.in_transaction and "INSERT INTO public.butler_model_overrides" in sql
        if self.fail == "mutation":
            raise RuntimeError("synthetic mutation failure")
        name, catalog_id, enabled, priority, tier = args
        key = (name, catalog_id)
        row = self.rows.setdefault(key, {"id": uuid.uuid4()})
        row.update(
            butler_name=name,
            catalog_entry_id=catalog_id,
            alias="control-model",
            enabled=enabled,
            priority=priority,
            complexity_tier=tier,
        )
        return {"id": row["id"]}

    async def fetch(self, sql, *args):
        assert "public.butler_model_overrides" in sql
        return [dict(row) for row in self.rows.values() if row["id"] in args[0]]

    async def execute(self, sql, *args):
        if sql == "SELECT 1":
            assert self.in_transaction
            return "SELECT 1"
        assert self.in_transaction and "DELETE FROM public.butler_model_overrides" in sql
        if self.fail == "mutation":
            raise RuntimeError("synthetic mutation failure")
        for key, row in list(self.rows.items()):
            if row["id"] == args[0] and row["butler_name"] == args[1]:
                del self.rows[key]
                return "DELETE 1"
        return "DELETE 0"

    async def fetchval(self, sql, *args):
        raise AssertionError("Owning audit must use the acquired mutation connection")

    async def _append_audit(self, sql, *args):
        assert "INSERT INTO public.audit_log" in sql
        assert self.in_transaction  # A pool-level post-commit emit is not atomic.
        self.audit_entered.set()
        await self.audit_release.wait()
        if self.fail == "audit_missing":
            raise asyncpg.UndefinedTableError("synthetic missing audit table")
        if self.fail == "audit":
            raise RuntimeError("synthetic audit failure")
        self.audits.append(
            {
                "actor": args[0],
                "action": args[1],
                "target": args[2],
                "reason": args[3],
                "metadata": args[6],
            }
        )
        return len(self.audits)


def _override_http(store):
    application = create_authenticated_domain_app(api_key="owner-key")
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool.return_value = store
    db.pool.return_value = store
    application.dependency_overrides[get_db] = lambda: db
    return application, db


async def test_override_batch_and_delete_append_one_server_attributed_receipt_per_request():
    store = _OverrideStore()
    application, db = _override_http(store)
    first, second = uuid.uuid4(), uuid.uuid4()
    with patch("butlers.api.dashboard_audit_middleware.get_db_manager", return_value=db):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            payload = {
                "reason": "  use the approved workhorse  ",
                "actor": "forged",
                "price_preview_unavailable": True,
                "overrides": [
                    {"catalog_entry_id": str(first), "priority": 3},
                    {"catalog_entry_id": str(second), "enabled": False},
                ],
            }
            response = await client.put("/api/butlers/qa/model-overrides", json=payload)
            assert response.status_code == 200
            assert len(response.json()["data"]) == 2
            assert len(store.rows) == 2 and len(store.audits) == 1
            assert store.audits[0]["actor"] == "owner"
            assert store.audits[0]["reason"] == "use the approved workhorse"
            assert store.audits[0]["metadata"]["client_price_preview_unavailable"] is True
            override_id = store.rows[("qa", first)]["id"]
            repeated = await client.put("/api/butlers/qa/model-overrides", json=payload)
            assert repeated.status_code == 200
            assert store.rows[("qa", first)]["id"] == override_id
            assert len(store.rows) == 2 and len(store.audits) == 2
            removed = await client.request(
                "DELETE",
                f"/api/butlers/qa/model-overrides/{override_id}",
                json={"reason": "restore defaults", "actor": "forged"},
            )
            assert removed.status_code == 200
            assert ("qa", first) not in store.rows
            assert len(store.audits) == 3
            assert store.audits[-1]["actor"] == "owner"
            assert store.audits[-1]["reason"] == "restore defaults"
            remaining = dict(store.rows)
            empty = await client.put(
                "/api/butlers/qa/model-overrides",
                json={"reason": "no remaining changes", "overrides": []},
            )
            assert empty.status_code == 200 and empty.json()["data"] == []
            assert store.rows == remaining and len(store.audits) == 4


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_override_reason_validation_and_atomic_failure(method):
    from copy import deepcopy

    store = _OverrideStore()
    model_id, override_id = uuid.uuid4(), uuid.uuid4()
    seed = {
        "id": override_id,
        "butler_name": "qa",
        "catalog_entry_id": model_id,
        "alias": "control-model",
        "enabled": True,
        "priority": None,
        "complexity_tier": None,
    }
    store.rows[("qa", model_id)] = seed
    application, _ = _override_http(store)
    path = "/api/butlers/qa/model-overrides" + (f"/{override_id}" if method == "DELETE" else "")
    valid = {"reason": "documented change"}
    if method == "PUT":
        valid["overrides"] = [{"catalog_entry_id": str(model_id), "priority": 9}]
    before = deepcopy(store.rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        for reason in [None, "", " \n\t", 42, "x" * 2001]:
            body = {**valid, "reason": reason}
            response = await client.request(method, path, json=body)
            assert response.status_code == 422
            assert store.rows == before and store.audits == []
        missing = {k: v for k, v in valid.items() if k != "reason"}
        assert (await client.request(method, path, json=missing)).status_code == 422
        assert store.rows == before and store.audits == []
        for failure, status in [("audit_missing", 503), ("audit", 500), ("mutation", 500)]:
            store.fail = failure
            response = await client.request(method, path, json=valid)
            assert response.status_code == status
            assert store.rows == before and store.audits == []


async def test_override_cancellation_before_audit_commit_rolls_back():
    store = _OverrideStore()
    store.audit_release.clear()
    application, _ = _override_http(store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        task = asyncio.create_task(
            client.put(
                "/api/butlers/qa/model-overrides",
                json={
                    "reason": "bounded cancellation control",
                    "overrides": [{"catalog_entry_id": str(uuid.uuid4())}],
                },
            )
        )
        try:
            # Readiness only: a loaded runner can take seconds to reach the audit write. The
            # cancellation assertions below stay tight.
            await asyncio.wait_for(store.audit_entered.wait(), 30)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert store.rows == {} and store.audits == []
        finally:
            store.audit_release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
