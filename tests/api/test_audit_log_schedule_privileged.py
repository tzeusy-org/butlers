"""The default privileged Audit Log view lists owner schedule mutations (bu-q7vx1q.57).

A unit test pins the predicate; a real-Postgres test (CI-only without Docker)
runs it against ``public.audit_log`` and checks that scheduler tick and noise
rows stay out.
"""

from __future__ import annotations

import re
import shutil
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.api.routers.audit import _PRIVILEGED_CONSEQUENCE_SQL
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app as create_app
from tests.audit_helpers import clear_audit_log_for_test

SCHEDULE_ACTIONS = (
    "schedule.create",
    "schedule.update",
    "schedule.delete",
    "schedule.trigger",
    "schedule.toggle",
)
TICK_AND_NOISE_ACTIONS = (
    "scheduler.tick",
    "schedule_tick",
    "scheduled_task.run",
    "GET /api/schedules",
    "butler_heartbeat",
)


def _like_patterns(sql: str) -> list[str]:
    return re.findall(r"action LIKE '([^']+)'", sql)


def _matches_like(pattern: str, value: str) -> bool:
    regex = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.fullmatch(regex, value) is not None


def _privileged_by_action(action: str) -> bool:
    return any(_matches_like(p, action) for p in _like_patterns(_PRIVILEGED_CONSEQUENCE_SQL))


def test_privileged_predicate_names_the_schedule_family() -> None:
    assert "schedule.%" in _like_patterns(_PRIVILEGED_CONSEQUENCE_SQL)
    assert "NOT LIKE" not in _PRIVILEGED_CONSEQUENCE_SQL


@pytest.mark.parametrize("action", SCHEDULE_ACTIONS)
def test_schedule_mutations_are_privileged(action: str) -> None:
    assert _privileged_by_action(action)


@pytest.mark.parametrize("action", TICK_AND_NOISE_ACTIONS)
def test_scheduler_tick_and_noise_stay_out(action: str) -> None:
    assert not _privileged_by_action(action)


docker_available = shutil.which("docker") is not None
requires_docker = pytest.mark.skipif(not docker_available, reason="Docker not available")


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "switchboard"]
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    await clear_audit_log_for_test(p)
    yield p
    await p.close()


@pytest.fixture
def audit_app(pool: asyncpg.Pool) -> FastAPI:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    mock_db.pool.return_value = pool
    application = create_app()
    application.dependency_overrides[get_db] = lambda: mock_db
    return application


@requires_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_default_privileged_view_lists_schedule_rows_and_not_tick_noise(
    pool: asyncpg.Pool, audit_app: FastAPI
) -> None:
    rows = [(a, "success") for a in (*SCHEDULE_ACTIONS, *TICK_AND_NOISE_ACTIONS)]
    await pool.executemany(
        "INSERT INTO public.audit_log (actor, action, result) VALUES ('owner', $1, $2)", rows
    )

    transport = httpx.ASGITransport(app=audit_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/audit-log?kind=privileged&limit=100")

    assert resp.status_code == 200
    actions = {entry["action"] for entry in resp.json()["data"]}
    assert set(SCHEDULE_ACTIONS) <= actions
    assert not actions & set(TICK_AND_NOISE_ACTIONS)
