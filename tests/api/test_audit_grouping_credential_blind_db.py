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
targets only — with a synthetic title composed from columns that cannot carry a
provider's words: ``action`` and ``target``, both already on the wire, plus
``failure_category`` (bu-vhie6), which core_202 CHECK-constrains at rest to
``PROBE_FAILURE_VOCABULARY``. The category is what makes the group identity the
credential *and its cause*, so a 401 and a 429 on one credential no longer share
one occurrence count and one acknowledgement.

These assertions are properties of the SQL, not of Python: the unit tests in
``test_audit_grouping.py`` pin the query's *shape* but never execute it, so only
a real Postgres proves the ``CASE`` predicate matches the target spellings that
are actually written (the ``target`` column is never normalised on write, so
``user:``/``system:``/``cli:`` rows exist alongside ``u:``/``s:``/``c:``).
"""

from __future__ import annotations

import json
import re
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
from butlers.api.models.audit import PROBE_FAILURE_VOCABULARY
from butlers.api.routers import audit as audit_router
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
    failure_category: str | None = None,
) -> int:
    """Write one ``result='error'`` row exactly as a credential producer does.

    ``failure_category`` defaults to ``None`` so the existing assertions keep
    describing a pre-core_202 row, which is also what every historic row in a
    real database looks like.
    """
    return await pool.fetchval(
        """
        INSERT INTO public.audit_log
            (actor, action, target, note, result, error, failure_category)
        VALUES ($1, $2, $3, $4, 'error', $5, $6)
        RETURNING id
        """,
        actor,
        action,
        target,
        note,
        error,
        failure_category,
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


async def test_differing_provider_text_on_one_uncategorised_credential_is_one_group(
    pool: asyncpg.Pool,
) -> None:
    """Uncategorised rows still group by credential alone, and must keep doing so.

    This was the whole story before bu-vhie6 and remains the story for rows
    written before core_202, which are deliberately not backfilled. Two such
    rows fold into one group with a truthful occurrence count regardless of how
    differently the provider worded them: the message is never read.

    Two distinct *causes* now separate — see
    ``test_distinct_causes_on_one_credential_are_distinct_groups`` — but only
    when the cause was persisted at write time.
    """
    await _insert_failure(pool, target="u:google", error=f"401-{uuid.uuid4().hex}")
    await _insert_failure(pool, target="u:google", error=f"429-{uuid.uuid4().hex}")

    rows = await pool.fetch(build_audit_group_query())
    assert len(rows) == 1, f"one credential produced {len(rows)} groups"
    assert int(rows[0]["occurrences"]) == 2


# ---------------------------------------------------------------------------
# The persisted cause splits the group, and only the persisted cause (bu-vhie6)
# ---------------------------------------------------------------------------


async def test_distinct_causes_on_one_credential_are_distinct_groups(
    pool: asyncpg.Pool,
) -> None:
    """AC2, executed: one credential, two causes, two groups.

    Before core_202 these two rows were one group, so acknowledging "Google is
    rate-limited" also silently acknowledged "Google rejected the credential" —
    an operator dismissing a transient throttle would have dismissed a genuinely
    dead credential with it. The provider text still differs between the rows
    and is still never read; the split comes entirely from the persisted
    vocabulary member.
    """
    await _insert_failure(
        pool,
        target="u:google",
        error=f"synthetic-withheld-{uuid.uuid4().hex}",
        failure_category="rejected",
    )
    await _insert_failure(
        pool,
        target="u:google",
        error=f"synthetic-withheld-{uuid.uuid4().hex}",
        failure_category="rate_limited",
    )

    rows = await pool.fetch(build_audit_group_query())
    assert len(rows) == 2, (
        f"two causes on one credential produced {len(rows)} group(s); the cause "
        "is not part of the identity"
    )
    issues = [issue_from_audit_group_row(r) for r in rows]
    assert len({i.issue_key for i in issues}) == 2, (
        "the two causes hash to one issue_key; acking one would ack the other"
    )


async def test_same_cause_on_one_credential_stays_one_group(pool: asyncpg.Pool) -> None:
    """The split is by cause, not by row: repeats must still aggregate.

    Without this the previous test passes just as well against an identity that
    fragments per occurrence, which would replace one over-broad group with a
    feed of singletons — the opposite failure, equally useless.
    """
    for _ in range(3):
        await _insert_failure(
            pool,
            target="u:google",
            error=f"synthetic-withheld-{uuid.uuid4().hex}",
            failure_category="rejected",
        )

    rows = await pool.fetch(build_audit_group_query())
    assert len(rows) == 1, f"three occurrences of one cause produced {len(rows)} groups"
    assert int(rows[0]["occurrences"]) == 3


async def test_the_category_reaches_the_group_title(pool: asyncpg.Pool) -> None:
    """The published title says which cause, so the split is legible to a human.

    Two groups that differ only in an invisible way would be a worse feed than
    one: the operator sees two identical rows and cannot tell which to act on.
    """
    await _insert_failure(
        pool,
        target="u:google",
        error=f"synthetic-withheld-{uuid.uuid4().hex}",
        failure_category="rate_limited",
    )

    summaries = await _summaries(pool)
    assert summaries == ["Credential failed: u:google [rate_limited] (diagnostic withheld)"], (
        f"unexpected categorised group title: {summaries}"
    )


async def test_uncategorised_rows_keep_the_byte_identical_pre_change_title(
    pool: asyncpg.Pool,
) -> None:
    """The stated decision for historic rows, asserted as an exact string.

    Rows written before core_202 have ``failure_category IS NULL`` and are not
    backfilled (backfilling would mean parsing the withheld ``note``, the exact
    inversion this change exists to prevent). Their group title must therefore
    be byte-identical to the pre-change one, because the title *is* the group
    identity and ``issue_key`` is its hash: any drift silently orphans every
    existing acknowledgement.
    """
    await _insert_failure(pool, target="u:google", error=f"synthetic-withheld-{uuid.uuid4().hex}")

    summaries = await _summaries(pool)
    assert summaries == ["Credential failed: u:google (diagnostic withheld)"], (
        f"a historic uncategorised row no longer groups as it did: {summaries}"
    )


async def test_a_categorised_row_does_not_join_its_credential_s_legacy_group(
    pool: asyncpg.Pool,
) -> None:
    """The transition, made explicit rather than left to be discovered.

    A credential that was already failing before core_202 and keeps failing
    after it shows *two* groups for a while: the frozen legacy one and the new
    categorised one. That is the accepted price of not backfilling — the legacy
    group stops growing and ages out of the window. Pinning it here means the
    behaviour is a decision on record, not a surprise in someone's feed.
    """
    await _insert_failure(pool, target="u:google", error=f"synthetic-withheld-{uuid.uuid4().hex}")
    await _insert_failure(
        pool,
        target="u:google",
        error=f"synthetic-withheld-{uuid.uuid4().hex}",
        failure_category="rejected",
    )

    summaries = sorted(await _summaries(pool))
    assert summaries == [
        "Credential failed: u:google (diagnostic withheld)",
        "Credential failed: u:google [rejected] (diagnostic withheld)",
    ], f"unexpected transitional grouping: {summaries}"


# ---------------------------------------------------------------------------
# Nothing outside the vocabulary can be stored at all
# ---------------------------------------------------------------------------


async def test_the_check_constraint_allows_exactly_the_live_vocabulary(
    pool: asyncpg.Pool,
) -> None:
    """core_202 inlines the vocabulary; this is what keeps the copy honest.

    A migration is a frozen snapshot and cannot import the tuple, so widening
    ``PROBE_FAILURE_VOCABULARY`` without a follow-up migration would leave the
    database rejecting a member the application happily clamps *to* — an INSERT
    that fails in production and nowhere else. Failing here instead makes the
    missing migration the thing that blocks the merge.
    """
    clause = await pool.fetchval(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conname = 'audit_log_failure_category_vocabulary'
          AND conrelid = 'public.audit_log'::regclass
        """
    )
    assert clause, "the failure_category vocabulary CHECK constraint is missing"
    allowed = set(re.findall(r"'([a-z_]+)'::text", clause))
    assert allowed == set(PROBE_FAILURE_VOCABULARY), (
        "the CHECK constraint and PROBE_FAILURE_VOCABULARY disagree; a migration "
        f"is missing. constraint={sorted(allowed)}"
    )


