"""DB-level regression for the QA case dossier session doors (bu-533qx.3).

The unit tests in ``test_api_qa_cases.py`` mock the pool, so they never prove
the dossier SELECT actually resolves ``a.healing_session_id`` / ``a.session_ids``
against the real ``public.healing_attempts`` schema — mocked-green SELECT
projections have broken main before. This module runs the *actual*
``GET /api/qa/cases/:id`` endpoint against a core-migrated Postgres and asserts:

  1. A case whose attempt row carries a ``healing_session_id`` and a
     non-empty ``session_ids`` array surfaces both in the dossier payload —
     the trace-spine doors to ``/sessions/:id``.
  2. A case with a NULL ``healing_session_id`` and an empty ``session_ids``
     renders no door (null + empty), never a broken link.
"""

from __future__ import annotations

import shutil
import uuid
from typing import Any
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.qa import _get_db_manager
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
    """Provision the core chain (public.healing_attempts + qa_findings)."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):  # type: ignore[no-untyped-def]
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    await p.execute("TRUNCATE TABLE public.healing_attempts CASCADE")
    yield p
    await p.close()


@pytest.fixture
def doors_app(pool: asyncpg.Pool) -> FastAPI:
    """Wire the QA router's shared credential pool to the migrated DB."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    application = create_app()
    application.dependency_overrides[_get_db_manager] = lambda: mock_db
    return application


async def _insert_attempt(
    pool: asyncpg.Pool,
    *,
    healing_session_id: uuid.UUID | None,
    session_ids: list[uuid.UUID],
) -> uuid.UUID:
    return await pool.fetchval(
        """
        INSERT INTO public.healing_attempts
            (fingerprint, butler_name, severity, exception_type, call_site,
             status, healing_session_id, session_ids)
        VALUES ($1, $2, $3, $4, $5, 'investigating', $6, $7)
        RETURNING id
        """,
        uuid.uuid4().hex * 2,
        "finance",
        1,
        "RuntimeError",
        "finance.jobs:run",
        healing_session_id,
        session_ids,
    )


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return await client.get(path)


async def test_case_dossier_projects_session_doors_from_real_schema(
    doors_app: FastAPI, pool: asyncpg.Pool
) -> None:
    healing_session_id = uuid.uuid4()
    session_ids = [uuid.uuid4(), uuid.uuid4()]
    attempt_id = await _insert_attempt(
        pool, healing_session_id=healing_session_id, session_ids=session_ids
    )

    response = await _get(doors_app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    assert data["healing_session_id"] == str(healing_session_id)
    assert data["session_ids"] == [str(s) for s in session_ids]


async def test_case_dossier_session_doors_null_safe_from_real_schema(
    doors_app: FastAPI, pool: asyncpg.Pool
) -> None:
    attempt_id = await _insert_attempt(pool, healing_session_id=None, session_ids=[])

    response = await _get(doors_app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    assert data["healing_session_id"] is None
    assert data["session_ids"] == []
