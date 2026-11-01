"""Retired Retry has no effects; actual QA admission retains publication holds.

Real migrated PostgreSQL independently reads original rows and authoritative
create_or_join_attempt outcomes across held/pre-egress/reconciled lineage.
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db, get_mcp_manager
from butlers.core.healing.tracking import PublicationLineageHeld, create_or_join_attempt
from butlers.core.qa.publication.binding import OperationKind, PublicationBinding, request_digest
from butlers.core.qa.publication.state import (
    begin_operation,
    mark_egress_started,
    record_outcome,
    record_reconciliation,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SHA = "b" * 40
_PAYLOAD = request_digest({"title": "Fix", "labels": ["qa"]})
_PR = "pr-7"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


async def _set_qa_role(connection: asyncpg.Connection) -> None:
    await connection.execute("SET ROLE butler_qa_rw")


@pytest.fixture
async def api_pool(migrated_db_url: str):
    pool = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def qa_pool(migrated_db_url: str):
    pool = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=4, setup=_set_qa_role)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def failed_attempt(api_pool) -> tuple[uuid.UUID, str]:
    attempt_id = uuid.uuid4()
    fingerprint = uuid.uuid4().hex * 2
    await api_pool.execute(
        "INSERT INTO public.healing_attempts "
        "(id, fingerprint, butler_name, status, severity, exception_type, call_site, closed_at) "
        "VALUES ($1, $2, 'general', 'failed', 2, 'E', 'x:y', now())",
        attempt_id,
        fingerprint,
    )
    return attempt_id, fingerprint


class _Client:
    """The retry route over the real pool, with spies for every dispatch path."""

    def __init__(self, pool) -> None:
        self.dispatch = AsyncMock()
        self.mcp = MagicMock()
        self.mcp.butler_names = ["general"]
        self.mcp.get_client = AsyncMock(side_effect=RuntimeError("no daemon"))
        self.mcp.invalidate_client = AsyncMock()
        db = MagicMock(spec=DatabaseManager)
        db.credential_shared_pool.return_value = pool
        self.app = create_app()
        self.app.dependency_overrides[get_db] = lambda: db
        self.app.dependency_overrides[get_mcp_manager] = lambda: self.mcp

    async def retry(self, attempt_id: uuid.UUID) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            return await client.post(f"/api/healing/attempts/{attempt_id}/retry")


async def _attempt_count(pool, fingerprint: str) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM public.healing_attempts WHERE fingerprint = $1", fingerprint
    )


async def _bound_operation(qa_pool, attempt_id: uuid.UUID):
    return await begin_operation(
        qa_pool,
        attempt_id=attempt_id,
        kind=OperationKind.PR_CREATE,
        binding=PublicationBinding(
            repository="acme/butlers",
            branch="qa/fix-abc",
            base_ref="main",
            head_sha=_SHA,
            pr_number=7,
        ),
        request_digest=_PAYLOAD,
        expected_object_id=_PR,
    )


@pytest.mark.parametrize("stage", ["egress_started", "ambiguous"])
async def test_retry_of_a_held_fingerprint_is_refused_with_no_insert_or_dispatch(
    api_pool, qa_pool, failed_attempt, stage: str
) -> None:
    attempt_id, fingerprint = failed_attempt
    operation = await _bound_operation(qa_pool, attempt_id)
    assert await mark_egress_started(qa_pool, operation.id)
    if stage == "ambiguous":
        assert await record_outcome(qa_pool, operation.id, "ambiguous")
    client = _Client(api_pool)

    response = await client.retry(attempt_id)

    assert response.status_code == 404
    with pytest.raises(PublicationLineageHeld):
        await create_or_join_attempt(
            qa_pool,
            fingerprint,
            "general",
            2,
            "E",
            "x:y",
            uuid.uuid4(),
            enforce_publication_hold=True,
        )
    assert await _attempt_count(api_pool, fingerprint) == 1
    client.dispatch.assert_not_awaited()
    client.mcp.get_client.assert_not_awaited()


async def test_retry_without_a_hold_proceeds_as_before(api_pool, qa_pool, failed_attempt) -> None:
    attempt_id, fingerprint = failed_attempt
    client = _Client(api_pool)

    response = await client.retry(attempt_id)

    assert response.status_code == 404
    assert await _attempt_count(api_pool, fingerprint) == 1
    _, created = await create_or_join_attempt(
        qa_pool,
        fingerprint,
        "general",
        2,
        "E",
        "x:y",
        uuid.uuid4(),
        enforce_publication_hold=True,
    )
    assert created is True
    assert await _attempt_count(api_pool, fingerprint) == 2
    client.dispatch.assert_not_awaited()


async def test_a_bound_operation_that_never_started_egress_does_not_hold(
    api_pool, qa_pool, failed_attempt
) -> None:
    attempt_id, fingerprint = failed_attempt
    await _bound_operation(qa_pool, attempt_id)
    client = _Client(api_pool)

    response = await client.retry(attempt_id)

    assert response.status_code == 404
    assert await _attempt_count(api_pool, fingerprint) == 1
    _, created = await create_or_join_attempt(
        qa_pool,
        fingerprint,
        "general",
        2,
        "E",
        "x:y",
        uuid.uuid4(),
        enforce_publication_hold=True,
    )
    assert created is True
    assert await _attempt_count(api_pool, fingerprint) == 2


async def test_retry_proceeds_once_the_hold_is_reconciled(
    api_pool, qa_pool, failed_attempt
) -> None:
    attempt_id, fingerprint = failed_attempt
    operation = await _bound_operation(qa_pool, attempt_id)
    assert await mark_egress_started(qa_pool, operation.id)
    client = _Client(api_pool)
    assert (await client.retry(attempt_id)).status_code == 404
    with pytest.raises(PublicationLineageHeld):
        await create_or_join_attempt(
            qa_pool,
            fingerprint,
            "general",
            2,
            "E",
            "x:y",
            uuid.uuid4(),
            enforce_publication_hold=True,
        )

    assert await record_reconciliation(
        qa_pool, operation.id, "resource_found", observed_object_id=_PR
    )
    response = await client.retry(attempt_id)

    assert response.status_code == 404
    _, created = await create_or_join_attempt(
        qa_pool,
        fingerprint,
        "general",
        2,
        "E",
        "x:y",
        uuid.uuid4(),
        enforce_publication_hold=True,
    )
    assert created is True
    assert await _attempt_count(api_pool, fingerprint) == 2
    client.dispatch.assert_not_awaited()
