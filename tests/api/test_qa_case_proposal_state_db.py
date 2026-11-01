"""Real-Postgres contract for the dossier ``proposal_state`` (bu-mc01y5.18).

The case API may report ``unpublished`` only when the publication ledger proves
no PR can exist; a retained diff with no local PR metadata is not that proof
(a lost ``pr_create`` response leaves none while the PR may be live).  The
unit suite mocks the projected ``pr_absence_proven`` column, so this module
drives the real ledger transitions and the real dossier SELECT together.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.core.qa.publication.binding import OperationKind, PublicationBinding, request_digest
from butlers.core.qa.publication.state import (
    Observation,
    Outcome,
    begin_operation,
    mark_egress_started,
    record_outcome,
    record_reconciliation,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_BINDING = PublicationBinding(
    repository="acme/butlers", branch="qa/fix-abc", base_ref="main", head_sha="b" * 40
)
_DIFF = [{"kind": "+", "text": "fixed = True"}]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


async def _set_qa_role(connection: asyncpg.Connection) -> None:
    await connection.execute("SET ROLE butler_qa_rw")


@pytest.fixture
async def pools(migrated_db_url: str):  # type: ignore[no-untyped-def]
    """An admin pool for the API and seeding, and the only ledger writer role."""
    admin = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    qa = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3, setup=_set_qa_role)
    try:
        yield admin, qa
    finally:
        await qa.close()
        await admin.close()


async def _attempt_with_retained_diff(pool: asyncpg.Pool) -> uuid.UUID:
    """An attempt with a retained proposal diff and no local PR metadata."""
    fingerprint = uuid.uuid4().hex * 2
    attempt_id = await pool.fetchval(
        "INSERT INTO public.healing_attempts "
        "(fingerprint, butler_name, status, severity, exception_type, call_site) "
        "VALUES ($1, 'qa', 'investigating', 2, 'E', 'x:y') RETURNING id",
        fingerprint,
    )
    patrol_id = await pool.fetchval("INSERT INTO public.qa_patrols DEFAULT VALUES RETURNING id")
    now = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO public.qa_findings (
            patrol_id, fingerprint, source_type, source_butler, severity, exception_type,
            event_summary, call_site, occurrence_count, first_seen, last_seen,
            healing_attempt_id, structured_evidence
        )
        VALUES ($1, $2, 'log_scanner', 'qa', 2, 'E', 'failed', 'x:y', 1, $3, $3, $4, $5::jsonb)
        """,
        patrol_id,
        fingerprint,
        now,
        attempt_id,
        json.dumps({"investigation_notes": {"diff_snapshot": _DIFF}}),
    )
    return attempt_id


async def _record(
    pool: asyncpg.Pool,
    attempt_id: uuid.UUID,
    kind: OperationKind,
    outcome: Outcome | None,
    reconciliation: Observation | None = None,
) -> None:
    """Drive one ledger operation: ``outcome=None`` leaves it at ``egress_started``."""
    operation = await begin_operation(
        pool,
        attempt_id=attempt_id,
        kind=kind,
        binding=_BINDING,
        request_digest=request_digest({"kind": kind.value}),
    )
    assert await mark_egress_started(pool, operation.id)
    if outcome is not None:
        assert await record_outcome(pool, operation.id, outcome)
    if reconciliation is not None:
        assert await record_reconciliation(pool, operation.id, reconciliation)


@pytest.mark.parametrize(
    ("ledger", "expected"),
    [
        ([], "unpublished"),
        ([(OperationKind.BRANCH_PUSH, "succeeded", None)], "unpublished"),
        ([(OperationKind.PR_CREATE, "failed", None)], "unpublished"),
        ([(OperationKind.PR_CREATE, None, "resource_absent")], "unpublished"),
        # A lost pr_create response: the PR may exist.
        ([(OperationKind.PR_CREATE, None, None)], "ambiguous"),
        ([(OperationKind.PR_CREATE, "ambiguous", None)], "ambiguous"),
        ([(OperationKind.BRANCH_PUSH, "ambiguous", None)], "ambiguous"),
        # Created without local PR metadata: never claim it unpublished.
        ([(OperationKind.PR_CREATE, "succeeded", None)], "ambiguous"),
    ],
)
async def test_unpublished_requires_ledger_proof_of_no_pr(
    pools: tuple[asyncpg.Pool, asyncpg.Pool],
    ledger: list[tuple[OperationKind, Outcome | None, Observation | None]],
    expected: str,
) -> None:
    admin, qa = pools
    attempt_id = await _attempt_with_retained_diff(admin)
    for kind, outcome, reconciliation in ledger:
        await _record(qa, attempt_id, kind, outcome, reconciliation)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = admin
    app: FastAPI = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["pr"] is None
    assert data["proposal_diff_snapshot"] == _DIFF
    assert data["proposal_state"] == expected
