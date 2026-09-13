"""Real-Postgres coverage for approval admission and legacy push evidence.

The reservation query, burst counter, deferred envelope, and pending-action
clock are all database state. These tests therefore use the production core +
approvals migration chains rather than a mocked pool.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from butlers.api.read_models.timeline_v1 import (
    query_timeline_attention_notifications_single,
    query_timeline_notification_histogram_single,
    query_timeline_notifications_single,
)
from butlers.api.routers.notifications import (
    _extract_stored_envelope,
    _fetch_notification_row,
    _query_notifications,
    ack_failed_notifications,
    mark_notification_read,
    notification_stats,
)
from butlers.config import ApprovalRiskTier
from butlers.core.approval_delivery_transport import (
    MessengerApprovalHandoffRepository,
    TrustedRecoveryContext,
)
from butlers.core.approval_delivery_worker import HandoffResult
from butlers.db import register_jsonb_codec
from butlers.modules.approvals.gate import _make_gate_wrapper
from butlers.modules.approvals.notifications import (
    ApprovalPushRuntime,
    emit_approval_push,
)
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.pipeline import (
    _load_conversation_history,
    _load_email_history,
    _load_realtime_history,
)
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    migration_db_name,
)
from butlers.tools.switchboard.notification.deliver import deliver as switchboard_deliver

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the production tables the park path writes and reads."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
    )


@pytest.fixture
async def approval_push_pool(migrated_db_url: str):
    """Return a clean JSONB-aware pool with approval pushes enabled."""
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await pool.execute(
        "TRUNCATE approval_delivery_attempts, approval_delivery_cohort_members, "
        "approval_delivery_presentations, approval_delivery_cohorts, "
        "approval_delivery_intents, approval_push_emissions, approval_events, "
        "pending_actions, deferred_notifications CASCADE"
    )
    await pool.execute(
        "UPDATE public.approvals_policy "
        "SET quiet_start_hour = NULL, quiet_end_hour = NULL, timezone = 'UTC' "
        "WHERE id = 1"
    )
    yield pool
    await pool.close()


@pytest.fixture(scope="module")
def recovery_transport_db_url(postgres_container) -> str:
    """Provision real Messenger and Switchboard schemas for recovery isolation."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(
        _build_alembic_config(
            db_url,
            chains=["core"],
            target_schema="switchboard",
        ),
        "core@head",
    )
    command.upgrade(
        _build_alembic_config(
            db_url,
            chains=["switchboard"],
            target_schema="switchboard",
        ),
        "switchboard@head",
    )
    command.upgrade(
        _build_alembic_config(
            db_url,
            chains=["messenger"],
            target_schema="messenger",
        ),
        "messenger@head",
    )
    return db_url


@pytest.fixture
async def messenger_handoff_pool(recovery_transport_db_url: str):
    pool = await asyncpg.create_pool(
        recovery_transport_db_url,
        min_size=1,
        max_size=4,
        server_settings={"search_path": "messenger,public"},
    )
    await pool.execute("TRUNCATE approval_delivery_handoffs")
    yield pool
    await pool.close()


