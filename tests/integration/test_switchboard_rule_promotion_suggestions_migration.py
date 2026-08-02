"""Real-Postgres regression: switchboard.rule_promotion_suggestions (bu-h26o9, bead 2 of 7).

Exercises migration ``sw_020`` (rule-promotion bead 2 —
``docs/plans/2026-07-06-switchboard-rule-promotion-design.md`` section 2 and
the merged openspec change ``switchboard-rule-promotion``) against a fully
migrated Postgres instance (testcontainers):

- ``rule_promotion_suggestions`` exists with the expected columns and indexes.
- The ``suggestion_kind`` / ``status`` / ``proposed_rule_type`` CHECK
  constraints match the fixed vocabularies.
- The ``chk_rule_promotion_suggestions_kind_shape`` constraint enforces that
  column population mirrors ``suggestion_kind`` (promotion rows carry the
  proposed-rule triple and no ``target_rule_id``; demotion rows carry only
  ``target_rule_id`` plus evidence and leave sampled sender/channel and
  proposed-rule fields NULL).
- The unique partial indexes prevent a second pending suggestion per
  sender/channel (promotion) or per rule (demotion).
- ``target_rule_id`` / ``created_rule_id`` FKs to ``ingestion_rules`` are
  enforced.
- ``ingestion_rules.promoted_from_suggestion_id`` round-trips a link back to
  the originating suggestion, for both a promotion-created rule and a
  pre-existing rule targeted by a demotion suggestion.
- Downgrade cleanly drops the table, its indexes, and the
  ``ingestion_rules`` column/index it added.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.db import register_jsonb_codec
from butlers.testing.migration import (
    create_migrated_test_db,
    create_migration_db,
    index_exists,
    migration_db_name,
    table_exists,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_DEMOTION_IDENTITY_SHAPE_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "switchboard"
    / "migrations"
    / "027_switchboard_demotion_identity_shape.py"
)


def _load_demotion_identity_shape_migration():
    spec = importlib.util.spec_from_file_location(
        "sw_027_demotion_identity_shape", _DEMOTION_IDENTITY_SHAPE_MIGRATION
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collect_demotion_identity_upgrade_sql() -> list[str]:
    module = _load_demotion_identity_shape_migration()
    statements: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = statements.append

    with patch.object(module, "op", mock_op):
        module.upgrade()

    return statements


def _normalize_sql(statement: str) -> str:
    return " ".join(statement.split()).upper()


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


async def _insert_ingestion_rule(pool: asyncpg.Pool, *, priority: int = 100) -> uuid.UUID:
    rule_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO ingestion_rules (id, scope, rule_type, condition, action, priority)
        VALUES ($1, 'global', 'sender_domain', '{"domain": "chase.com"}'::jsonb,
                'route_to:finance', $2)
        """,
        rule_id,
        priority,
    )
    return rule_id


async def _insert_promotion_suggestion(
    pool: asyncpg.Pool,
    *,
    sender_key: str = "billing@chase.com",
    source_channel: str = "email",
    status: str = "pending_review",
) -> uuid.UUID:
    row_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO rule_promotion_suggestions
            (id, suggestion_kind, sender_key, source_channel, proposed_rule_type,
             proposed_condition, proposed_action, evidence_count, first_evidence_at,
             last_evidence_at, status)
        VALUES
            ($1, 'promotion', $2, $3, 'sender_address',
             '{"address": "billing@chase.com"}'::jsonb, 'route_to:finance', 3,
             now() - interval '2 days', now(), $4)
        """,
        row_id,
        sender_key,
        source_channel,
        status,
    )
    return row_id


async def _insert_demotion_suggestion(
    pool: asyncpg.Pool,
    *,
    target_rule_id: uuid.UUID,
    status: str = "pending_review",
) -> uuid.UUID:
    row_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO rule_promotion_suggestions
            (id, suggestion_kind, target_rule_id, evidence_count, status)
        VALUES ($1, 'demotion', $2, 5, $3)
        """,
        row_id,
        target_rule_id,
        status,
    )
    return row_id


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


