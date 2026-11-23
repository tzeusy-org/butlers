# Spec: REQ-module-calendar-037
"""POST /api/calendar/sources/role over a real default-size pool (bu-5ii4gj.1 S2).

The per-source advisory lock is held on a pool connection; the schema write must reuse it, or a
small pool deadlocks under concurrent same-source requests. Synthetic data only.
"""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import MagicMock

import httpx
import pytest

from butlers.api.deps import get_db
from butlers.testing.db_manager import db_manager
from butlers.testing.fake_pool import fake_pool

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

KEY = "provider:google:partner@example.test"


async def test_concurrent_same_source_requests_do_not_exhaust_the_default_pool(db, app) -> None:
    pool = await db("core")  # the API default: max 3 connections
    assert pool.get_max_size() == 3
    await pool.execute(
        "INSERT INTO calendar_sources (source_key, source_kind, lane, time_role) "
        "VALUES ($1, 'provider_event', 'user', 'unclassified')",
        KEY,
    )
    manager = db_manager(
        {"general": pool, "switchboard": fake_pool(execute=None)}, modules={"general": {"calendar"}}
    )
    manager.butlers_with_module = MagicMock(return_value=["general"])
    app.dependency_overrides[get_db] = lambda: manager
    roles = ["household_member", "team", "owner", "other_person", "household_member"]

    async def post(role: str) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(
                "/api/calendar/sources/role", json={"source_key": KEY, "role": role}
            )

    responses = await asyncio.wait_for(asyncio.gather(*(post(r) for r in roles)), timeout=30)

    assert [r.status_code for r in responses] == [200] * len(roles)
    stored = await pool.fetchval(
        "SELECT time_role FROM calendar_sources WHERE source_key = $1", KEY
    )
    assert stored in roles