@pytest.fixture
async def switchboard_recovery_pool(recovery_transport_db_url: str):
    pool = await asyncpg.create_pool(
        recovery_transport_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
    await pool.execute("TRUNCATE notifications, message_inbox CASCADE")
    await pool.execute(
        """
        INSERT INTO butler_registry (
            name, endpoint_url, modules, last_seen_at, eligibility_state
        ) VALUES
            ('relationship', 'http://relationship.invalid/mcp', '[]'::jsonb,
             clock_timestamp(), 'active'),
            ('messenger', 'http://messenger.invalid/mcp', '[\"telegram\"]'::jsonb,
             clock_timestamp(), 'active')
        ON CONFLICT (name) DO UPDATE
        SET last_seen_at = EXCLUDED.last_seen_at,
            eligibility_state = EXCLUDED.eligibility_state
        """
    )
    yield pool
    await pool.close()


def _runtime(dispatch: AsyncMock) -> ApprovalPushRuntime:
    """Provide deterministic owner/secret dependencies without an LLM or broker."""
    credential_store = SimpleNamespace(resolve=AsyncMock(return_value="callback-secret"))
    return ApprovalPushRuntime(
        dispatch=dispatch,
        resolve_owner_recipient=AsyncMock(return_value="100200300"),
        credential_store=credential_store,
        dashboard_base_url="https://dashboard.example.test",
    )


async def test_park_deduplication_key_blocks_owner_decisions_and_allows_expiry(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """A durable key preserves owner decisions without blocking expiry resurfacing."""
    now = datetime.now(UTC)
    deduplication_key = "relationship:entity-dedup:test-source:test-target"

    async def _park(action_id: uuid.UUID):
        return await park_pending_action(
            approval_push_pool,
            action_id=action_id,
            tool_name="memory_entity_merge",
            tool_args={"source_entity_id": "test-source", "target_entity_id": "test-target"},
            agent_summary="Merge test duplicate entities",
            requested_at=now,
            expires_at=now + timedelta(days=3),
            origin_butler="relationship",
            approval_push_runtime=None,
            deduplication_key=deduplication_key,
        )

    first_action_id = uuid.uuid4()
    await _park(first_action_id)

    await approval_push_pool.execute(
        "UPDATE pending_actions SET status = 'abandoned' WHERE id = $1", first_action_id
    )
    duplicate = await _park(uuid.uuid4())
    assert duplicate.duplicate is True
    assert duplicate.action_id == first_action_id

    await approval_push_pool.execute(
        "UPDATE pending_actions SET status = 'expired' WHERE id = $1", first_action_id
    )
    replacement_id = uuid.uuid4()
    replacement = await _park(replacement_id)
    assert replacement.duplicate is False
    assert replacement.action_id == replacement_id


async def _insert_pending_action(
    pool: asyncpg.Pool,
    *,
    requested_at: datetime,
    expires_at: datetime | None = None,
    tool_name: str = "relationship_assert_fact",
) -> dict[str, object]:
    action_id = uuid.uuid4()
    action = {
        "id": action_id,
        "tool_name": tool_name,
        "requested_at": requested_at,
        "expires_at": expires_at or requested_at + timedelta(hours=72),
        "why": "The owner requested this relationship update.",
        "blast_radius": "contact",
        "reversibility": "compensable",
    }
    await pool.execute(
        """
        INSERT INTO pending_actions
            (id, tool_name, tool_args, status, requested_at, expires_at,
             why, evidence, blast_radius, reversibility)
        VALUES ($1, $2, $3, 'pending', $4, $5, $6, $7, $8, $9)
        """,
        action_id,
        tool_name,
        {"subject": "owner", "predicate": "knows", "object": "Ada"},
        requested_at,
        action["expires_at"],
        action["why"],
        [],
        action["blast_radius"],
        action["reversibility"],
    )
    return action


async def _never_execute(**_kwargs: object) -> dict[str, object]:
    raise AssertionError("A parked gate action must not execute its original tool")


async def test_gate_park_atomically_admits_one_recoverable_presentation_without_sending(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """Park creates durable action/presentation state without live delivery."""
    dispatch = AsyncMock()
    runtime = _runtime(dispatch)
    wrapper = _make_gate_wrapper(
        tool_name="relationship_assert_fact",
        original_fn=_never_execute,
        pool=approval_push_pool,
        expiry_hours=72,
        risk_tier=ApprovalRiskTier.MEDIUM,
        rule_precedence=(),
        butler_name="relationship",
        approval_push_runtime=runtime,
    )

    result = await wrapper(
        subject="owner",
        predicate="knows",
        object="Ada",
        _why="The owner asked to preserve this relationship fact.",
        _evidence=[],
        _blast_radius="contact",
        _reversibility="compensable",
    )

    assert result["status"] == "pending_approval"
    action_id = uuid.UUID(result["action_id"])
    dispatch.assert_not_awaited()
    row = await approval_push_pool.fetchrow(
        """
        SELECT pa.id, pa.tool_name, adi.action_key, adi.origin_butler, adi.admission_mode,
               adp.presentation_key, adp.presentation_mode, adp.state
          FROM pending_actions AS pa
          JOIN approval_delivery_intents AS adi ON adi.action_id = pa.id
          JOIN approval_delivery_presentations AS adp ON adp.intent_id = adi.id
         WHERE pa.id = $1
        """,
        action_id,
    )
    assert row is not None
    assert row["tool_name"] == "relationship_assert_fact"
    assert row["action_key"] == f"approval:public:{action_id}"
    assert row["origin_butler"] == "relationship"
    assert row["admission_mode"] == "single"
    assert row["presentation_key"] == f"approval:public:{action_id}:p:1"
    assert row["presentation_mode"] == "single"
    assert row["state"] == "ready"
    assert await approval_push_pool.fetchval("SELECT count(*) FROM approval_push_emissions") == 0

    same_id = await park_pending_action(
        approval_push_pool,
        action_id=action_id,
        tool_name="relationship_assert_fact",
        tool_args={"subject": "owner"},
        agent_summary="Retry same admission",
        requested_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=72),
        origin_butler="relationship",
        approval_push_runtime=None,
    )
    assert same_id.duplicate is True
    assert same_id.intent_id is not None
    assert same_id.action_key == row["action_key"]


async def test_atomic_admission_rolls_back_action_when_intent_insert_fails(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """A failure after the action INSERT cannot strand a pending action."""
    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    await approval_push_pool.execute(
        """
        CREATE OR REPLACE FUNCTION reject_test_intent() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'reject test intent'; END $$;
        CREATE TRIGGER reject_test_intent
        BEFORE INSERT ON approval_delivery_intents
        FOR EACH ROW EXECUTE FUNCTION reject_test_intent()
        """
    )
    try:
        with pytest.raises(asyncpg.RaiseError, match="reject test intent"):
            await park_pending_action(
                approval_push_pool,
                action_id=action_id,
                tool_name="relationship_assert_fact",
                tool_args={"subject": "owner"},
                agent_summary="Test atomic rollback",
                requested_at=now,
                expires_at=now + timedelta(hours=72),
                origin_butler="relationship",
                approval_push_runtime=None,
            )
        assert not await approval_push_pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pending_actions WHERE id = $1)", action_id
        )
    finally:
        await approval_push_pool.execute(
            "DROP TRIGGER IF EXISTS reject_test_intent ON approval_delivery_intents; "
            "DROP FUNCTION IF EXISTS reject_test_intent()"
        )


async def test_semantic_duplicate_returns_existing_action_and_intent_under_concurrency(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """The schema lock and semantic key admit one stable action/intent pair."""
    now = datetime.now(UTC)
    key = f"relationship:test:{uuid.uuid4()}"

    async def admit():
        return await park_pending_action(
            approval_push_pool,
            action_id=uuid.uuid4(),
            tool_name="memory_entity_merge",
            tool_args={"source": "one", "target": "two"},
            agent_summary="Merge duplicate entities",
            requested_at=now,
            expires_at=now + timedelta(hours=72),
            origin_butler="relationship",
            approval_push_runtime=None,
            deduplication_key=key,
        )

    first, second = await asyncio.gather(admit(), admit())
    assert first.action_id == second.action_id
    assert first.intent_id == second.intent_id
    assert first.action_key == second.action_key
    assert {first.duplicate, second.duplicate} == {False, True}
    assert (
        await approval_push_pool.fetchval(
            "SELECT count(*) FROM pending_actions WHERE deduplication_key = $1", key
        )
        == 1
    )


async def test_concurrent_first_three_digest_and_collapse_are_durable(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """Five simultaneous parks yield three direct, one digest, one collapse."""
    now = datetime.now(UTC)

    async def admit(index: int):
        return await park_pending_action(
            approval_push_pool,
            action_id=uuid.uuid4(),
            tool_name=f"test_action_{index}",
            tool_args={"index": index},
            agent_summary=f"Test action {index}",
            requested_at=now,
            expires_at=now + timedelta(hours=72),
            origin_butler="relationship",
            approval_push_runtime=None,
        )

    admissions = await asyncio.gather(*(admit(index) for index in range(5)))
    assert sorted(item.admission_mode for item in admissions) == [
        "cohort_anchor",
        "collapsed",
        "single",
        "single",
        "single",
    ]
    assert (
        await approval_push_pool.fetchval(
            "SELECT count(*) FROM approval_delivery_presentations "
            "WHERE presentation_mode = 'burst_digest'"
        )
        == 1
    )
    assert (
        await approval_push_pool.fetchval(
            "SELECT count(*) FROM approval_delivery_presentations WHERE state = 'collapsed'"
        )
        == 1
    )
    assert (
        await approval_push_pool.fetchval("SELECT count(*) FROM approval_delivery_cohort_members")
        == 2
    )
    assert await approval_push_pool.fetchval("SELECT count(*) FROM deferred_notifications") == 0

    cohort_id = await approval_push_pool.fetchval("SELECT id FROM approval_delivery_cohorts")
    await approval_push_pool.execute(
        "UPDATE approval_delivery_cohort_members SET eligible = false WHERE cohort_id = $1",
        cohort_id,
    )
    await approval_push_pool.execute(
        "UPDATE approval_delivery_presentations "
        "SET state = 'cancelled', last_reason_code = 'cohort_empty', next_attempt_at = NULL "
        "WHERE cohort_id = $1 AND state = 'ready'",
        cohort_id,
    )
    successor = await admit(6)
    assert successor.admission_mode == "collapsed"
    digest_rows = await approval_push_pool.fetch(
        "SELECT presentation_generation, state FROM approval_delivery_presentations "
        "WHERE cohort_id = $1 ORDER BY presentation_generation",
        cohort_id,
    )
    assert [tuple(row.values()) for row in digest_rows] == [(1, "cancelled"), (2, "ready")]


async def test_admission_recomputes_database_time_after_serialization_wait(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """A cohort expiring during lock wait cannot capture the delayed admission."""
    database_now = await approval_push_pool.fetchval("SELECT clock_timestamp()")
    cohort_id = uuid.uuid4()
    cohort_key = f"approval-cohort:public:{cohort_id}"
    window_start = database_now - timedelta(minutes=10) + timedelta(milliseconds=250)
    await approval_push_pool.execute(
        """
        INSERT INTO approval_delivery_cohorts (
            id, cohort_key, owning_schema, window_started_at, window_ends_at
        ) VALUES (
            $1, $2, 'public', $3::timestamptz,
            $3::timestamptz + interval '10 minutes'
        )
        """,
        cohort_id,
        cohort_key,
        window_start,
    )

    action_id = uuid.uuid4()
    async with approval_push_pool.acquire() as blocker:
        async with blocker.transaction():
            await blocker.execute(
                "SELECT pg_advisory_xact_lock(hashtext('approval-delivery:' || current_schema()))"
            )
            admission_task = asyncio.create_task(
                park_pending_action(
                    approval_push_pool,
                    action_id=action_id,
                    tool_name="relationship_assert_fact",
                    tool_args={"subject": "owner"},
                    agent_summary="Post-lock clock admission",
                    requested_at=database_now,
                    expires_at=database_now + timedelta(hours=72),
                    origin_butler="relationship",
                    approval_push_runtime=None,
                )
            )
            await asyncio.sleep(0.4)
            release_time = await blocker.fetchval("SELECT clock_timestamp()")
        admission = await asyncio.wait_for(admission_task, timeout=5)

    assert admission.admission_mode == "single"
    assert admission.not_before >= release_time


async def test_quiet_hours_are_snapshotted_without_generic_deferral(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """Admission stores the exact quiet-hours release and never reuses the generic queue."""
    database_now = await approval_push_pool.fetchval("SELECT clock_timestamp()")
    quiet_start = database_now.hour
    quiet_end = (quiet_start + 1) % 24
    await approval_push_pool.execute(
        """
        UPDATE public.approvals_policy
           SET quiet_start_hour = $1, quiet_end_hour = $2, timezone = 'UTC'
         WHERE id = 1
        """,
        quiet_start,
        quiet_end,
    )
    now = datetime.now(UTC)
    admission = await park_pending_action(
        approval_push_pool,
        action_id=uuid.uuid4(),
        tool_name="relationship_assert_fact",
        tool_args={"subject": "owner"},
        agent_summary="Quiet-hours admission",
        requested_at=now,
        expires_at=now + timedelta(hours=72),
        origin_butler="relationship",
        approval_push_runtime=_runtime(AsyncMock()),
    )
    row = await approval_push_pool.fetchrow(
        "SELECT state, last_reason_code, not_before, next_attempt_at "
        "FROM approval_delivery_presentations WHERE presentation_key = $1",
        admission.presentation_key,
    )
    expected_release = database_now.replace(hour=quiet_end, minute=0, second=0, microsecond=0)
    if expected_release <= database_now:
        expected_release += timedelta(days=1)
    assert row["next_attempt_at"] == row["not_before"]
    assert row["not_before"] == expected_release
    assert row["last_reason_code"] == "quiet_hours"
    assert await approval_push_pool.fetchval("SELECT count(*) FROM deferred_notifications") == 0


async def test_delivery_schema_rejects_unknown_vocabulary_and_mutated_attempts(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """Closed values and append-only attempt evidence are enforced in PostgreSQL."""
    now = datetime.now(UTC)
    admission = await park_pending_action(
        approval_push_pool,
        action_id=uuid.uuid4(),
        tool_name="relationship_assert_fact",
        tool_args={"subject": "owner"},
        agent_summary="Closed-vocabulary admission",
        requested_at=now,
        expires_at=now + timedelta(hours=72),
        origin_butler="relationship",
        approval_push_runtime=None,
    )
    presentation_id = await approval_push_pool.fetchval(
        "SELECT id FROM approval_delivery_presentations WHERE presentation_key = $1",
        admission.presentation_key,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await approval_push_pool.execute(
            "UPDATE approval_delivery_presentations SET state = 'stuck' WHERE id = $1",
            presentation_id,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await approval_push_pool.execute(
            "UPDATE approval_delivery_presentations SET last_reason_code = 'raw_error' "
            "WHERE id = $1",
            presentation_id,
        )
    attempt_id = uuid.uuid4()
    await approval_push_pool.execute(
        """
        INSERT INTO approval_delivery_attempts (
            id, presentation_id, presentation_generation, attempt_number,
            claim_fence, outcome
        ) VALUES ($1, $2, 1, 1, 1, 'started')
        """,
        attempt_id,
        presentation_id,
    )
    with pytest.raises(asyncpg.RaiseError):
        await approval_push_pool.execute(
            "UPDATE approval_delivery_attempts SET outcome = 'confirmed' WHERE id = $1",
            attempt_id,
        )


@pytest.mark.filterwarnings(
    "ignore:The test .* is marked with '@pytest.mark.asyncio':pytest.PytestWarning"
)
def test_approvals_migration_preserves_legacy_rows_and_refuses_nonempty_downgrade(
    postgres_container,
) -> None:
    """Upgrade performs no backfill; recovery evidence blocks destructive downgrade."""
    from sqlalchemy import create_engine, exc, text

    from alembic import command
    from butlers.migrations import _build_alembic_config, get_chain_head

    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["approvals"])
    command.upgrade(config, "approvals_014")
    action_id = uuid.uuid4()
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO pending_actions (id, tool_name, tool_args, status) "
                    "VALUES (:id, 'legacy_action', '{}'::jsonb, 'pending')"
                ),
                {"id": action_id},
            )
            connection.execute(
                text(
                    "INSERT INTO approval_push_emissions "
                    "(action_id, emission_kind, outcome) "
                    "VALUES (:id, 'single', 'delivered')"
                ),
                {"id": action_id},
            )
        command.upgrade(config, "approvals@head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM approval_delivery_intents")
                ).scalar_one()
                == 0
            )
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == (get_chain_head("approvals"))
            assert (
                connection.execute(
                    text(
                        "SELECT emission_kind || ':' || outcome "
                        "FROM approval_push_emissions WHERE action_id = :id"
                    ),
                    {"id": action_id},
                ).scalar_one()
                == "single:delivered"
            )
        command.downgrade(config, "approvals_014")
        command.upgrade(config, "approvals@head")
        intent_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO approval_delivery_intents "
                    "(id, action_id, action_key, owning_schema, origin_butler, admission_mode) "
                    "VALUES (:intent, :action, :key, 'public', 'relationship', 'single')"
                ),
                {
                    "intent": intent_id,
                    "action": action_id,
                    "key": f"approval:public:{action_id}",
                },
            )
        with pytest.raises(exc.DBAPIError, match="approval delivery recovery data exists"):
            command.downgrade(config, "approvals_014")
    finally:
        engine.dispose()


@pytest.mark.filterwarnings(
    "ignore:The test .* is marked with '@pytest.mark.asyncio':pytest.PytestWarning"
)
def test_messenger_handoff_migration_enforces_binding_and_refuses_data_loss(
    postgres_container,
) -> None:
    """The additive Messenger ledger accepts only bounded trusted tuple shapes."""
    from sqlalchemy import create_engine, exc, text

    from alembic import command
    from butlers.migrations import _build_alembic_config

    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["messenger"])
    command.upgrade(config, "messenger@head")
    engine = create_engine(db_url)
    subject_id = uuid.uuid4()
    try:
        with pytest.raises(exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO approval_delivery_handoffs "
                        "(issuer, owning_schema, subject_key, presentation_key, "
                        "presentation_generation, presentation_mode) VALUES "
                        "('relationship', 'relationship', :subject, :presentation, 1, "
                        "'burst_digest')"
                    ),
                    {
                        "subject": f"approval:relationship:{subject_id}",
                        "presentation": f"approval:relationship:{subject_id}:p:1",
                    },
                )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO approval_delivery_handoffs "
                    "(issuer, owning_schema, subject_key, presentation_key, "
                    "presentation_generation, presentation_mode) VALUES "
                    "('relationship', 'relationship', :subject, :presentation, 1, 'single')"
                ),
                {
                    "subject": f"approval:relationship:{subject_id}",
                    "presentation": f"approval:relationship:{subject_id}:p:1",
                },
            )
        with pytest.raises(exc.DBAPIError, match="Cannot downgrade msg_004"):
            command.downgrade(config, "msg_003")
    finally:
        engine.dispose()