def test_rule_promotion_suggestions_table_exists_with_expected_columns(
    migrated_db_url: str,
) -> None:
    assert table_exists(migrated_db_url, "rule_promotion_suggestions", schema="switchboard")

    engine = create_engine(migrated_db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'switchboard' AND table_name = 'rule_promotion_suggestions'"
            )
        ).fetchall()
    engine.dispose()

    columns = {r[0] for r in rows}
    assert columns == {
        "id",
        "suggestion_kind",
        "sender_key",
        "source_channel",
        "proposed_rule_type",
        "proposed_condition",
        "proposed_action",
        "evidence_count",
        "first_evidence_at",
        "last_evidence_at",
        "is_clearly_automated",
        "status",
        "target_rule_id",
        "created_rule_id",
        "dismissal_reason",
        "cooldown_until",
        "created_at",
        "decided_at",
        "decided_by",
    }


def test_expected_indexes_exist(migrated_db_url: str) -> None:
    assert index_exists(
        migrated_db_url, "ix_rule_promotion_suggestions_status_created", schema="switchboard"
    )
    assert index_exists(
        migrated_db_url, "ux_rule_promotion_suggestions_pending_promotion", schema="switchboard"
    )
    assert index_exists(
        migrated_db_url, "ux_rule_promotion_suggestions_pending_demotion", schema="switchboard"
    )
    assert index_exists(
        migrated_db_url, "ix_rule_promotion_suggestions_target_rule", schema="switchboard"
    )


