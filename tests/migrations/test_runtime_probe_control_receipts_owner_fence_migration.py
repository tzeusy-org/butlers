"""core_212: fence the runtime-probe receipt table's OWNER too (bu-jcym4).

Covers REQ-database-security-008.  ``core_201`` ENABLEd row security on
``public.runtime_probe_control_receipts`` but deliberately did not FORCE it,
because FORCE would also apply the policy to the table OWNER and, at the
time, the owner was only understood as a backup concern
(``deploy/backup/pg_dump.sh`` dumps as that identity).

``bu-0uqgo.7``'s spec-to-code reconciliation found the owner is also the
identity the Dashboard API pool connects as: ``src/butlers/api/deps.py``
disables ``SET ROLE`` for every API-managed pool, so the Dashboard queries
this table as the same owner ``pg_dump`` uses, and ``ENABLE`` without
``FORCE`` exempts that principal from the policy meant to gate this table to
``butler_switchboard_rw`` alone --
``tests/migrations/test_runtime_probe_control_receipts_migration.py``'s
former ``test_the_nightly_backup_can_still_read_the_table`` proved the owner
could read receipts RLS is supposed to hide.

This revision closes two independent gaps:

1. FORCE ROW LEVEL SECURITY, paired with excluding this table from
   ``deploy/backup/pg_dump.sh`` (the established pattern for RLS-fenced
   tables the dump role cannot read -- see that script's header).
2. A ``BEFORE TRUNCATE ... FOR EACH STATEMENT`` trigger.  RLS does not gate
   TRUNCATE at all, and the owner cannot be stripped of the implicit TRUNCATE
   right PostgreSQL grants every table owner, so core_201's ``FOR EACH ROW``
   retention trigger (which TRUNCATE never fires) was not enough to stop the
   owner from reopening every live replay window with one statement.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.core.runtime_probe_control.receipts import nonce_digest
from butlers.testing.migration import create_migrated_test_db, migration_db_name

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_212_runtime_probe_control_receipts_owner_fence.py"
)

_TABLE = "public.runtime_probe_control_receipts"
_AUDIENCE = "switchboard.runtime_probe_control.v1"
_SWITCHBOARD = "butler_switchboard_rw"

docker_available = shutil.which("docker") is not None
_integration = pytest.mark.skipif(not docker_available, reason="Docker not available")
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_212", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed_sql(function_name: str) -> str:
    module = _load_migration()
    op = MagicMock()
    with patch.object(module, "op", op):
        getattr(module, function_name)()
    return "\n".join(str(call.args[0]) for call in op.execute.call_args_list)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_row_security_is_forced() -> None:
    sql = _executed_sql("upgrade")

    assert f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY" in sql


def test_truncate_trigger_fires_for_every_statement_not_every_row() -> None:
    """FOR EACH STATEMENT is what makes this fire for TRUNCATE at all."""
    sql = _executed_sql("upgrade")

    assert "CREATE OR REPLACE FUNCTION public.runtime_probe_control_receipts_no_truncate" in sql
    assert "RAISE EXCEPTION" in sql
    trigger = sql[sql.index("CREATE TRIGGER trg_runtime_probe_control_receipts_no_truncate") :]
    assert f"BEFORE TRUNCATE ON {_TABLE}" in trigger
    assert "FOR EACH STATEMENT" in trigger


def test_downgrade_removes_everything_it_installed() -> None:
    sql = _executed_sql("downgrade")

    assert "DROP TRIGGER IF EXISTS trg_runtime_probe_control_receipts_no_truncate" in sql
    assert "DROP FUNCTION IF EXISTS public.runtime_probe_control_receipts_no_truncate" in sql
    assert f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY" in sql


# ---------------------------------------------------------------------------
# Live boundary
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def core_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


async def _connect(db_url: str, *, role: str | None = None) -> asyncpg.Connection:
    connection = await asyncpg.connect(db_url)
    if role is not None:
        await connection.execute(f'SET ROLE "{role}"')
    return connection


def _synthetic_nonce() -> bytes:
    return os.urandom(32)


@_integration
@_asyncio_session
async def test_owner_can_no_longer_read_with_row_security_off(core_db_url: str) -> None:
    """The exact gap bu-jcym4 closes: the owner is now subject to the policy."""
    owner = await _connect(core_db_url)
    try:
        await owner.execute("SET row_security = off")
        with pytest.raises(asyncpg.PostgresError) as raised:
            await owner.fetchval(f"SELECT count(*) FROM {_TABLE}")
        assert "row-level security" in str(raised.value).lower()
    finally:
        await owner.close()


@_integration
@_asyncio_session
async def test_owner_sees_zero_rows_under_ordinary_row_security(core_db_url: str) -> None:
    """With row_security left on (pg_dump's default off aside), the owner reads
    zero rows rather than erroring -- FORCE applies the same USING clause to it
    that switchboard already had."""
    switchboard = await _connect(core_db_url, role=_SWITCHBOARD)
    digest = nonce_digest(_synthetic_nonce())
    try:
        await switchboard.execute(
            f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
            "VALUES ($1, $2, $3, $4)",
            _AUDIENCE,
            digest,
            "probe-2026-05a",
            datetime.now(UTC) + timedelta(seconds=60),
        )
    finally:
        await switchboard.close()

    owner = await _connect(core_db_url)
    try:
        visible = await owner.fetchval(
            f"SELECT count(*) FROM {_TABLE} WHERE nonce_digest = $1", digest
        )
    finally:
        await owner.close()

    assert visible == 0


@_integration
@_asyncio_session
async def test_switchboard_is_unaffected_by_the_owner_fence(core_db_url: str) -> None:
    """The gap being closed is the owner's exemption, not switchboard's access."""
    switchboard = await _connect(core_db_url, role=_SWITCHBOARD)
    digest = nonce_digest(_synthetic_nonce())
    try:
        await switchboard.execute(
            f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
            "VALUES ($1, $2, $3, $4)",
            _AUDIENCE,
            digest,
            "probe-2026-05a",
            datetime.now(UTC) + timedelta(seconds=60),
        )
        visible = await switchboard.fetchval(
            f"SELECT count(*) FROM {_TABLE} WHERE nonce_digest = $1", digest
        )
    finally:
        await switchboard.close()

    assert visible == 1


@_integration
@_asyncio_session
async def test_owner_cannot_truncate_the_table(core_db_url: str) -> None:
    """TRUNCATE bypasses RLS entirely and the owner cannot be stripped of the
    implicit right to it, so only a trigger can close this path."""
    owner = await _connect(core_db_url)
    try:
        with pytest.raises(asyncpg.RaiseError):
            await owner.execute(f"TRUNCATE {_TABLE}")
    finally:
        await owner.close()


@_integration
@_asyncio_session
async def test_switchboard_cannot_truncate_the_table_either(core_db_url: str) -> None:
    """Switchboard was never granted TRUNCATE (core_201 only granted SELECT,
    INSERT, DELETE), so this is belt-and-suspenders against the same reopened
    replay window."""
    switchboard = await _connect(core_db_url, role=_SWITCHBOARD)
    try:
        with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.RaiseError)):
            await switchboard.execute(f"TRUNCATE {_TABLE}")
    finally:
        await switchboard.close()


@_integration
@_asyncio_session
async def test_a_live_receipt_still_cannot_be_deleted_by_delete(core_db_url: str) -> None:
    """The owner fence must not disturb core_201's per-row retention trigger."""
    switchboard = await _connect(core_db_url, role=_SWITCHBOARD)
    digest = nonce_digest(_synthetic_nonce())
    try:
        await switchboard.execute(
            f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
            "VALUES ($1, $2, $3, $4)",
            _AUDIENCE,
            digest,
            "probe-2026-05a",
            datetime.now(UTC) + timedelta(seconds=60),
        )

        with pytest.raises(asyncpg.RaiseError):
            await switchboard.execute(f"DELETE FROM {_TABLE} WHERE nonce_digest = $1", digest)
    finally:
        await switchboard.close()