def _trusted_recovery_context() -> TrustedRecoveryContext:
    subject_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    subject_key = f"approval:relationship:{subject_id}"
    return TrustedRecoveryContext(
        issuer="relationship",
        owning_schema="relationship",
        operation="handoff",
        subject_kind="action",
        subject_key=subject_key,
        presentation_key=f"{subject_key}:p:1",
        presentation_generation=1,
        presentation_mode="single",
    )


async def test_messenger_handoff_tuple_suppresses_duplicates_and_reconciles_ambiguity(
    messenger_handoff_pool: asyncpg.Pool,
) -> None:
    repository = MessengerApprovalHandoffRepository(messenger_handoff_pool)
    context = _trusted_recovery_context()
    provider = AsyncMock(return_value={"result": {"message_id": "provider-ref-1"}})

    first = await repository.process(context, provider_call=provider)
    duplicate = await repository.process(context, provider_call=provider)

    assert first == duplicate == HandoffResult("confirmed", provider_reference="provider-ref-1")
    provider.assert_awaited_once()
    row = await messenger_handoff_pool.fetchrow(
        """
        SELECT issuer, owning_schema, handoff_class, reason_code, provider_reference,
               subject_key, presentation_key
        FROM approval_delivery_handoffs
        """
    )
    assert dict(row) == {
        "issuer": "relationship",
        "owning_schema": "relationship",
        "handoff_class": "confirmed",
        "reason_code": None,
        "provider_reference": "provider-ref-1",
        "subject_key": context.subject_key,
        "presentation_key": context.presentation_key,
    }

    await messenger_handoff_pool.execute("TRUNCATE approval_delivery_handoffs")
    uncertain_provider = AsyncMock(side_effect=TimeoutError("synthetic timeout"))
    ambiguous = await repository.process(context, provider_call=uncertain_provider)
    repeated = await repository.process(context, provider_call=AsyncMock())
    assert ambiguous == repeated == HandoffResult("ambiguous", "provider_outcome_unknown")
    uncertain_provider.assert_awaited_once()

    await messenger_handoff_pool.execute("TRUNCATE approval_delivery_handoffs")
    whatsapp_failure = AsyncMock(return_value={"error": "synthetic bridge unavailable"})
    explicit_failure = await repository.process(context, provider_call=whatsapp_failure)
    suppressed_provider = AsyncMock(return_value={"message_id": "must-not-send"})
    failure_duplicate = await repository.process(context, provider_call=suppressed_provider)
    assert (
        explicit_failure
        == failure_duplicate
        == HandoffResult("ambiguous", "provider_outcome_unknown")
    )
    whatsapp_failure.assert_awaited_once()
    suppressed_provider.assert_not_awaited()
    assert (
        await messenger_handoff_pool.fetchval(
            "SELECT handoff_class FROM approval_delivery_handoffs"
        )
        == "ambiguous"
    )

    await messenger_handoff_pool.execute("TRUNCATE approval_delivery_handoffs")
    email_success = AsyncMock(return_value={"status": "sent"})
    email_confirmed = await repository.process(context, provider_call=email_success)
    assert email_confirmed == HandoffResult("confirmed")
    email_success.assert_awaited_once()

    await messenger_handoff_pool.execute("TRUNCATE approval_delivery_handoffs")
    reconcile_context = TrustedRecoveryContext(
        **{
            **context.as_internal_dict(),
            "operation": "reconcile",
        }
    )
    reconciled = await repository.process(
        reconcile_context,
        provider_call=None,
        reconcile_call=AsyncMock(
            return_value=HandoffResult("safe_retry", "provider_preflight_failed")
        ),
    )
    assert reconciled.classification == "safe_retry"
    retry_provider = AsyncMock(return_value={"message_id": "provider-ref-after-reconcile"})
    confirmed = await repository.process(context, provider_call=retry_provider)
    assert confirmed.classification == "confirmed"
    retry_provider.assert_awaited_once()


