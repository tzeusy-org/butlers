"""Real-Postgres tests for the owner-declared location retention policy.

Covers REQ-chronicler-api-018 (bu-s11n0s.7): the migrated ``chronicler_028``
policy row, the protected compare-and-set setter and its fail-closed reads.
Every refusal re-reads the stored row to prove nothing was written.
"""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture
async def chronicler_pool(db) -> asyncpg.Pool:
    return await db("chronicler")


_URL = "/api/chronicler/location-retention"
_STORED = "SELECT days, version, updated_by FROM location_retention_policy WHERE id = 1"


def _client(pool) -> httpx.AsyncClient:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    app.dependency_overrides[get_db] = lambda: db
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_owner_shortens_horizon_by_compare_and_set(chronicler_pool) -> None:
    """Default is 30 days, not enforced; a strict CAS PUT shortens it; bad bodies write nothing."""
    async with _client(chronicler_pool) as client:
        initial = (await client.get(_URL)).json()
        assert (initial["days"], initial["version"], initial["status"]) == (30, 1, "not_enforced")
        assert (initial["min_days"], initial["max_days"]) == (1, 30)

        refused = [
            {"days": True, "expected_version": 1},
            {"days": "7", "expected_version": 1},
            {"days": 7.0, "expected_version": 1},
            {"days": None, "expected_version": 1},
            {"days": 0, "expected_version": 1},
            {"days": 31, "expected_version": 1},
            {"days": 7},
            {"days": 7, "expected_version": 1, "actor": "someone-else"},
        ]
        for body in refused:
            resp = await client.put(_URL, json=body)
            assert resp.status_code == 422, (body, resp.text)
        assert tuple(await chronicler_pool.fetchrow(_STORED)) == (30, 1, "system:default")

        ok = await client.put(_URL, json={"days": 7, "expected_version": 1})
        assert ok.status_code == 200, ok.text
        assert (ok.json()["days"], ok.json()["version"]) == (7, 2)

        stale = await client.put(_URL, json={"days": 14, "expected_version": 1})
        assert stale.status_code == 409, stale.text
        assert stale.json()["detail"]["current_version"] == 2

    days, version, updated_by = await chronicler_pool.fetchrow(_STORED)
    assert (days, version) == (7, 2)
    assert updated_by == "owner"


async def test_unauthenticated_put_is_refused_before_any_write(chronicler_pool) -> None:
    async with _client(chronicler_pool) as client:
        resp = await client.put(
            _URL,
            json={"days": 3, "expected_version": 1},
            headers={"x-api-key": "wrong-owner-key"},
        )
        assert resp.status_code == 401, resp.text
        # Positive control: the same body with the owner credential is applied.
        assert (await client.put(_URL, json={"days": 3, "expected_version": 1})).status_code == 200
    assert (await chronicler_pool.fetchrow(_STORED))["version"] == 2


async def test_missing_or_out_of_range_policy_fails_closed(chronicler_pool) -> None:
    """The DB refuses an out-of-range horizon; a missing row is 503, never a default."""
    with pytest.raises(asyncpg.CheckViolationError):
        await chronicler_pool.execute("UPDATE location_retention_policy SET days = 31 WHERE id = 1")

    await chronicler_pool.execute("DELETE FROM location_retention_policy")
    async with _client(chronicler_pool) as client:
        assert (await client.get(_URL)).status_code == 503
        put = await client.put(_URL, json={"days": 5, "expected_version": 1})
        assert put.status_code == 503
    assert await chronicler_pool.fetchval("SELECT count(*) FROM location_retention_policy") == 0