def test_ingestion_rules_gains_promoted_from_suggestion_id_column(migrated_db_url: str) -> None:
    engine = create_engine(migrated_db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'switchboard' AND table_name = 'ingestion_rules' "
                "AND column_name = 'promoted_from_suggestion_id'"
            )
        ).fetchall()
    engine.dispose()
    assert len(rows) == 1
    assert index_exists(
        migrated_db_url, "ix_ingestion_rules_promoted_from_suggestion", schema="switchboard"
    )


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_suggestion_kind_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, sender_key, source_channel, proposed_rule_type,
                 proposed_condition, proposed_action)
            VALUES ('bogus', 'user@example.com', 'email', 'sender_address',
                    '{}'::jsonb, 'skip')
            """
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_status_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    rule_id = await _insert_ingestion_rule(pool, priority=101)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, target_rule_id, status)
            VALUES ('demotion', $1, 'ghosted')
            """,
            rule_id,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_proposed_rule_type_check_constraint_rejects_bogus_value(
    pool: asyncpg.Pool,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, sender_key, source_channel, proposed_rule_type,
                 proposed_condition, proposed_action)
            VALUES ('promotion', 'user@example.com', 'email', 'mime_type',
                    '{}'::jsonb, 'skip')
            """
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_proposed_rule_type_check_accepts_source_endpoint(pool: asyncpg.Pool) -> None:
    """The latest schema can persist an exact opaque connector identity proposal."""
    await pool.execute(
        """
        INSERT INTO rule_promotion_suggestions
            (suggestion_kind, sender_key, source_channel, proposed_rule_type,
             proposed_condition, proposed_action)
        VALUES ('promotion', 'spotify:acct-1', 'music', 'source_endpoint',
                '{"endpoint_identity": "spotify:acct-1"}'::jsonb, 'route_to:lifestyle')
        """
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_kind_shape_check_rejects_promotion_row_missing_fields(
    pool: asyncpg.Pool,
) -> None:
    """A promotion-kind row must carry the full proposed-rule triple."""
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions (suggestion_kind, sender_key, source_channel)
            VALUES ('promotion', 'user@example.com', 'email')
            """
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_kind_shape_check_rejects_promotion_row_with_target_rule_id(
    pool: asyncpg.Pool,
) -> None:
    """A promotion-kind row must not carry target_rule_id (that's demotion-only)."""
    rule_id = await _insert_ingestion_rule(pool, priority=102)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, sender_key, source_channel, proposed_rule_type,
                 proposed_condition, proposed_action, target_rule_id)
            VALUES ('promotion', 'user@example.com', 'email', 'sender_address',
                    '{}'::jsonb, 'skip', $1)
            """,
            rule_id,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_kind_shape_check_rejects_demotion_row_missing_target_rule_id(
    pool: asyncpg.Pool,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO rule_promotion_suggestions (suggestion_kind) VALUES ('demotion')"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_kind_shape_check_rejects_demotion_row_with_proposed_action(
    pool: asyncpg.Pool,
) -> None:
    rule_id = await _insert_ingestion_rule(pool, priority=103)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, target_rule_id, proposed_action)
            VALUES ('demotion', $1, 'skip')
            """,
            rule_id,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_kind_shape_check_rejects_demotion_row_with_sender_key(
    pool: asyncpg.Pool,
) -> None:
    """Demotion scope comes from target_rule_id, not one sampled sender."""
    rule_id = await _insert_ingestion_rule(pool, priority=106)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, target_rule_id, sender_key)
            VALUES ('demotion', $1, 'sample@example.com')
            """,
            rule_id,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_kind_shape_check_rejects_demotion_row_with_source_channel(
    pool: asyncpg.Pool,
) -> None:
    """Demotion scope comes from target_rule_id, not one sampled channel."""
    rule_id = await _insert_ingestion_rule(pool, priority=107)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, target_rule_id, source_channel)
            VALUES ('demotion', $1, 'email')
            """,
            rule_id,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_kind_shape_check_rejects_promotion_row_with_empty_string_sender_key(
    pool: asyncpg.Pool,
) -> None:
    """Empty string is NOT NULL but is just as vacuous as NULL for a required
    identity field — the CHECK must reject it, not just a missing value."""
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, sender_key, source_channel, proposed_rule_type,
                 proposed_condition, proposed_action)
            VALUES ('promotion', '', 'email', 'sender_address', '{}'::jsonb, 'skip')
            """
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_kind_shape_check_rejects_promotion_row_with_empty_string_proposed_action(
    pool: asyncpg.Pool,
) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, sender_key, source_channel, proposed_rule_type,
                 proposed_condition, proposed_action)
            VALUES ('promotion', 'user@example.com', 'email', 'sender_address',
                    '{}'::jsonb, '')
            """
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_target_rule_id_fk_is_enforced(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions (suggestion_kind, target_rule_id)
            VALUES ('demotion', $1)
            """,
            uuid.uuid4(),
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_created_rule_id_fk_is_enforced(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(
            """
            INSERT INTO rule_promotion_suggestions
                (suggestion_kind, sender_key, source_channel, proposed_rule_type,
                 proposed_condition, proposed_action, created_rule_id)
            VALUES ('promotion', 'user@example.com', 'email', 'sender_address',
                    '{}'::jsonb, 'skip', $1)
            """,
            uuid.uuid4(),
        )


# ---------------------------------------------------------------------------
# Unique partial indexes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_second_pending_promotion_suggestion_for_same_sender_channel_rejected(
    pool: asyncpg.Pool,
) -> None:
    await _insert_promotion_suggestion(pool, sender_key="dup@example.com", source_channel="email")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_promotion_suggestion(
            pool, sender_key="dup@example.com", source_channel="email"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_second_pending_demotion_suggestion_for_same_rule_rejected(
    pool: asyncpg.Pool,
) -> None:
    rule_id = await _insert_ingestion_rule(pool, priority=104)
    await _insert_demotion_suggestion(pool, target_rule_id=rule_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_demotion_suggestion(pool, target_rule_id=rule_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_dismissed_promotion_suggestion_does_not_block_new_pending_one(
    pool: asyncpg.Pool,
) -> None:
    """The unique index is partial (WHERE status = 'pending_review') — a
    dismissed suggestion for the same sender/channel must not block a fresh
    pending one from being created."""
    await _insert_promotion_suggestion(
        pool, sender_key="retry@example.com", source_channel="email", status="dismissed"
    )
    # Should not raise.
    await _insert_promotion_suggestion(
        pool, sender_key="retry@example.com", source_channel="email", status="pending_review"
    )


# ---------------------------------------------------------------------------
# Round-trip: promotion confirm + demotion targeting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_confirming_a_promotion_suggestion_links_created_rule(pool: asyncpg.Pool) -> None:
    suggestion_id = await _insert_promotion_suggestion(
        pool, sender_key="confirm@chase.com", source_channel="email"
    )

    new_rule_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO ingestion_rules
            (id, scope, rule_type, condition, action, priority, created_by)
        VALUES ($1, 'global', 'sender_address', '{"address": "confirm@chase.com"}'::jsonb,
                'route_to:finance', 200, 'promotion')
        """,
        new_rule_id,
    )
    await pool.execute(
        """
        UPDATE rule_promotion_suggestions
        SET status = 'confirmed', created_rule_id = $1, decided_at = now(), decided_by = 'owner'
        WHERE id = $2
        """,
        new_rule_id,
        suggestion_id,
    )
    await pool.execute(
        "UPDATE ingestion_rules SET promoted_from_suggestion_id = $1 WHERE id = $2",
        suggestion_id,
        new_rule_id,
    )

    suggestion_row = await pool.fetchrow(
        "SELECT * FROM rule_promotion_suggestions WHERE id = $1", suggestion_id
    )
    rule_row = await pool.fetchrow("SELECT * FROM ingestion_rules WHERE id = $1", new_rule_id)

    assert suggestion_row["status"] == "confirmed"
    assert suggestion_row["created_rule_id"] == new_rule_id
    assert rule_row["created_by"] == "promotion"
    assert rule_row["promoted_from_suggestion_id"] == suggestion_id