async def test_messenger_safe_retry_never_marks_provider_started_and_reuses_same_key(
    messenger_handoff_pool: asyncpg.Pool,
) -> None:
    repository = MessengerApprovalHandoffRepository(messenger_handoff_pool)
    context = _trusted_recovery_context()

    safe_retry = await repository.process(context, provider_call=None)
    assert safe_retry == HandoffResult("safe_retry", "transport_unavailable")
    assert await messenger_handoff_pool.fetchval(
        "SELECT provider_started_at IS NULL FROM approval_delivery_handoffs"
    )

    provider = AsyncMock(return_value={"message_id": "provider-ref-2"})
    confirmed = await repository.process(context, provider_call=provider)
    assert confirmed == HandoffResult("confirmed", provider_reference="provider-ref-2")
    provider.assert_awaited_once()
    assert (
        await messenger_handoff_pool.fetchval("SELECT count(*) FROM approval_delivery_handoffs")
        == 1
    )


async def test_recovery_path_persists_no_generic_or_history_content(
    switchboard_recovery_pool: asyncpg.Pool,
) -> None:
    context = _trusted_recovery_context()
    message_sentinel = "rendered-message-synthetic-sentinel"
    recipient_sentinel = "recipient-thread-synthetic-sentinel"
    callback_sentinel = "callback-material-synthetic-sentinel"
    notify_request = {
        "schema_version": "notify.v1",
        "origin_butler": "relationship",
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": message_sentinel,
            "recipient": recipient_sentinel,
        },
        "actions": [
            {
                "verb": "approve",
                "callback_token": callback_sentinel,
                "dashboard_url": "https://dashboard.example.test/approvals/synthetic",
            },
            {
                "verb": "reject",
                "callback_token": callback_sentinel,
                "dashboard_url": "https://dashboard.example.test/approvals/synthetic",
            },
            {
                "verb": "open_dashboard",
                "dashboard_url": "https://dashboard.example.test/approvals/synthetic",
            },
        ],
        "recovery": {
            **context.as_internal_dict(),
        },
    }
    notify_request["recovery"].pop("issuer")
    notify_request["recovery"].pop("owning_schema")

    route_result = {
        "result": {
            "notify_response": {
                "status": "ok",
                "handoff": {"classification": "confirmed"},
            }
        },
        "transport": {"outcome": "confirmed", "retryable": False},
    }
    with (
        patch(
            "butlers.tools.switchboard.notification.deliver.route",
            new=AsyncMock(return_value=route_result),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.log_notification",
            new=AsyncMock(),
        ) as generic_log,
    ):
        result = await switchboard_deliver(
            switchboard_recovery_pool,
            source_butler="relationship",
            trusted_source="relationship",
            notify_request=notify_request,
        )

    assert result["handoff"]["classification"] == "confirmed"
    generic_log.assert_not_awaited()
    assert await switchboard_recovery_pool.fetchval("SELECT count(*) FROM notifications") == 0
    assert await switchboard_recovery_pool.fetchval("SELECT count(*) FROM message_inbox") == 0
    now = datetime.now(UTC)
    assert (
        await _load_realtime_history(
            switchboard_recovery_pool,
            recipient_sentinel,
            now,
            source_channel="telegram_bot",
        )
        == []
    )
    assert await _load_email_history(switchboard_recovery_pool, recipient_sentinel, now) == []
    assert (
        await _load_conversation_history(
            switchboard_recovery_pool,
            "telegram_bot",
            recipient_sentinel,
            now,
        )
        == ""
    )

    ordinary_payloads = [
        {"content": "ordinary-absent-metadata"},
        {"content": "ordinary-null-metadata", "metadata": None},
        {"content": "ordinary-scalar-metadata", "metadata": "synthetic"},
        {"content": "ordinary-array-metadata", "metadata": ["synthetic"]},
    ]
    expected_content = [payload["content"] for payload in ordinary_payloads]
    for channel, thread in (
        ("telegram_bot", "ordinary-realtime-thread"),
        ("email", "ordinary-email-thread"),
    ):
        for index, payload in enumerate(ordinary_payloads, start=1):
            await switchboard_recovery_pool.execute(
                """
                INSERT INTO message_inbox (
                    received_at, request_context, raw_payload, normalized_text,
                    direction, lifecycle_state, schema_version
                ) VALUES (
                    $1, $2, $3, $4, 'inbound', 'completed', 'message_inbox.v2'
                )
                """,
                now - timedelta(seconds=10 - index),
                {
                    "source_channel": channel,
                    "source_sender_identity": "ordinary-synthetic-sender",
                    "source_thread_identity": thread,
                },
                payload,
                payload["content"],
            )

    realtime = await _load_realtime_history(
        switchboard_recovery_pool,
        "ordinary-realtime-thread",
        now,
        source_channel="telegram_bot",
    )
    email = await _load_email_history(
        switchboard_recovery_pool,
        "ordinary-email-thread",
        now,
    )
    assert [row["raw_content"] for row in realtime] == expected_content
    assert [row["raw_content"] for row in email] == expected_content
    realtime_context = await _load_conversation_history(
        switchboard_recovery_pool,
        "telegram_bot",
        "ordinary-realtime-thread",
        now,
    )
    email_context = await _load_conversation_history(
        switchboard_recovery_pool,
        "email",
        "ordinary-email-thread",
        now,
    )
    for content in expected_content:
        assert content in realtime_context
        assert content in email_context

    recovery_notification_id = await switchboard_recovery_pool.fetchval(
        """
        INSERT INTO notifications (
            source_butler, channel, recipient, message, metadata, status
        ) VALUES ($1, 'telegram', $2, $3, $4, 'failed')
        RETURNING id
        """,
        "relationship",
        recipient_sentinel,
        message_sentinel,
        {"notify_request": notify_request},
    )
    await switchboard_recovery_pool.execute(
        """
        INSERT INTO message_inbox (
            received_at, request_context, raw_payload, normalized_text,
            direction, lifecycle_state, schema_version
        ) VALUES (
            $1, $2, $3, $4, 'outbound', 'completed', 'message_inbox.v2'
        )
        """,
        now - timedelta(seconds=1),
        {
            "source_channel": "telegram_bot",
            "source_sender_identity": "relationship",
            "source_thread_identity": recipient_sentinel,
        },
        {"content": message_sentinel, "metadata": {"approval_recovery": True}},
        message_sentinel,
    )

    assert (
        await _fetch_notification_row(switchboard_recovery_pool, recovery_notification_id) is None
    )
    generic_page = await _query_notifications(
        switchboard_recovery_pool,
        offset=0,
        limit=20,
    )
    assert generic_page.data == []
    fake_db = SimpleNamespace(pool=lambda _name: switchboard_recovery_pool)
    stats = await notification_stats(since=None, until=None, db=fake_db)
    assert stats.data.total == stats.data.sent == stats.data.failed == 0
    cache = SimpleNamespace(invalidate=AsyncMock(), invalidate_all=AsyncMock())
    with pytest.raises(HTTPException) as read_error:
        await mark_notification_read(
            recovery_notification_id,
            db=fake_db,
            cache=cache,
        )
    assert read_error.value.status_code == 404
    acknowledged = await ack_failed_notifications(db=fake_db, cache=cache)
    assert acknowledged.data.acknowledged == 0
    with pytest.raises(ValueError, match="no generic replay envelope"):
        _extract_stored_envelope({"metadata": {"notify_request": notify_request}})

    assert (
        await query_timeline_notifications_single(
            switchboard_recovery_pool,
            limit=20,
        )
        == []
    )
    assert (
        await query_timeline_notification_histogram_single(
            switchboard_recovery_pool,
            since=now - timedelta(hours=1),
            until=now + timedelta(hours=1),
        )
        == []
    )
    attention, attention_count = await query_timeline_attention_notifications_single(
        switchboard_recovery_pool,
        since=now - timedelta(hours=1),
        until=now + timedelta(hours=1),
    )
    assert attention == [] and attention_count == 0
    assert (
        await _load_realtime_history(
            switchboard_recovery_pool,
            recipient_sentinel,
            now,
            source_channel="telegram_bot",
        )
        == []
    )
    assert await _load_email_history(switchboard_recovery_pool, recipient_sentinel, now) == []
    assert (
        await _load_conversation_history(
            switchboard_recovery_pool,
            "telegram_bot",
            recipient_sentinel,
            now,
        )
        == ""
    )


