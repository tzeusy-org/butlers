"""core_310: wake-recovery provenance and protocol states on deferred_notifications.

Bead bu-kqnum.3.6.1 (slice A of strict-owner-telegram-wake-recovery, tasks 1.1/3.1/3.3a).
The unit tests pin only what a migrated database cannot show (the revision chain and the
missing-table skip); every schema invariant is asserted by running the SQL against a migrated
database (Docker; CI-only).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.testing.migration import load_migration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_310_deferred_notification_wake_provenance.py"
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


def _emitted(fn_name: str) -> str:
    mod = load_migration(_MIGRATION_PATH)
    mock_op = MagicMock()
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return "\n".join(call.args[0] for call in mock_op.execute.call_args_list)


@pytest.mark.unit
def test_revision_chain_is_a_single_additive_step() -> None:
    mod = load_migration(_MIGRATION_PATH)

    assert (mod.revision, mod.down_revision) == ("core_310", "core_309")
    assert mod.branch_labels is None and mod.depends_on is None


@pytest.mark.unit
def test_upgrade_skips_a_schema_without_the_table() -> None:
    sql = _emitted("upgrade")

    assert "to_regclass('deferred_notifications') IS NULL" in sql


@pytest.fixture
async def sb_pool(db):
    return await db(
        "core",
        "switchboard",
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        schema="switchboard",
    )


async def _run(sb_pool, sql: str) -> None:
    await sb_pool.execute(sql)


async def _insert(sb_pool, *, status: str = "pending", provenance: bool = False) -> uuid.UUID:
    wake = (
        "'owner_attention_quiet_hours', 'w', nextval('deferred_notifications_admission_seq'),"
        " 'UTC', '{}'::jsonb, 'd', now()"
        if provenance
        else "NULL, NULL, NULL, NULL, NULL, NULL, NULL"
    )
    return await sb_pool.fetchval(
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
async def test_a_migrated_database_has_the_columns_and_a_legacy_row_stays_pending(sb_pool) -> None:
    columns = {
        r["column_name"]
        for r in await sb_pool.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = 'deferred_notifications'"
        )
    }
    assert set(_WAKE_COLUMNS) <= columns

    row_id = await _insert(sb_pool)
    row = await sb_pool.fetchrow("SELECT status, wake_hold_kind FROM deferred_notifications")
    assert row["status"] == "pending" and row["wake_hold_kind"] is None
    assert row_id is not None


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_protocol_state_needs_provenance_and_provenance_is_immutable(sb_pool) -> None:
    import asyncpg

    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(sb_pool, status="release_prepared", provenance=False)
    for status in _PROTOCOL_STATUSES:  # every protocol state is allowed with provenance
        await _insert(sb_pool, status=status, provenance=True)
    with pytest.raises(asyncpg.CheckViolationError):  # provenance is all-or-nothing
        await sb_pool.execute(
            "INSERT INTO deferred_notifications"
            " (butler_name, channel, message, envelope, deliver_at, wake_hold_kind)"
            " VALUES ('b', 'telegram', 'm', '{}'::jsonb, now(), 'owner_attention_quiet_hours')"
        )

    row_id = await _insert(sb_pool, status="release_prepared", provenance=True)
    with pytest.raises(asyncpg.CheckViolationError):
        await sb_pool.execute(
            "UPDATE deferred_notifications SET wake_policy_window_key = 'other' WHERE id = $1",
            row_id,
        )
    legacy_id = await _insert(sb_pool)
    with pytest.raises(asyncpg.CheckViolationError):
        await sb_pool.execute(
            "UPDATE deferred_notifications SET wake_hold_kind = 'owner_attention_quiet_hours'"
            " WHERE id = $1",
            legacy_id,
        )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_then_upgrade_round_trips_on_an_empty_table(sb_pool) -> None:
    import asyncpg

    await _run(sb_pool, _emitted("downgrade"))
    columns = {
        r["column_name"]
        for r in await sb_pool.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = 'deferred_notifications'"
        )
    }
    assert not set(_WAKE_COLUMNS) & columns
    with pytest.raises(asyncpg.CheckViolationError):  # pre-protocol status check is back
        await sb_pool.execute(
            "INSERT INTO deferred_notifications"
            " (butler_name, channel, message, envelope, deliver_at, status)"
            " VALUES ('b', 'telegram', 'm', '{}'::jsonb, now(), 'release_prepared')"
        )

    await _run(sb_pool, _emitted("upgrade"))
    assert await sb_pool.fetchval("SELECT count(*) FROM deferred_notifications") == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_downgrade_refuses_while_a_row_is_in_a_protocol_state(sb_pool) -> None:
    import asyncpg

    await _insert(sb_pool, status="release_committed", provenance=True)

    with pytest.raises(asyncpg.RaiseError, match="downgrade refused"):
        await _run(sb_pool, _emitted("downgrade"))
    assert await sb_pool.fetchval("SELECT count(*) FROM deferred_notifications") == 1