@pytest.mark.asyncio(loop_scope="session")
async def test_demotion_suggestion_targets_existing_rule(pool: asyncpg.Pool) -> None:
    rule_id = await _insert_ingestion_rule(pool, priority=105)
    suggestion_id = await _insert_demotion_suggestion(pool, target_rule_id=rule_id)

    row = await pool.fetchrow(
        "SELECT * FROM rule_promotion_suggestions WHERE id = $1", suggestion_id
    )
    assert row["suggestion_kind"] == "demotion"
    assert row["target_rule_id"] == rule_id
    assert row["sender_key"] is None
    assert row["source_channel"] is None
    assert row["proposed_action"] is None
    assert row["created_rule_id"] is None


# ---------------------------------------------------------------------------
# Upgrade data normalization
# ---------------------------------------------------------------------------


def test_upgrade_locks_writers_immediately_before_normalizing_legacy_demotion_rows() -> None:
    """The lock spans cleanup and the stricter CHECK replacement.

    ``UPDATE`` alone permits a concurrent writer to insert a still-valid
    pre-sw_027 demotion row between cleanup and the new CHECK.  The migration
    must acquire the writer-conflicting table lock as the immediately preceding
    statement, then retain it through the constraint replacement transaction.
    """
    statements = [
        _normalize_sql(statement) for statement in _collect_demotion_identity_upgrade_sql()
    ]
    lock_statement = "LOCK TABLE RULE_PROMOTION_SUGGESTIONS IN SHARE ROW EXCLUSIVE MODE"

    assert lock_statement in statements
    lock_index = statements.index(lock_statement)
    cleanup_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("UPDATE RULE_PROMOTION_SUGGESTIONS")
    )
    constraint_drop_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith(
            "ALTER TABLE RULE_PROMOTION_SUGGESTIONS "
            "DROP CONSTRAINT CHK_RULE_PROMOTION_SUGGESTIONS_KIND_SHAPE"
        )
    )

    assert cleanup_index == lock_index + 1
    assert cleanup_index < constraint_drop_index


