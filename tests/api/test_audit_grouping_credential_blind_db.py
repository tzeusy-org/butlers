"""DB-level regression: credential-target error groups carry no provider text (bu-uqipv).

``grouped_errors`` groups ``public.audit_log`` error rows by ``error_summary``,
and ``error_summary`` *is* the group's identity — the string the Issues feed
publishes as ``Issue.error_message``, folds into its ``description``, hashes
into its ``issue_key``, and binds as ``$1`` when the occurrences drill-down
re-derives the same group. For a ``u:``/``s:``/``c:`` row that string was the
provider's failure text: ``_write_credential_audit`` passes the raw probe
message through ``credential_lifecycle_outcome``, which stores it in ``error``.

bu-ove06 stopped ``AuditLogEntry`` publishing that column. The group title built
from it is one surface further out, and it cannot simply be blanked: a constant
would collapse every credential failure in the fleet into one indistinguishable
group. The rule under test instead replaces the summary — for credential
targets only — with a synthetic title composed from columns the wire already
carries (``action`` and ``target``).

These assertions are properties of the SQL, not of Python: the unit tests in
``test_audit_grouping.py`` pin the query's *shape* but never execute it, so only
a real Postgres proves the ``CASE`` predicate matches the target spellings that
are actually written (the ``target`` column is never normalised on write, so
``user:``/``system:``/``cli:`` rows exist alongside ``u:``/``s:``/``c:``).
"""

from __future__ import annotations

import json
import shutil
import uuid
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.audit_grouping import (
    build_audit_group_occurrences_query,
    build_audit_group_query,
    issue_from_audit_group_row,
)
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_butler_configs, get_mcp_manager
from butlers.api.routers import issues as issues_module
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

BASE_URL = "http://test"
ISSUES_PATH = "/api/issues?window=all"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.audit_log CASCADE")
    yield p
    await p.close()


