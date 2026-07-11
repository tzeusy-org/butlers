"""Real-Postgres integration tests for the sessions degraded-source envelope (bu-tpudw.2).

Mocked-pool unit tests (tests/api/test_sessions_pagination.py,
tests/api/test_sessions_aggregate.py, tests/api/test_session_detail_global.py)
stub ``fan_out_with_status`` and hand it a synthetic ``failed`` list, so they
prove the router THREADING but cannot prove that a genuine pool fault actually
lands in that list — the exact mocked-green-but-main-red gap this repo has been
burned by before (PR #2598 class). These tests exercise the REAL
``DatabaseManager.fan_out_with_status`` against a migrated Postgres: two butler
pools over distinct schemas, one deliberately closed so its per-butler
``SELECT ... FROM sessions`` raises for real. We then assert:

- ``GET /api/sessions`` returns the reachable page AND names the down pool in
  ``meta.sources_degraded`` (never a truthful-looking whole list),
- ``GET /api/sessions/aggregate`` names the down pool in ``meta.sources_degraded``
  (so a summed ``failed_count: 0`` is not read as an all-clear),
- ``GET /api/sessions/{id}`` splits 404 (unknown across reachable pools) from
  503 (a pool was unreachable, naming it) — and still returns 200 for a session
  that lives in a reachable pool even while a sibling pool is down.
"""

from __future__ import annotations

import shutil
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.sessions import _get_db_manager as _sessions_get_db
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE_URL = "http://test"

# Two real per-butler schemas the core chain provisions a `sessions` table in.
_REACHABLE = "general"
_DOWN = "finance"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain — per-butler schemas each with a `sessions` table."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def db_manager(migrated_db_url: str):
    """A real DatabaseManager with two butler pools over distinct schemas.

    ``general`` and ``finance`` are real role schemas from core_001, each with
    its own ``sessions`` table (schema-scoped via the pool's search_path).
    """
    parsed = urlparse(migrated_db_url)
    mgr = DatabaseManager(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
    )
    db_name = parsed.path.lstrip("/")
    await mgr.add_butler(_REACHABLE, db_name=db_name, db_schema=_REACHABLE)
    await mgr.add_butler(_DOWN, db_name=db_name, db_schema=_DOWN)
    try:
        yield mgr
    finally:
        await mgr.close()


async def _seed_session(mgr: DatabaseManager, butler: str, *, success: bool | None) -> str:
    """Insert one minimal sessions row into ``butler``'s schema; return its id."""
    session_id = uuid4()
    await mgr.pool(butler).execute(
        """
        INSERT INTO sessions (id, prompt, trigger_source, request_id, success)
        VALUES ($1, 'p', 'schedule', $2, $3)
        """,
        session_id,
        f"req-{session_id}",
        success,
    )
    return str(session_id)


def _app(mgr: DatabaseManager):
    app = create_app()
    app.dependency_overrides[_sessions_get_db] = lambda: mgr
    return app


async def test_list_names_genuinely_down_pool(db_manager) -> None:
    """A closed pool surfaces as a real degraded source on the list envelope."""
    await _seed_session(db_manager, _REACHABLE, success=True)
    await db_manager.pool(_DOWN).close()  # genuine fault: fetch will raise

    app = _app(db_manager)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 200
    body = resp.json()
    # The reachable pool's row still comes back — partial, not empty...
    assert len(body["data"]) == 1
    assert body["data"][0]["butler"] == _REACHABLE
    # ...and the genuinely-down pool is named, never silently dropped.
    assert body["meta"]["sources_degraded"] == [_DOWN]


async def test_list_no_degraded_when_all_pools_healthy(db_manager) -> None:
    """Every pool answering -> sources_degraded is null (honest complete page)."""
    await _seed_session(db_manager, _REACHABLE, success=True)

    app = _app(db_manager)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get("/api/sessions")

    assert resp.status_code == 200
    assert resp.json()["meta"]["sources_degraded"] is None


async def test_aggregate_names_down_pool_so_failed_zero_is_not_all_clear(db_manager) -> None:
    """A down pool is named even when the reachable pool reports failed_count=0."""
    await _seed_session(db_manager, _REACHABLE, success=True)
    await db_manager.pool(_DOWN).close()

    app = _app(db_manager)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get("/api/sessions/aggregate?status=failed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["failed_count"] == 0
    assert body["meta"]["sources_degraded"] == [_DOWN]


async def test_detail_503_when_pool_down_and_not_found(db_manager) -> None:
    """Unknown id + a down pool = 503 naming the pool, not a false 404."""
    await db_manager.pool(_DOWN).close()

    app = _app(db_manager)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get(f"/api/sessions/{uuid4()}")

    assert resp.status_code == 503
    assert _DOWN in resp.json()["detail"]


async def test_detail_404_when_all_pools_healthy_and_unknown(db_manager) -> None:
    """Unknown id with every pool reachable is a genuine 404."""
    app = _app(db_manager)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get(f"/api/sessions/{uuid4()}")

    assert resp.status_code == 404


async def test_detail_found_in_reachable_pool_despite_sibling_down(db_manager) -> None:
    """A session in a reachable pool resolves 200 even while a sibling pool is down."""
    session_id = await _seed_session(db_manager, _REACHABLE, success=True)
    await db_manager.pool(_DOWN).close()

    app = _app(db_manager)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get(f"/api/sessions/{session_id}")

    assert resp.status_code == 200
    assert resp.json()["data"]["butler"] == _REACHABLE
