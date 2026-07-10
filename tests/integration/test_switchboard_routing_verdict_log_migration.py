"""Real-Postgres regression: switchboard.routing_verdict_log (bu-aga08, bead 1 of 7).

Exercises migration ``sw_019`` (rule-promotion bead 1 —
``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 1)
against a fully migrated Postgres instance (testcontainers), not just the
mocked-pool unit tests in ``roster/switchboard/tests/test_routing_verdict_log.py``:

- ``routing_verdict_log`` exists with the expected columns and indexes.
- The ``verdict_source`` / ``verdict_action`` CHECK constraints match the
  spec's fixed vocabularies.
- The ``ingestion_event_id`` FK to ``public.ingestion_events`` is enforced and
  cascades on delete (``sw_023``, bu-w4m9q) so scheduled retention purges of
  ``public.ingestion_events`` cannot FK-halt.
- ``record_routing_verdict`` (the actual production writer) round-trips
  through the real table, including the ``matched_rule_id`` /
  ``session_id`` FKs to this schema's own ``ingestion_rules`` / ``sessions``
  tables.
- Downgrade cleanly drops the table and its indexes.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.testing.migration import (
    create_migrated_test_db,
    index_exists,
    migration_db_name,
    table_exists,
)
from butlers.tools.switchboard.routing.verdict_log import record_routing_verdict

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "switchboard"]
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    yield p
    await p.close()


async def _insert_ingestion_event(pool: asyncpg.Pool, *, dedupe_key: str) -> uuid.UUID:
    event_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.ingestion_events
            (id, source_channel, source_provider, source_endpoint_identity,
             external_event_id, dedupe_key, dedupe_strategy, ingestion_tier, policy_tier)
        VALUES ($1, 'email', 'gmail', 'owner@example.com', $2, $3, 'connector_api', 'full', 'default')
        """,
        event_id,
        f"ext-{dedupe_key}",
        dedupe_key,
    )
    return event_id


async def _insert_ingestion_rule(pool: asyncpg.Pool) -> uuid.UUID:
    rule_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO ingestion_rules (id, scope, rule_type, condition, action, priority)
        VALUES ($1, 'global', 'sender_domain', '{"domain": "chase.com"}'::jsonb, 'route_to:finance', 100)
        """,
        rule_id,
    )
    return rule_id


async def _insert_session(pool: asyncpg.Pool) -> uuid.UUID:
    session_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO sessions (id, prompt, trigger_source, request_id)
        VALUES ($1, 'classify this message', 'classification', $2)
        """,
        session_id,
        str(uuid.uuid4()),
    )
    return session_id


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


def test_routing_verdict_log_table_exists_with_expected_columns(migrated_db_url: str) -> None:
    assert table_exists(migrated_db_url, "routing_verdict_log")

    engine = create_engine(migrated_db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'routing_verdict_log'"
            )
        ).fetchall()
    engine.dispose()

    columns = {r[0] for r in rows}
    assert columns == {
        "id",
        "ingestion_event_id",
        "sender_key",
        "source_channel",
        "verdict_source",
        "verdict_action",
        "verdict_target",
        "matched_rule_id",
        "session_id",
        "decided_at",
    }


