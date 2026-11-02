"""Real-Postgres contract for the dossier ``proposal_state`` (bu-mc01y5.18).

The case API may report ``unpublished`` only when the publication ledger proves
no PR can exist; a retained diff with no local PR metadata is not that proof
(a lost ``pr_create`` response leaves none while the PR may be live).  The
unit suite mocks the projected ``pr_absence_proven`` column, so this module
drives the real ledger transitions and the real dossier SELECT together.

An empty ledger is that proof only for an attempt the publisher handled: it
retained the sealed export before its first egress (bu-mc01y5.24), or receipted
a refusal made before retention (bu-mc01y5.28).  An attempt from before the
ledger cutover has no ledger rows, export or receipt.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.core.qa.publication.binding import OperationKind, PublicationBinding, request_digest
from butlers.core.qa.publication.publisher import PoolLedger, PublicationStatus, QaPublisher
from butlers.core.qa.publication.state import (
    Observation,
    Outcome,
    begin_operation,
    mark_egress_started,
    record_outcome,
    record_reconciliation,
)
from butlers.core.qa.publication.transport import ProviderRejected
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app as create_app
from tests.core.qa.publication_fakes import FakeProvider, binding_for, synthetic_export

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


async def _retain_export(pool: asyncpg.Pool, attempt_id: uuid.UUID) -> None:
    """Retain a sealed export row, as the publisher does before its first egress."""
    head = "b" * 40
    await pool.execute(
        "INSERT INTO public.qa_publication_exports "
        "(attempt_id, base_sha, head_sha, commits, objects, digest) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        attempt_id,
        "a" * 40,
        head,
        [head],
        b"",
        "c" * 64,
    )


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
        # Settled at once: this table only cares about the reconciled outcome.
        assert await record_reconciliation(
            pool, operation.id, reconciliation, absence_settle=timedelta(0)
        )


@pytest.mark.parametrize(
    ("ledger", "expected"),
    [
        (None, "ambiguous"),  # Pre-ledger attempt: no export retained, no ledger rows.
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
    ledger: list[tuple[OperationKind, Outcome | None, Observation | None]] | None,
    expected: str,
) -> None:
    admin, qa = pools
    attempt_id = await _attempt_with_retained_diff(admin)
    if ledger is not None:
        await _retain_export(qa, attempt_id)
        for kind, outcome, reconciliation in ledger:
            await _record(qa, attempt_id, kind, outcome, reconciliation)

    assert await _proposal_state(admin, attempt_id) == expected


async def test_pr_already_exists_rejection_never_reads_unpublished(
    pools: tuple[asyncpg.Pool, asyncpg.Pool],
) -> None:
    """GitHub's 422 "a pull request already exists" may front a live PR (bu-mc01y5.23)."""
    admin, qa = pools
    attempt_id = await _attempt_with_retained_diff(admin)
    export = synthetic_export()
    publisher = QaPublisher(
        provider=FakeProvider(raise_on_create=ProviderRejected("validation_failed")),
        ledger=PoolLedger(qa),
        repo_root=Path("/r"),
    )
    whitelist = MagicMock()
    whitelist.ensure_loaded = AsyncMock()
    whitelist.is_allowed = MagicMock(return_value=(True, "ok"))

    result = await publisher.publish(
        attempt_id=attempt_id,
        binding=binding_for(export),
        export=export,
        title="fix(qa): ValueError in module:1",
        body="## Root Cause\nA missing guard.",
        labels=["self-healing"],
        whitelist=whitelist,
    )

    assert result.status is PublicationStatus.AMBIGUOUS
    assert await _proposal_state(admin, attempt_id) == "ambiguous"


async def test_refusal_before_retention_reads_unpublished_through_its_receipt(
    pools: tuple[asyncpg.Pool, asyncpg.Pool],
) -> None:
    """A whitelist refusal retains no export; its receipt proves the publisher handled it
    (bu-mc01y5.28), and a repeated refusal leaves the first receipt standing."""
    admin, qa = pools
    attempt_id = await _attempt_with_retained_diff(admin)
    export = synthetic_export()
    provider = FakeProvider()
    publisher = QaPublisher(provider=provider, ledger=PoolLedger(qa), repo_root=Path("/r"))
    whitelist = MagicMock()
    whitelist.ensure_loaded = AsyncMock()
    whitelist.is_allowed = MagicMock(return_value=(False, "denied"))

    for refusing in (whitelist, None):
        result = await publisher.publish(
            attempt_id=attempt_id,
            binding=binding_for(export),
            export=export,
            title="fix(qa): ValueError in module:1",
            body="## Root Cause\nA missing guard.",
            labels=["self-healing"],
            whitelist=refusing,
        )
        assert (result.status, result.category) == (
            PublicationStatus.REFUSED,
            "repo_not_whitelisted",
        )

    receipts = await admin.fetch(
        "SELECT status, category FROM public.qa_publication_refusals WHERE attempt_id = $1",
        attempt_id,
    )
    assert [tuple(r) for r in receipts] == [("refused", "repo_not_whitelisted")]
    assert provider.calls == []
    assert await _proposal_state(admin, attempt_id) == "unpublished"


async def _proposal_state(admin: asyncpg.Pool, attempt_id: uuid.UUID) -> str:
    """Read the dossier through the real API and SELECT; the case has no PR metadata."""
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
    return data["proposal_state"]
