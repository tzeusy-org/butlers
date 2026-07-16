"""Real-Postgres regression: rule-promotion trigger job (bu-wuwy9, bead 3 of 7).

End-to-end coverage of ``run_rule_promotion_trigger`` against a fully
migrated Postgres instance (testcontainers), exercising the real
``IngestionPolicyEvaluator`` (not a fake) so the "already covered by an
enabled rule" check is verified against the actual schema-qualified query
(``switchboard.ingestion_rules``) rather than mocked out.

Complements the pure-function unit tests in
``roster/switchboard/tests/test_rule_promotion_trigger.py`` — this file
verifies the real SQL (INSERT ... ON CONFLICT, the partial unique index, the
message_inbox header join) actually executes correctly against Postgres,
which a mocked-pool unit test cannot catch.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.switchboard.routing.rule_promotion import run_rule_promotion_trigger

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision core + switchboard chains with switchboard tables in their
    real ``switchboard`` schema (matching production topology) — required
    for ``IngestionPolicyEvaluator``'s hardcoded ``switchboard.ingestion_rules``
    query to resolve correctly.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    """Mirrors the production Switchboard pool's search_path
    (``butlers.db.Database._server_settings`` -> ``schema_search_path
    ("switchboard")`` -> ``"switchboard,public"``) so this module's unqualified
    table references (``routing_verdict_log``, ``rule_promotion_suggestions``,
    ``message_inbox``, ``ingestion_rules``) resolve against the ``switchboard``
    schema, exactly as they do at runtime.
    """
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _insert_ingestion_event(
    pool: asyncpg.Pool, *, event_id: uuid.UUID, received_at: datetime, source_channel: str
) -> None:
    await pool.execute(
        """
        INSERT INTO public.ingestion_events
            (id, received_at, source_channel, source_provider, source_endpoint_identity,
             external_event_id, dedupe_key, dedupe_strategy, ingestion_tier, policy_tier)
        VALUES ($1, $2, $3, 'test-provider', 'test-endpoint', $4, $4, 'test', 'full', 'full')
        """,
        event_id,
        received_at,
        source_channel,
        str(event_id),
    )


async def _insert_message_inbox(
    pool: asyncpg.Pool,
    *,
    event_id: uuid.UUID,
    received_at: datetime,
    headers: dict[str, str] | None = None,
) -> None:
    # message_inbox is partitioned by received_at (monthly). The switchboard
    # migration only proactively creates the "current month" + "next month"
    # partitions at migration time — test evidence timestamps deliberately
    # spread across a few days (sometimes crossing into a prior month
    # relative to whenever this suite happens to run) need their own
    # partition ensured explicitly, exactly as the pipeline does at real
    # ingest time (see migration 001's ensure-partition call site).
    await pool.execute("SELECT switchboard_message_inbox_ensure_partition($1)", received_at)
    raw_payload = {"payload": {"raw": {"headers": headers or {}}}}
    await pool.execute(
        """
        INSERT INTO message_inbox (id, received_at, normalized_text, raw_payload)
        VALUES ($1, $2, 'test evidence message', $3)
        """,
        event_id,
        received_at,
        raw_payload,
    )


async def _insert_verdict(
    pool: asyncpg.Pool,
    *,
    ingestion_event_id: uuid.UUID,
    sender_key: str,
    source_channel: str,
    decided_at: datetime,
    verdict_action: str = "route_to",
    verdict_target: str | None = "finance",
    verdict_source: str = "llm",
) -> None:
    await pool.execute(
        """
        INSERT INTO routing_verdict_log
            (ingestion_event_id, sender_key, source_channel, verdict_source,
             verdict_action, verdict_target, decided_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        ingestion_event_id,
        sender_key,
        source_channel,
        verdict_source,
        verdict_action,
        verdict_target,
        decided_at,
    )


async def _seed_evidence(
    pool: asyncpg.Pool,
    *,
    sender_key: str,
    source_channel: str,
    timestamps: list[datetime],
    verdict_action: str = "route_to",
    verdict_target: str | None = "finance",
    headers: dict[str, str] | None = None,
) -> None:
    for ts in timestamps:
        event_id = uuid.uuid4()
        await _insert_ingestion_event(
            pool, event_id=event_id, received_at=ts, source_channel=source_channel
        )
        await _insert_message_inbox(pool, event_id=event_id, received_at=ts, headers=headers)
        await _insert_verdict(
            pool,
            ingestion_event_id=event_id,
            sender_key=sender_key,
            source_channel=source_channel,
            decided_at=ts,
            verdict_action=verdict_action,
            verdict_target=verdict_target,
        )