def test_expected_indexes_exist(migrated_db_url: str) -> None:
    assert index_exists(migrated_db_url, "ix_routing_verdict_log_sender_channel_decided")
    assert index_exists(migrated_db_url, "ix_routing_verdict_log_llm_only")


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_verdict_source_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    event_id = await _insert_ingestion_event(pool, dedupe_key="check-source-bogus")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO routing_verdict_log
                (ingestion_event_id, sender_key, source_channel, verdict_source, verdict_action)
            VALUES ($1, 'user@example.com', 'email', 'bogus', 'route_to')
            """,
            event_id,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_verdict_action_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    event_id = await _insert_ingestion_event(pool, dedupe_key="check-action-bogus")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO routing_verdict_log
                (ingestion_event_id, sender_key, source_channel, verdict_source, verdict_action)
            VALUES ($1, 'user@example.com', 'email', 'llm', 'ghosted')
            """,
            event_id,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_ingestion_event_id_fk_is_enforced(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(
            """
            INSERT INTO routing_verdict_log
                (ingestion_event_id, sender_key, source_channel, verdict_source, verdict_action)
            VALUES ($1, 'user@example.com', 'email', 'llm', 'route_to')
            """,
            uuid.uuid4(),
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_matched_rule_id_fk_is_enforced(pool: asyncpg.Pool) -> None:
    event_id = await _insert_ingestion_event(pool, dedupe_key="fk-rule-bogus")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(
            """
            INSERT INTO routing_verdict_log
                (ingestion_event_id, sender_key, source_channel, verdict_source,
                 verdict_action, matched_rule_id)
            VALUES ($1, 'user@example.com', 'email', 'rule', 'route_to', $2)
            """,
            event_id,
            uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# ON DELETE CASCADE (sw_023, bu-w4m9q): purging an ingestion_event must cascade
# the attached verdict rows away rather than FK-halting the retention purge.
# ---------------------------------------------------------------------------


def test_ingestion_event_fk_has_on_delete_cascade(migrated_db_url: str) -> None:
    """pg_constraint.confdeltype == 'c' (CASCADE) for the event FK, not the
    default 'a' (NO ACTION / RESTRICT) it shipped with in sw_019."""
    engine = create_engine(migrated_db_url)
    with engine.connect() as conn:
        confdeltype = conn.execute(
            text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conname = 'routing_verdict_log_ingestion_event_id_fkey'"
            )
        ).scalar()
    engine.dispose()
    assert confdeltype == "c"


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_ingestion_event_cascades_to_verdict_log(pool: asyncpg.Pool) -> None:
    """Deleting a ``public.ingestion_events`` row with an attached verdict row
    succeeds and cascades the verdict row away — the exact shape of an
    ``OwnTracksRetention`` purge cycle hitting a routed (full-tier) event."""
    event_id = await _insert_ingestion_event(pool, dedupe_key="cascade-delete")
    row_id = await record_routing_verdict(
        pool,
        ingestion_event_id=event_id,
        sender_identity="alerts@chase.com",
        source_channel="email",
        verdict_source="llm",
        verdict_action="route_to",
        verdict_target="finance",
    )
    assert row_id is not None

    # The purge DELETE must NOT raise a ForeignKeyViolationError.
    await pool.execute("DELETE FROM public.ingestion_events WHERE id = $1", event_id)

    orphan = await pool.fetchrow("SELECT 1 FROM routing_verdict_log WHERE id = $1::uuid", row_id)
    assert orphan is None


# ---------------------------------------------------------------------------
# record_routing_verdict() round-trip via the real production writer.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_record_routing_verdict_round_trips_llm_verdict_with_session_id(
    pool: asyncpg.Pool,
) -> None:
    event_id = await _insert_ingestion_event(pool, dedupe_key="roundtrip-llm")
    session_id = await _insert_session(pool)

    row_id = await record_routing_verdict(
        pool,
        ingestion_event_id=event_id,
        sender_identity="Billing <billing@chase.com>",
        source_channel="email",
        verdict_source="llm",
        verdict_action="route_to",
        verdict_target="finance",
        session_id=session_id,
    )
    assert row_id is not None

    row = await pool.fetchrow("SELECT * FROM routing_verdict_log WHERE id = $1::uuid", row_id)
    assert row is not None
    assert row["ingestion_event_id"] == event_id
    assert row["sender_key"] == "billing@chase.com"
    assert row["source_channel"] == "email"
    assert row["verdict_source"] == "llm"
    assert row["verdict_action"] == "route_to"
    assert row["verdict_target"] == "finance"
    assert row["matched_rule_id"] is None
    assert row["session_id"] == session_id


@pytest.mark.asyncio(loop_scope="session")
async def test_record_routing_verdict_round_trips_rule_verdict_with_matched_rule_id(
    pool: asyncpg.Pool,
) -> None:
    event_id = await _insert_ingestion_event(pool, dedupe_key="roundtrip-rule")
    rule_id = await _insert_ingestion_rule(pool)

    row_id = await record_routing_verdict(
        pool,
        ingestion_event_id=event_id,
        sender_identity="alerts@chase.com",
        source_channel="email",
        verdict_source="rule",
        verdict_action="route_to",
        verdict_target="finance",
        matched_rule_id=rule_id,
    )
    assert row_id is not None

    row = await pool.fetchrow("SELECT * FROM routing_verdict_log WHERE id = $1::uuid", row_id)
    assert row["verdict_source"] == "rule"
    assert row["matched_rule_id"] == rule_id
    assert row["session_id"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_record_routing_verdict_returns_none_on_bad_ingestion_event_id(
    pool: asyncpg.Pool,
) -> None:
    """Best-effort contract: an FK violation (e.g. a stale/racy
    ingestion_event_id) must be swallowed, not raised, and the caller gets
    ``None`` back rather than an exception breaking the routing decision it
    describes."""
    row_id = await record_routing_verdict(
        pool,
        ingestion_event_id=uuid.uuid4(),
        sender_identity="user@example.com",
        source_channel="email",
        verdict_source="llm",
        verdict_action="route_to",
        verdict_target="finance",
    )
    assert row_id is None


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_table_and_indexes(postgres_container) -> None:
    from butlers.migrations import _build_alembic_config

    db_url = create_migrated_test_db(
        postgres_container, migration_db_name(), chains=["core", "switchboard"]
    )

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.downgrade(config, "switchboard@sw_018")

    assert not table_exists(db_url, "routing_verdict_log")
    assert not index_exists(db_url, "ix_routing_verdict_log_sender_channel_decided")
    assert not index_exists(db_url, "ix_routing_verdict_log_llm_only")