@pytest.fixture
def issues_app(pool: asyncpg.Pool) -> FastAPI:
    """Mount the real Issues router over the migrated DB with no butlers to probe.

    ``get_butler_configs`` is overridden to an empty roster so the live
    reachability lane contributes nothing: this test is about the audit lane,
    and an empty roster keeps the request off the network entirely.
    ``get_mcp_manager`` is still resolved as a dependency even with no butlers
    to probe, and raises unless ``init_dependencies()`` has run, so it is
    overridden too.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    application = create_app()
    application.dependency_overrides[issues_module._get_db_manager] = lambda: mock_db
    application.dependency_overrides[get_butler_configs] = lambda: []
    application.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    return application


async def _insert_failure(
    pool: asyncpg.Pool,
    *,
    target: str | None,
    error: str,
    note: str | None = None,
    action: str = "failed",
    actor: str = "owner",
) -> int:
    """Write one ``result='error'`` row exactly as a credential producer does."""
    return await pool.fetchval(
        """
        INSERT INTO public.audit_log (actor, action, target, note, result, error)
        VALUES ($1, $2, $3, $4, 'error', $5)
        RETURNING id
        """,
        actor,
        action,
        target,
        note,
        error,
    )


async def _summaries(pool: asyncpg.Pool) -> list[str]:
    rows = await pool.fetch(build_audit_group_query())
    return [str(r["error_summary"]) for r in rows]


# ---------------------------------------------------------------------------
# The provider text never becomes a group identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["u:google", "user:google", "s:BUTLER_TELEGRAM_TOKEN", "system:X", "c:claude", "cli:claude"],
)
async def test_no_credential_spelling_publishes_its_provider_text(
    pool: asyncpg.Pool, target: str
) -> None:
    """Every scope spelling that can appear in the column is covered.

    ``target`` is never normalised on write, so the long spellings are live
    data, not hypotheticals — a predicate that only matched ``u:``/``s:``/``c:``
    would leave three namespaces publishing verbatim.
    """
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    await _insert_failure(
        pool,
        target=target,
        error=sentinel,
        note=f"Probe failed: {sentinel}; probe_status=live_failed:401",
    )

    summaries = await _summaries(pool)
    assert summaries, "the credential failure vanished from the feed entirely"
    assert all(sentinel not in s for s in summaries), (
        f"provider text survived as a group title for target={target}"
    )
    assert any(target in s for s in summaries), (
        f"the group no longer names the credential it belongs to: {summaries}"
    )


async def test_two_credentials_remain_two_distinguishable_groups(pool: asyncpg.Pool) -> None:
    """The fix must not collapse the credential namespace into one group.

    Blanking the summary would have made every credential failure in the fleet
    share one identity — one row in the feed, one occurrence count, and one
    acknowledgement covering unrelated broken credentials.
    """
    await _insert_failure(pool, target="u:google", error=f"a-{uuid.uuid4().hex}")
    await _insert_failure(pool, target="u:notion", error=f"b-{uuid.uuid4().hex}")

    summaries = await _summaries(pool)
    assert len(set(summaries)) == 2, f"credential groups collapsed together: {summaries}"

    issues = [issue_from_audit_group_row(r) for r in await pool.fetch(build_audit_group_query())]
    assert len({i.issue_key for i in issues}) == 2, (
        "two credentials share one issue_key; acking one would ack the other"
    )


async def test_differing_provider_text_on_one_credential_is_one_group(
    pool: asyncpg.Pool,
) -> None:
    """Identity is the credential, not the message.

    The accepted cost of a content-blind identity: two causes on the same
    credential fold into one group with a truthful occurrence count. The
    per-occurrence detail is still in ``public.audit_log`` and
    ``public.secret_probe_log`` — withheld from the wire, not destroyed.
    """
    await _insert_failure(pool, target="u:google", error=f"401-{uuid.uuid4().hex}")
    await _insert_failure(pool, target="u:google", error=f"429-{uuid.uuid4().hex}")

    rows = await pool.fetch(build_audit_group_query())
    assert len(rows) == 1, f"one credential produced {len(rows)} groups"
    assert int(rows[0]["occurrences"]) == 2


async def test_non_credential_rows_keep_their_error_summary_verbatim(
    pool: asyncpg.Pool,
) -> None:
    """A namespace carve-out, not a blanket gag on operator diagnostics."""
    await _insert_failure(pool, target="butler:qa", error="DB connection timeout")
    await _insert_failure(pool, target=None, actor="health", action="session", error="Boom\ndetail")

    summaries = await _summaries(pool)
    assert "DB connection timeout" in summaries
    assert "Boom" in summaries, f"first-line normalization changed: {summaries}"


async def test_drill_down_resolves_the_credential_group_it_published(
    pool: asyncpg.Pool,
) -> None:
    """The occurrences query binds the published summary as ``$1``.

    If the credential branch existed in the feed's CTE but not the drill-down's
    (or vice versa) this would return zero rows for a group the feed had just
    shown — the exact disagreement the shared CTE exists to prevent.
    """
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    await _insert_failure(pool, target="u:google", error=sentinel)

    group = (await pool.fetch(build_audit_group_query()))[0]
    issue = issue_from_audit_group_row(group)
    rows = await pool.fetch(
        build_audit_group_occurrences_query(), issue.error_message, issue.butlers, 50, 0
    )
    assert len(rows) == 1, "the drill-down lost its own group"
    # The row still carries the stored error at the DB; AuditLogEntry is what
    # withholds it on the wire (bu-ove06), and that is asserted there.
    assert rows[0]["error"] == sentinel


# ---------------------------------------------------------------------------
# Absence sentinel over the endpoint itself
# ---------------------------------------------------------------------------


async def test_issues_response_body_contains_no_provider_text(
    pool: asyncpg.Pool, issues_app: FastAPI
) -> None:
    """The acceptance criterion, asserted on the serialized response.

    The sentinel is generated per run and only ever asserted absent, so this
    test never itself becomes a place a failure string is written down.
    """
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    await _insert_failure(
        pool,
        target="u:google",
        error=sentinel,
        note=f"Probe failed: {sentinel}; probe_status=live_failed:401",
    )

    transport = httpx.ASGITransport(app=issues_app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        resp = await client.get(ISSUES_PATH)

    assert resp.status_code == 200
    body = json.dumps(resp.json())
    assert sentinel not in body, "the provider failure string reached GET /api/issues"
    assert "u:google" in body, "the credential failure is missing from the feed entirely"


async def test_occurrences_response_body_contains_no_provider_text(
    pool: asyncpg.Pool, issues_app: FastAPI
) -> None:
    """The drill-down one door past the feed carries no provider text either.

    ``GET /api/issues/{issue_key}/occurrences`` re-derives the group from the
    same CTE and then serializes the raw rows behind it. bu-ove06 withholds the
    row's ``error``; this pins that the group's own *title* — which the feed
    just handed the client as ``issue_key`` — did not smuggle it back in.
    """
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    await _insert_failure(
        pool,
        target="u:google",
        error=sentinel,
        note=f"Probe failed: {sentinel}; probe_status=live_failed:401",
    )

    group = (await pool.fetch(build_audit_group_query()))[0]
    issue_key = issue_from_audit_group_row(group).issue_key

    transport = httpx.ASGITransport(app=issues_app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        resp = await client.get(f"/api/issues/{issue_key}/occurrences?window=all")

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["meta"]["total"] == 1, "the drill-down lost the group the feed showed"
    assert sentinel not in json.dumps(payload), (
        "the provider failure string reached the occurrences drill-down"
    )