async def test_failed_push_is_retried_once_the_callback_secret_is_fixed(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """bu-mda0r: a push that failed (e.g. missing secret) must not be stuck as
    a permanent 'duplicate' once the underlying problem is fixed. Before this
    fix, ``reserve_approval_push``'s plain ``ON CONFLICT DO NOTHING`` claimed
    the reservation on the first (failing) attempt and every later attempt for
    the same action silently no-opped as 'duplicate' forever, even after the
    secret became available.
    """
    now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    action = await _insert_pending_action(approval_push_pool, requested_at=now)

    broken_credential_store = SimpleNamespace(resolve=AsyncMock(return_value=None))
    dispatch = AsyncMock()
    broken_runtime = ApprovalPushRuntime(
        dispatch=dispatch,
        resolve_owner_recipient=AsyncMock(return_value="100200300"),
        credential_store=broken_credential_store,
    )

    first_outcome = await emit_approval_push(
        pool=approval_push_pool,
        action=action,
        origin_butler="relationship",
        runtime=broken_runtime,
        now=now,
    )
    assert first_outcome == "failed"
    dispatch.assert_not_awaited()

    recorded_outcome = await approval_push_pool.fetchval(
        "SELECT outcome FROM approval_push_emissions WHERE action_id = $1",
        action["id"],
    )
    assert recorded_outcome == "failed"

    # The secret is now provisioned; retry the exact same action.
    fixed_runtime = _runtime(dispatch)
    second_outcome = await emit_approval_push(
        pool=approval_push_pool,
        action=action,
        origin_butler="relationship",
        runtime=fixed_runtime,
        now=now + timedelta(seconds=1),
    )

    assert second_outcome == "delivered"
    dispatch.assert_awaited_once()

    recorded_outcome_after_retry = await approval_push_pool.fetchval(
        "SELECT outcome FROM approval_push_emissions WHERE action_id = $1",
        action["id"],
    )
    assert recorded_outcome_after_retry == "delivered"

    # A THIRD call for the same now-delivered action is a genuine duplicate.
    third_outcome = await emit_approval_push(
        pool=approval_push_pool,
        action=action,
        origin_butler="relationship",
        runtime=fixed_runtime,
        now=now + timedelta(seconds=2),
    )
    assert third_outcome == "duplicate"
    dispatch.assert_awaited_once()


async def test_quiet_hours_defers_push_without_changing_pending_action_expiry(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """Deferral persists the full push envelope while preserving expires_at exactly."""
    now = datetime(2026, 7, 18, 15, 30, tzinfo=UTC)  # 23:30 Asia/Singapore
    action = await _insert_pending_action(approval_push_pool, requested_at=now)
    await approval_push_pool.execute(
        """
        UPDATE public.approvals_policy
        SET quiet_start_hour = 22, quiet_end_hour = 7, timezone = 'Asia/Singapore'
        WHERE id = 1
        """
    )
    dispatch = AsyncMock()

    outcome = await emit_approval_push(
        pool=approval_push_pool,
        action=action,
        origin_butler="relationship",
        runtime=_runtime(dispatch),
        now=now,
    )

    assert outcome == "deferred"
    dispatch.assert_not_awaited()
    deferred = await approval_push_pool.fetchrow(
        "SELECT envelope, priority, status, deferred_at, deliver_at FROM deferred_notifications"
    )
    assert deferred is not None
    assert deferred["priority"] == "high"
    assert deferred["status"] == "pending"
    assert deferred["deferred_at"] == now
    assert deferred["deliver_at"] == datetime(2026, 7, 18, 23, 0, tzinfo=UTC)
    assert deferred["envelope"]["delivery"]["intent"] == "approval_request"

    stored_expiry = await approval_push_pool.fetchval(
        "SELECT expires_at FROM pending_actions WHERE id = $1",
        action["id"],
    )
    assert stored_expiry == action["expires_at"]


async def test_real_database_burst_reservation_emits_one_digest_then_collapses(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """The fourth park emits the sole digest; later parks in-window do not push."""
    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    dispatch = AsyncMock()
    runtime = _runtime(dispatch)
    outcomes: list[str] = []

    for index in range(5):
        action_now = now + timedelta(seconds=index)
        action = await _insert_pending_action(approval_push_pool, requested_at=action_now)
        outcomes.append(
            await emit_approval_push(
                pool=approval_push_pool,
                action=action,
                origin_butler="relationship",
                runtime=runtime,
                now=action_now,
            )
        )

    assert outcomes == ["delivered", "delivered", "delivered", "delivered", "collapsed"]
    assert dispatch.await_count == 4
    fourth_envelope = dispatch.await_args_list[3].args[0]
    assert fourth_envelope["delivery"]["message"].startswith("4 actions awaiting review.")
    assert fourth_envelope["actions"] == [
        {
            "verb": "open_dashboard",
            "dashboard_url": "https://dashboard.example.test/approvals",
        }
    ]

    kinds = await approval_push_pool.fetch(
        "SELECT emission_kind FROM approval_push_emissions ORDER BY created_at"
    )
    assert [row["emission_kind"] for row in kinds] == [
        "single",
        "single",
        "single",
        "burst_digest",
        "collapsed",
    ]
