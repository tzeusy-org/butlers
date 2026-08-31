"""Owner-gated Models runtime-attention observation and reissue contracts.

REQ-dashboard-model-settings-002; REQ-runtime-attention-outbox-003.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.model_settings import _get_db_manager

pytestmark = pytest.mark.unit


def _db(app, *, fetch_rows=(), fetchrow=None, fetch_side_effect=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=list(fetch_rows), side_effect=fetch_side_effect)
    pool.fetchrow = AsyncMock(return_value=fetchrow)
    manager = MagicMock(spec=DatabaseManager)
    manager.credential_shared_pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: manager
    return pool


def _episode_row(*, episode_id=None, state="uncertain", successor_id=None, manual_reissue_of=None):
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    return {
        "catalog_entry_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "episode_id": episode_id or uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "lifecycle_state": state,
        "created_at": now,
        "updated_at": now,
        "delivered_at": None,
        "delivery_error_class": "transport_uncertain" if state == "uncertain" else None,
        "delivery_error_detail": "transport_timeout" if state == "uncertain" else None,
        "manual_reissue_of": manual_reissue_of,
        "successor_id": successor_id,
    }


@pytest.mark.parametrize(
    "configured,header,expected", [(False, None, 503), (True, None, 401), (True, "wrong", 401)]
)
async def test_attention_owner_gate_precedes_observation(
    app, monkeypatch: pytest.MonkeyPatch, configured: bool, header: str | None, expected: int
) -> None:
    """REQ-dashboard-model-settings-002: no protected read occurs before owner auth."""
    if configured:
        monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    else:
        monkeypatch.delenv("DASHBOARD_API_KEY", raising=False)
    pool = _db(app)
    headers = {"X-API-Key": header} if header is not None else {}

    with patch("butlers.api.owner_control.dashboard_owner_control_total") as counter:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/settings/models/attention", headers=headers)

    assert response.status_code == expected
    pool.fetch.assert_not_awaited()
    counter.labels.assert_called_once_with(outcome="unavailable" if expected == 503 else "denied")


async def test_attention_observation_distinguishes_no_episode_from_unavailable(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-dashboard-model-settings-002: empty and degraded are different facts."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    pool = _db(app, fetch_rows=[])
    headers = {"X-API-Key": "owner-key"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        empty = await client.get("/api/settings/models/attention", headers=headers)
        pool.fetch.side_effect = asyncpg.UndefinedTableError("redacted")
        degraded = await client.get("/api/settings/models/attention", headers=headers)

    assert empty.status_code == 200
    assert empty.json()["data"] == {"available": True, "episodes": {}}
    assert degraded.status_code == 200
    assert degraded.json()["data"] == {"available": False, "episodes": {}}


async def test_attention_observation_returns_only_sanitized_state(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    row = _episode_row()
    _db(app, fetch_rows=[row])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/settings/models/attention", headers={"X-API-Key": "owner-key"}
        )

    assert response.status_code == 200
    payload = next(iter(response.json()["data"]["episodes"].values()))
    assert payload["lifecycle_state"] == "uncertain"
    assert payload["reissue_eligible"] is True
    assert set(payload) == {
        "episode_id",
        "lifecycle_state",
        "created_at",
        "updated_at",
        "delivered_at",
        "safe_reason",
        "manual_reissue_of",
        "successor_id",
        "reissue_eligible",
    }


async def test_uncertain_manual_successor_is_not_advertised_as_reissue_eligible(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-dashboard-model-settings-002: the projection matches the DB lineage gate."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    row = _episode_row(manual_reissue_of=uuid.uuid4())
    _db(app, fetch_rows=[row])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/settings/models/attention", headers={"X-API-Key": "owner-key"}
        )

    assert response.status_code == 200
    episode = next(iter(response.json()["data"]["episodes"].values()))
    assert episode["lifecycle_state"] == "uncertain"
    assert episode["manual_reissue_of"] is not None
    assert episode["reissue_eligible"] is False


async def test_reissue_owner_gate_precedes_side_effect(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    pool = _db(app)
    episode_id = uuid.uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/settings/models/attention/{episode_id}/reissue")

    assert response.status_code == 401
    pool.fetchrow.assert_not_awaited()


async def test_reissue_returns_atomic_successor_without_transport_or_breaker_write(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REQ-runtime-attention-outbox-003: the API invokes only the atomic DB operation."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    original_id = uuid.uuid4()
    successor_id = uuid.uuid4()
    row = {
        "original_episode_id": original_id,
        "successor_episode_id": successor_id,
        "successor_state": "pending",
        "created": True,
    }
    pool = _db(app, fetchrow=row)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/settings/models/attention/{original_id}/reissue",
            headers={"X-API-Key": "owner-key"},
        )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "original_episode_id": str(original_id),
        "successor_episode_id": str(successor_id),
        "successor_state": "pending",
        "created": True,
    }
    sql = pool.fetchrow.await_args.args[0]
    assert "reissue_runtime_attention_episode" in sql
    assert "model_dispatch_attempts" not in sql
    assert "notify" not in sql.lower()


async def test_non_uncertain_reissue_is_denied_without_false_success(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    error = asyncpg.RaiseError("runtime-attention episode is not eligible for reissue")
    error.sqlstate = "55000"
    pool = _db(app)
    pool.fetchrow.side_effect = error

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/settings/models/attention/{uuid.uuid4()}/reissue",
            headers={"X-API-Key": "owner-key"},
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "Attention episode is not eligible for reissue"


@pytest.mark.parametrize(
    "failure",
    [
        asyncpg.InterfaceError("pool is closed"),
        OSError("connection lost"),
        RuntimeError("pool is closing"),
        TimeoutError("database deadline"),
    ],
)
async def test_reissue_expected_connection_failures_are_truthful_unavailability(
    app, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    """REQ-dashboard-model-settings-002: expected infrastructure failures are 503, not 500."""
    monkeypatch.setenv("DASHBOARD_API_KEY", "owner-key")
    pool = _db(app)
    pool.fetchrow.side_effect = failure

    with patch("butlers.api.routers.model_settings.model_attention_operator_total") as counter:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/settings/models/attention/{uuid.uuid4()}/reissue",
                headers={"X-API-Key": "owner-key"},
            )

    assert response.status_code == 503
    assert response.json()["detail"] == "Attention reissue is unavailable"
    counter.labels.assert_called_once_with(operation="reissue", outcome="unavailable")