async def test_the_database_refuses_a_raw_probe_token(pool: asyncpg.Pool) -> None:
    """The structural half of owner Option C, not just the application clamp.

    ``clamp_failure_category`` collapses a stray value to ``other`` on the way
    through ``audit.append()``. This asserts the guarantee survives a writer
    that bypasses that function entirely — a migration, a psql session, a future
    job with its own INSERT — because a raw ``probe_status`` token in this
    column would put provider-derived text straight into a group title.
    """
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_failure(
            pool,
            target="u:google",
            error="synthetic-withheld",
            failure_category="live_failed:403",
        )


async def test_append_clamps_a_non_member_instead_of_dropping_the_row(
    pool: asyncpg.Pool,
) -> None:
    """A mislabelled category must cost the label, never the audit row.

    Audit writes are fire-and-forget; letting the CHECK constraint raise would
    mean a producer bug silently deletes the record of a credential failure.
    ``audit.append`` clamps to ``other`` first, so the row survives and lands in
    an honest bucket.
    """
    row_id = await audit_router.append(
        pool,
        "owner",
        "failed",
        target="u:google",
        error="synthetic-withheld",
        result="error",
        failure_category="live_failed:403",
    )
    stored = await pool.fetchval(
        "SELECT failure_category FROM public.audit_log WHERE id = $1", row_id
    )
    assert stored == "other", f"a non-vocabulary category was stored as {stored!r}"


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
    test never itself becomes a place a failure string is written down. Both
    title shapes are exercised: the uncategorised row that a pre-core_202
    producer wrote, and the categorised row bu-vhie6 introduced, because the
    second builds a *different* string and an absence proved only against the
    first would say nothing about it.
    """
    sentinel = f"synthetic-withheld-{uuid.uuid4().hex}"
    for category in (None, "rejected"):
        await _insert_failure(
            pool,
            target="u:google",
            error=sentinel,
            note=f"Probe failed: {sentinel}; probe_status=live_failed:401",
            failure_category=category,
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