async def _insert_enabled_rule(
    pool: asyncpg.Pool,
    *,
    rule_type: str,
    condition: dict[str, object],
    action: str,
    priority: int,
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO ingestion_rules (id, scope, rule_type, condition, action, priority)
        VALUES ($1, 'global', $2, $3, $4, $5)
        """,
        rule_id,
        rule_type,
        condition,
        action,
        priority,
    )
    return rule_id


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# End-to-end scan -> suggestion write
# ---------------------------------------------------------------------------


async def test_eligible_pattern_creates_pending_suggestion(pool: asyncpg.Pool) -> None:
    """Scenario: 'Promotion-eligible pattern creates a suggestion'."""
    sender_key = "billing@acme-eligible.com"
    base = _now() - timedelta(days=3)
    timestamps = [base, base + timedelta(days=1), base + timedelta(days=2)]

    await _seed_evidence(
        pool,
        sender_key=sender_key,
        source_channel="email",
        timestamps=timestamps,
        verdict_action="route_to",
        verdict_target="finance",
    )

    result = await run_rule_promotion_trigger(pool)

    assert result["suggestions_created"] == 1

    row = await pool.fetchrow(
        "SELECT * FROM rule_promotion_suggestions WHERE sender_key = $1", sender_key
    )
    assert row is not None
    assert row["status"] == "pending_review"
    assert row["suggestion_kind"] == "promotion"
    assert row["proposed_rule_type"] == "sender_address"
    assert row["proposed_condition"] == {"address": sender_key}
    assert row["proposed_action"] == "route_to:finance"
    assert row["evidence_count"] == 3


async def test_single_burst_evidence_does_not_create_suggestion(pool: asyncpg.Pool) -> None:
    """Scenario: 'Single-burst evidence does not trigger promotion'."""
    sender_key = "notifications@burst-same-day.com"
    base = _now()
    timestamps = [base, base + timedelta(minutes=4), base + timedelta(minutes=9)]

    await _seed_evidence(
        pool,
        sender_key=sender_key,
        source_channel="email",
        timestamps=timestamps,
        verdict_action="route_to",
        verdict_target="general",
    )

    result = await run_rule_promotion_trigger(pool)

    assert result["suggestions_created"] == 0
    row = await pool.fetchrow(
        "SELECT 1 FROM rule_promotion_suggestions WHERE sender_key = $1", sender_key
    )
    assert row is None


async def test_midnight_boundary_burst_does_not_create_suggestion(pool: asyncpg.Pool) -> None:
    """Scenario: 'Midnight-boundary burst does not trigger promotion' — the
    normative spec scenario this bead's UTC+floor gate exists to satisfy,
    verified here against a real Postgres write path (not just the pure
    function unit-tested in roster/switchboard/tests/)."""
    sender_key = "ci-bot@midnight-burst.com"
    # Anchor at the next UTC midnight so this is deterministic regardless of
    # what time the test suite happens to run.
    now = _now()
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    timestamps = [
        next_midnight - timedelta(minutes=5),
        next_midnight - timedelta(minutes=1),
        next_midnight + timedelta(minutes=2),
    ]

    await _seed_evidence(
        pool,
        sender_key=sender_key,
        source_channel="email",
        timestamps=timestamps,
        verdict_action="route_to",
        verdict_target="general",
    )

    result = await run_rule_promotion_trigger(pool)

    assert result["suggestions_created"] == 0
    row = await pool.fetchrow(
        "SELECT 1 FROM rule_promotion_suggestions WHERE sender_key = $1", sender_key
    )
    assert row is None


async def test_existing_enabled_rule_suppresses_re_proposal(pool: asyncpg.Pool) -> None:
    """Scenario: 'Existing rule suppresses re-proposal' — exercised against
    the real IngestionPolicyEvaluator + switchboard.ingestion_rules table."""
    sender_key = "alerts@already-covered.com"
    await _insert_enabled_rule(
        pool,
        rule_type="sender_address",
        condition={"address": sender_key},
        action="skip",
        priority=10,
    )

    base = _now() - timedelta(days=3)
    timestamps = [base, base + timedelta(days=1), base + timedelta(days=2)]
    await _seed_evidence(
        pool,
        sender_key=sender_key,
        source_channel="email",
        timestamps=timestamps,
        verdict_action="route_to",
        verdict_target="finance",
    )

    result = await run_rule_promotion_trigger(pool)

    assert result["skipped_existing_rule"] == 1
    assert result["suggestions_created"] == 0
    row = await pool.fetchrow(
        "SELECT 1 FROM rule_promotion_suggestions WHERE sender_key = $1", sender_key
    )
    assert row is None


async def test_is_clearly_automated_flag_set_from_local_part_prefix(pool: asyncpg.Pool) -> None:
    """Scenario: 'Automated sender flagged' — via the local-part prefix
    convention rather than headers.

    Deliberately does NOT use List-Unsubscribe/Precedence/Auto-Submitted
    evidence headers here: migration 003 already seeds *global* enabled
    rules matching exactly those header signals (metadata_only/
    low_priority_queue/skip), so evidence carrying them would be correctly
    suppressed by the "already covered by an existing rule" check before
    ever reaching the classifier — that interaction is real production
    behavior, not a bug, but it means this scenario can only be exercised
    end-to-end via the header-independent local-part branch here. The
    header-based branch of the classifier itself is covered directly in
    ``roster/switchboard/tests/test_rule_promotion_trigger.py``.
    """
    sender_key = "notifications@vendor-digest.com"
    base = _now() - timedelta(days=3)
    timestamps = [base, base + timedelta(days=1), base + timedelta(days=2)]

    await _seed_evidence(
        pool,
        sender_key=sender_key,
        source_channel="email",
        timestamps=timestamps,
        verdict_action="route_to",
        verdict_target="general",
    )

    result = await run_rule_promotion_trigger(pool)
    assert result["suggestions_created"] == 1

    row = await pool.fetchrow(
        "SELECT is_clearly_automated, proposed_action FROM rule_promotion_suggestions "
        "WHERE sender_key = $1",
        sender_key,
    )
    assert row["is_clearly_automated"] is True
    assert row["proposed_action"] == "route_to:general"


async def test_repeated_evidence_bumps_existing_pending_suggestion(pool: asyncpg.Pool) -> None:
    """Scenario: 'Repeated evidence bumps an existing pending suggestion'."""
    sender_key = "invoices@bump-me.com"
    base = _now() - timedelta(days=5)
    timestamps = [base, base + timedelta(days=1), base + timedelta(days=2)]
    await _seed_evidence(
        pool,
        sender_key=sender_key,
        source_channel="email",
        timestamps=timestamps,
        verdict_action="route_to",
        verdict_target="finance",
    )

    first = await run_rule_promotion_trigger(pool)
    assert first["suggestions_created"] == 1

    row = await pool.fetchrow(
        "SELECT id, evidence_count FROM rule_promotion_suggestions WHERE sender_key = $1",
        sender_key,
    )
    suggestion_id = row["id"]
    assert row["evidence_count"] == 3

    # A fresh agreeing verdict arrives after the suggestion was created.
    new_event_id = uuid.uuid4()
    new_ts = base + timedelta(days=3)
    await _insert_ingestion_event(
        pool, event_id=new_event_id, received_at=new_ts, source_channel="email"
    )
    await _insert_message_inbox(pool, event_id=new_event_id, received_at=new_ts)
    await _insert_verdict(
        pool,
        ingestion_event_id=new_event_id,
        sender_key=sender_key,
        source_channel="email",
        decided_at=new_ts,
        verdict_action="route_to",
        verdict_target="finance",
    )

    second = await run_rule_promotion_trigger(pool)
    assert second["suggestions_bumped"] == 1
    assert second["suggestions_created"] == 0

    rows = await pool.fetch(
        "SELECT id, evidence_count FROM rule_promotion_suggestions WHERE sender_key = $1",
        sender_key,
    )
    # The unique partial index enforces exactly one pending row per sender/channel.
    assert len(rows) == 1
    assert rows[0]["id"] == suggestion_id
    assert rows[0]["evidence_count"] == 4


async def test_pending_suggestion_is_superseded_when_a_manual_rule_now_covers_it(
    pool: asyncpg.Pool,
) -> None:
    """A coverage recheck retires a stale pending card without minting another rule."""
    sender_key = "manual-rule@supersede-me.com"
    base = _now() - timedelta(days=3)
    timestamps = [base, base + timedelta(days=1), base + timedelta(days=2)]
    await _seed_evidence(
        pool,
        sender_key=sender_key,
        source_channel="email",
        timestamps=timestamps,
        verdict_action="route_to",
        verdict_target="finance",
    )

    initial = await run_rule_promotion_trigger(pool)
    assert initial["suggestions_created"] == 1

    await _insert_enabled_rule(
        pool,
        rule_type="sender_address",
        condition={"address": sender_key},
        action="skip",
        priority=5,
    )

    superseded = await run_rule_promotion_trigger(pool)
    assert superseded["suggestions_superseded"] == 1
    row = await pool.fetchrow(
        "SELECT status, decided_at, decided_by, created_rule_id "
        "FROM rule_promotion_suggestions WHERE sender_key = $1",
        sender_key,
    )
    assert row is not None
    assert row["status"] == "superseded"
    assert row["decided_at"] is not None
    assert row["decided_by"] == "system:rule_promotion_trigger"
    assert row["created_rule_id"] is None

    # A later scan sees the active rule and leaves the terminal audit row intact.
    repeated = await run_rule_promotion_trigger(pool)
    assert repeated["suggestions_superseded"] == 0
    # The module-scoped migrated DB retains prior fixtures, so other covered
    # candidates can contribute too; this sender must at least be suppressed.
    assert repeated["skipped_existing_rule"] >= 1
    rows = await pool.fetch(
        "SELECT status FROM rule_promotion_suggestions WHERE sender_key = $1", sender_key
    )
    assert [row["status"] for row in rows] == ["superseded"]


async def test_insufficient_evidence_count_creates_nothing(pool: asyncpg.Pool) -> None:
    sender_key = "too-few@sparse-sender.com"
    base = _now() - timedelta(days=2)
    timestamps = [base, base + timedelta(days=1)]  # threshold default is 3
    await _seed_evidence(
        pool,
        sender_key=sender_key,
        source_channel="email",
        timestamps=timestamps,
        verdict_action="route_to",
        verdict_target="finance",
    )

    result = await run_rule_promotion_trigger(pool)

    assert result["suggestions_created"] == 0
    row = await pool.fetchrow(
        "SELECT 1 FROM rule_promotion_suggestions WHERE sender_key = $1", sender_key
    )
    assert row is None
