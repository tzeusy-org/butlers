"""core_272: wake-recovery provenance and protocol states on deferred_notifications.

Bead bu-kqnum.3.6.1 (slice A of strict-owner-telegram-wake-recovery, tasks 1.1/3.1/3.3).
The unit tests read the SQL the migration emits; the integration tests run that same SQL
against a migrated database (Docker; CI-only).
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_272_deferred_notification_wake_provenance.py"
)

_WAKE_COLUMNS = (
    "wake_hold_kind",
    "wake_policy_window_key",
    "wake_admission_sequence",
    "wake_timezone",
    "wake_target",
    "wake_envelope_digest",
    "wake_original_deliver_at",
)
_PROTOCOL_STATUSES = (
    "release_prepared",
    "release_committed",
    "release_retained_dnd",
    "release_retained_unavailable",
    "release_retained_oversize",
    "release_retained_mismatch",
    "release_retained_commit_error",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_272", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _emitted(fn_name: str) -> str:
    mod = _load_migration()
    mock_op = MagicMock()
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return "\n".join(call.args[0] for call in mock_op.execute.call_args_list)


@pytest.mark.unit
def test_revision_chain_is_a_single_additive_step() -> None:
    mod = _load_migration()

    assert (mod.revision, mod.down_revision) == ("core_272", "core_268")
    assert mod.branch_labels is None and mod.depends_on is None


@pytest.mark.unit
def test_upgrade_adds_every_provenance_column_nullable() -> None:
    sql = _emitted("upgrade")

    for column in _WAKE_COLUMNS:
        assert f"ADD COLUMN IF NOT EXISTS {column} " in sql
    assert "NOT NULL" not in sql.split("ADD CONSTRAINT")[0]
    assert "DEFAULT" not in sql.split("ADD CONSTRAINT")[0]


@pytest.mark.unit
def test_upgrade_allows_every_protocol_state_but_only_on_a_row_with_provenance() -> None:
    sql = _emitted("upgrade")

    for status in _PROTOCOL_STATUSES:
        assert f"'{status}'" in sql
    assert "'pending', 'delivered', 'expired', 'cancelled'" in sql
    assert "OR wake_hold_kind IS NOT NULL" in sql


@pytest.mark.unit
def test_provenance_is_all_or_nothing_and_immutable() -> None:
    sql = _emitted("upgrade")

    assert "chk_deferred_notifications_wake_provenance" in sql
    assert "wake_hold_kind = 'owner_attention_quiet_hours'" in sql
    assert "BEFORE UPDATE ON deferred_notifications" in sql
    assert "FOR EACH ROW EXECUTE FUNCTION" in sql
    check = sql[sql.index("ADD CONSTRAINT chk_deferred_notifications_wake_provenance") :]
    check = check[: check.index(";")]
    for column in _WAKE_COLUMNS:
        assert f"{column} IS NULL" in check
        assert f"{column} IS NOT NULL" in check
    for column in _WAKE_COLUMNS:
        assert f"NEW.{column} IS DISTINCT FROM OLD.{column}" in sql


@pytest.mark.unit
def test_upgrade_skips_a_schema_without_the_table() -> None:
    sql = _emitted("upgrade")

    assert "to_regclass('deferred_notifications') IS NULL" in sql


@pytest.mark.unit
def test_downgrade_refuses_before_dropping_anything() -> None:
    sql = _emitted("downgrade")

    refusal = sql.index("RAISE EXCEPTION")
    assert sql.index("status IN ('release_prepared'") < refusal
    assert refusal < sql.index("DROP TRIGGER")
    assert refusal < sql.index("DROP COLUMN")
    assert refusal < sql.index("DROP SEQUENCE")


@pytest.mark.unit
def test_downgrade_restores_the_pre_protocol_status_check() -> None:
    sql = _emitted("downgrade")

    restored = sql[sql.rindex("ADD CONSTRAINT chk_deferred_notifications_status") :]
    assert "'pending', 'delivered', 'expired', 'cancelled'" in restored
    assert "release_" not in restored.split("DROP COLUMN")[0]


@pytest.fixture
async def pool(postgres_container):
    from butlers.testing.migration import create_migrated_test_pool

    p = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
        pool_schema="switchboard",
    )
    try:
        yield p
    finally:
        await p.close()


async def _run(pool, sql: str) -> None:
    await pool.execute(sql)


async def _insert(pool, *, status: str = "pending", provenance: bool = False) -> uuid.UUID:
    wake = (
        "'owner_attention_quiet_hours', 'w', nextval('deferred_notifications_admission_seq'),"
        " 'UTC', '{}'::jsonb, 'd', now()"
        if provenance
        else "NULL, NULL, NULL, NULL, NULL, NULL, NULL"
    )
    return await pool.fetchval(
        f"""
        INSERT INTO deferred_notifications
            (butler_name, channel, message, envelope, deliver_at, status, {", ".join(_WAKE_COLUMNS)})
        VALUES ('b', 'telegram', 'm', '{{}}'::jsonb, now(), $1, {wake})
        RETURNING id
        """,
        status,
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_a_migrated_database_has_the_columns_and_a_legacy_row_stays_pending(pool) -> None:
    columns = {
        r["column_name"]
        for r in await pool.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = 'deferred_notifications'"
        )
    }
    assert set(_WAKE_COLUMNS) <= columns

    row_id = await _insert(pool)
    row = await pool.fetchrow("SELECT status, wake_hold_kind FROM deferred_notifications")
    assert row["status"] == "pending" and row["wake_hold_kind"] is None
    assert row_id is not None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_protocol_state_needs_provenance_and_provenance_is_immutable(pool) -> None:
    import asyncpg

    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(pool, status="release_prepared", provenance=False)

    row_id = await _insert(pool, status="release_prepared", provenance=True)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "UPDATE deferred_notifications SET wake_policy_window_key = 'other' WHERE id = $1",
            row_id,
        )
    legacy_id = await _insert(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "UPDATE deferred_notifications SET wake_hold_kind = 'owner_attention_quiet_hours'"
            " WHERE id = $1",
            legacy_id,
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_then_upgrade_round_trips_on_an_empty_table(pool) -> None:
    await _run(pool, _emitted("downgrade"))
    columns = {
        r["column_name"]
        for r in await pool.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = 'deferred_notifications'"
        )
    }
    assert not set(_WAKE_COLUMNS) & columns

    await _run(pool, _emitted("upgrade"))
    assert await pool.fetchval("SELECT count(*) FROM deferred_notifications") == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_refuses_while_a_row_is_in_a_protocol_state(pool) -> None:
    import asyncpg

    await _insert(pool, status="release_committed", provenance=True)

    with pytest.raises(asyncpg.RaiseError, match="downgrade refused"):
        await _run(pool, _emitted("downgrade"))
    assert await pool.fetchval("SELECT count(*) FROM deferred_notifications") == 1
