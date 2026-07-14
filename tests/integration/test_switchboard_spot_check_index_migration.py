"""Real-Postgres regression: demotion spot-check index (bu-x55k3, bead 5 of 7).

Exercises migration ``sw_021`` (rule-promotion bead 5 —
``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 4 and
the merged openspec change ``switchboard-rule-promotion``, "Requirement:
Demotion via Spot-Check Sampling") against a fully migrated Postgres
instance (testcontainers), not just the mocked-pool unit tests in
``roster/switchboard/tests/test_rule_demotion.py``:

- ``ix_routing_verdict_log_spot_check_rule`` exists.
- ``record_routing_verdict(verdict_source='spot_check', matched_rule_id=...)``
  (the actual production writer, already exercised for other verdict_source
  values in ``test_switchboard_routing_verdict_log_migration.py``) round-trips
  through the real table for the 'spot_check' shape.
- ``maybe_create_demotion_suggestion`` (the actual production demotion
  scorer) round-trips against a real ``ingestion_rules`` row and real
  ``routing_verdict_log`` spot-check rows, including the CHECK constraint
  shape for a ``suggestion_kind='demotion'`` insert and the
  ``ux_rule_promotion_suggestions_pending_demotion`` unique index.
- Downgrade cleanly drops the index.
"""

from __future__ import annotations

import shutil
import uuid

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, index_exists, migration_db_name
from butlers.tools.switchboard.routing.rule_demotion import maybe_create_demotion_suggestion
from butlers.tools.switchboard.routing.verdict_log import record_routing_verdict

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    # Provision the switchboard chain into its own schema (bu-9auxy) to mirror prod's
    # per-butler-schema topology instead of landing switchboard tables in public.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
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


async def _insert_promoted_rule(
    pool: asyncpg.Pool, *, action: str = "route_to:finance"
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO ingestion_rules
            (id, scope, rule_type, condition, action, priority, created_by)
        VALUES ($1, 'global', 'sender_address', '{"address": "alerts@chase.com"}'::jsonb,
                $2, 100, 'promotion')
        """,
        rule_id,
        action,
    )
    return rule_id


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


def test_spot_check_index_exists(migrated_db_url: str) -> None:
    assert index_exists(
        migrated_db_url, "ix_routing_verdict_log_spot_check_rule", schema="switchboard"
    )


# ---------------------------------------------------------------------------
# record_routing_verdict(verdict_source='spot_check') round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_record_routing_verdict_spot_check_round_trips(pool: asyncpg.Pool) -> None:
    rule_id = await _insert_promoted_rule(pool)
    event_id = await _insert_ingestion_event(pool, dedupe_key="spot-check-1")

    row_id = await record_routing_verdict(
        pool,
        ingestion_event_id=event_id,
        sender_identity="alerts@chase.com",
        source_channel="email",
        verdict_source="spot_check",
        verdict_action="route_to",
        verdict_target="finance",
        matched_rule_id=rule_id,
    )
    assert row_id is not None

    row = await pool.fetchrow(
        "SELECT verdict_source, matched_rule_id FROM routing_verdict_log WHERE id = $1",
        uuid.UUID(row_id),
    )
    assert row["verdict_source"] == "spot_check"
    assert row["matched_rule_id"] == rule_id


# ---------------------------------------------------------------------------
# maybe_create_demotion_suggestion round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_maybe_create_demotion_suggestion_round_trips_on_sustained_disagreement(
    pool: asyncpg.Pool,
) -> None:
    rule_id = await _insert_promoted_rule(pool, action="route_to:finance")

    # 5 spot-checks, all disagreeing with the rule's route_to:finance.
    for i in range(5):
        event_id = await _insert_ingestion_event(pool, dedupe_key=f"demote-disagree-{i}")
        await record_routing_verdict(
            pool,
            ingestion_event_id=event_id,
            sender_identity="alerts@chase.com",
            source_channel="email",
            verdict_source="spot_check",
            verdict_action="route_to",
            verdict_target="general",
            matched_rule_id=rule_id,
        )

    result = await maybe_create_demotion_suggestion(pool, rule_id=str(rule_id), min_samples=5)

    assert result.ran is True
    assert result.demoted_suggestion_created is True
    assert result.agreement_score == 0.0

    row = await pool.fetchrow(
        "SELECT suggestion_kind, target_rule_id, status, evidence_count "
        "FROM rule_promotion_suggestions WHERE target_rule_id = $1",
        rule_id,
    )
    assert row is not None
    assert row["suggestion_kind"] == "demotion"
    assert row["status"] == "pending_review"
    assert row["evidence_count"] == 5


@pytest.mark.asyncio(loop_scope="session")
async def test_maybe_create_demotion_suggestion_respects_pending_demotion_unique_index(
    pool: asyncpg.Pool,
) -> None:
    """A second call while a demotion suggestion is already pending must not
    attempt (or need) a second insert -- the unique partial index backs this,
    but the app layer already short-circuits via the pending-check query."""
    rule_id = await _insert_promoted_rule(pool, action="skip")

    for i in range(5):
        event_id = await _insert_ingestion_event(pool, dedupe_key=f"demote-pending-{i}")
        await record_routing_verdict(
            pool,
            ingestion_event_id=event_id,
            sender_identity="alerts@chase.com",
            source_channel="email",
            verdict_source="spot_check",
            verdict_action="route_to",
            verdict_target="general",
            matched_rule_id=rule_id,
        )

    first = await maybe_create_demotion_suggestion(pool, rule_id=str(rule_id), min_samples=5)
    assert first.demoted_suggestion_created is True

    second = await maybe_create_demotion_suggestion(pool, rule_id=str(rule_id), min_samples=5)
    assert second.demoted_suggestion_created is False
    assert second.reason == "pending_demotion_exists"

    count = await pool.fetchval(
        "SELECT count(*) FROM rule_promotion_suggestions WHERE target_rule_id = $1", rule_id
    )
    assert count == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_maybe_create_demotion_suggestion_skips_non_promoted_rule(pool: asyncpg.Pool) -> None:
    rule_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO ingestion_rules (id, scope, rule_type, condition, action, priority, created_by)
        VALUES ($1, 'global', 'sender_address', '{"address": "hand@written.com"}'::jsonb,
                'route_to:finance', 100, 'dashboard')
        """,
        rule_id,
    )
    result = await maybe_create_demotion_suggestion(pool, rule_id=str(rule_id))
    assert result.ran is False
    assert result.reason == "not_a_promoted_rule"

    count = await pool.fetchval(
        "SELECT count(*) FROM rule_promotion_suggestions WHERE target_rule_id = $1", rule_id
    )
    assert count == 0


def test_downgrade_drops_spot_check_index(postgres_container) -> None:
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )

    config = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_020")

    assert not index_exists(db_url, "ix_routing_verdict_log_spot_check_rule", schema="switchboard")