def test_upgrade_clears_legacy_demotion_identity_fields(postgres_container) -> None:
    """The forward migration removes stray sampled identity data before pinning NULL."""
    from butlers.migrations import _build_alembic_config, run_migrations

    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    command.upgrade(config, "switchboard@sw_026")

    rule_id = uuid.uuid4()
    suggestion_id = uuid.uuid4()
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO switchboard.ingestion_rules
                        (id, scope, rule_type, condition, action, priority, created_by)
                    VALUES
                        (:rule_id, 'global', 'sender_domain',
                         '{"domain": "example.com"}'::jsonb, 'route_to:finance',
                         300, 'migration_test')
                    """
                ),
                {"rule_id": rule_id},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO switchboard.rule_promotion_suggestions
                        (id, suggestion_kind, sender_key, source_channel, target_rule_id,
                         evidence_count, status)
                    VALUES
                        (:suggestion_id, 'demotion', 'sample@example.com', 'email',
                         :rule_id, 5, 'pending_review')
                    """
                ),
                {"suggestion_id": suggestion_id, "rule_id": rule_id},
            )

        command.upgrade(config, "switchboard@head")

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT sender_key, source_channel
                    FROM switchboard.rule_promotion_suggestions
                    WHERE id = :suggestion_id
                    """
                ),
                {"suggestion_id": suggestion_id},
            ).one()
    finally:
        engine.dispose()

    assert tuple(row) == (None, None)


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_drops_table_indexes_and_ingestion_rules_column(
    postgres_container,
) -> None:
    from butlers.migrations import _build_alembic_config

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )

    config = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
    command.downgrade(config, "switchboard@sw_019")

    assert not table_exists(db_url, "rule_promotion_suggestions", schema="switchboard")
    assert not index_exists(
        db_url, "ix_rule_promotion_suggestions_status_created", schema="switchboard"
    )
    assert not index_exists(
        db_url, "ux_rule_promotion_suggestions_pending_promotion", schema="switchboard"
    )
    assert not index_exists(
        db_url, "ux_rule_promotion_suggestions_pending_demotion", schema="switchboard"
    )
    assert not index_exists(
        db_url, "ix_rule_promotion_suggestions_target_rule", schema="switchboard"
    )
    assert not index_exists(
        db_url, "ix_ingestion_rules_promoted_from_suggestion", schema="switchboard"
    )

    engine = create_engine(db_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'switchboard' AND table_name = 'ingestion_rules' "
                "AND column_name = 'promoted_from_suggestion_id'"
            )
        ).fetchall()
    engine.dispose()
    assert len(rows) == 0


def test_source_endpoint_rows_block_data_losing_downgrade_to_sw_028(
    postgres_container,
) -> None:
    """The source-endpoint migration must refuse to erase a live suggestion."""
    from butlers.migrations import _build_alembic_config

    db_url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )
    suggestion_id = uuid.uuid4()
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO switchboard.rule_promotion_suggestions
                        (id, suggestion_kind, sender_key, source_channel,
                         proposed_rule_type, proposed_condition, proposed_action,
                         evidence_count, first_evidence_at, last_evidence_at, status)
                    VALUES
                        (:id, 'promotion', 'spotify:acct-1', 'music',
                         'source_endpoint', '{"endpoint_identity":"spotify:acct-1"}'::jsonb,
                         'route_to:lifestyle', 3, now() - interval '2 days', now(),
                         'pending_review')
                    """
                ),
                {"id": suggestion_id},
            )

        config = _build_alembic_config(db_url, chains=["switchboard"], target_schema="switchboard")
        with pytest.raises(Exception):
            command.downgrade(config, "switchboard@sw_028")

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT proposed_rule_type FROM switchboard.rule_promotion_suggestions "
                    "WHERE id = :id"
                ),
                {"id": suggestion_id},
            ).one()
    finally:
        engine.dispose()

    assert row[0] == "source_endpoint"
