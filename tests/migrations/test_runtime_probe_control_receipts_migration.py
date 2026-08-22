"""core_201: the runtime-probe replay receipt is Switchboard-owned.

Covers REQ-database-security-008 (Runtime-Probe Replay Receipt Is
Switchboard-Owned).  Signature checking alone cannot stop a replay --- two
copies of one valid capability both verify --- so the durable unique receipt
is the thing that makes single-use real.  These tests pin the two properties
that keep it meaningful: exactly one insert can win per nonce, and no role
other than Switchboard can read, write, or quietly drop receipts.

The structural tests parse the migration.  The privilege tests run the real
chain against a disposable PostgreSQL container and exercise each boundary
through ``SET ROLE``, which is the same effective-role mechanism production
uses --- with the same caveat REQ-database-security-007 records: in the shared
login topology a role is an effective identity, not an unforgeable principal.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.core.runtime_probe_control.receipts import (
    RECEIPT_RETENTION_SKEW,
    RuntimeProbeControlReceipts,
    nonce_digest,
)
from butlers.core.runtime_probe_control.verification import RuntimeProbeVerificationPersistence
from butlers.testing.migration import create_migrated_test_db, migration_db_name

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_201_runtime_probe_control_receipts.py"
)

_TABLE = "public.runtime_probe_control_receipts"
_AUDIENCE = "switchboard.runtime_probe_control.v1"
_SWITCHBOARD = "butler_switchboard_rw"
_OTHER_BUTLER = "butler_general_rw"
_CONNECTOR = "connector_writer"

docker_available = shutil.which("docker") is not None
_integration = pytest.mark.skipif(not docker_available, reason="Docker not available")
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_201", _MIGRATION_PATH)
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


def test_revision_chains_onto_the_current_core_head() -> None:
    module = _load_migration()

    assert module.revision == "core_201"
    assert module.down_revision == "core_200"
    assert module.branch_labels is None
    assert module.depends_on is None


def test_receipt_keeps_only_what_the_replay_decision_needs() -> None:
    """A leaked receipt table must not rebuild into a usable capability."""
    sql = _executed_sql("upgrade")

    assert f"CREATE TABLE IF NOT EXISTS {_TABLE}" in sql
    assert "nonce_digest   BYTEA       NOT NULL" in sql
    assert "capability_exp TIMESTAMPTZ NOT NULL" in sql
    # The raw nonce and the signature are deliberately absent.
    assert "nonce          " not in sql
    assert "signature" not in sql
    assert "payload" not in sql


def test_uniqueness_is_the_replay_decision() -> None:
    sql = _executed_sql("upgrade")

    assert "CONSTRAINT pk_runtime_probe_control_receipts" in sql
    assert "PRIMARY KEY (audience, nonce_digest)" in sql
    assert "CHECK (audience = 'switchboard.runtime_probe_control.v1')" in sql
    assert "CHECK (octet_length(nonce_digest) = 32)" in sql


def test_grants_are_revoked_before_switchboard_gets_its_own() -> None:
    """Order matters: ``init-db`` hands every runtime role DML at CREATE TABLE."""
    sql = _executed_sql("upgrade")

    revoke_public = sql.index(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM PUBLIC")
    grant_switchboard = sql.index(f"GRANT SELECT, INSERT, DELETE ON TABLE {_TABLE}")
    assert revoke_public < grant_switchboard

    for role in (_OTHER_BUTLER, _CONNECTOR):
        assert f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {role}" in sql
    # No UPDATE: a receipt is append-only until its retention bound elapses.
    assert "UPDATE ON TABLE" not in sql


def test_row_security_is_enabled_but_deliberately_not_forced() -> None:
    """Row security survives an init-db re-grant; FORCE would break the backup.

    ENABLE is what makes the fence durable: ``scripts/init-db.sql`` re-grants
    public-schema DML to every runtime role on each run, and a policy is not a
    grant, so it is not re-granted away.

    FORCE is deliberately absent.  It would also fence the table OWNER, and the
    owner is the identity ``deploy/backup/pg_dump.sh`` dumps as -- see
    ``test_forcing_row_security_would_break_that_backup``.  The owner is
    therefore NOT fenced by this migration; owner-fencing needs a mechanism
    that does not sit in the backup path.
    """
    sql = _executed_sql("upgrade")

    assert f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" not in sql
    assert f"USING (current_user = '{_SWITCHBOARD}')" in sql
    assert f"WITH CHECK (current_user = '{_SWITCHBOARD}')" in sql


def test_verification_function_cannot_reach_breaker_state() -> None:
    """A probe writes verification evidence and nothing else."""
    sql = _executed_sql("upgrade")

    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    update = sql[sql.index("UPDATE public.model_catalog") : sql.index("RETURN FOUND")]
    assert "enabled" not in update
    assert "priority" not in update
    assert "breaker" not in update
    assert "REVOKE ALL PRIVILEGES ON FUNCTION" in sql
    assert sql.index("REVOKE ALL PRIVILEGES ON FUNCTION") < sql.index("GRANT EXECUTE ON FUNCTION")


def test_downgrade_removes_everything_it_installed() -> None:
    sql = _executed_sql("downgrade")

    assert "DROP FUNCTION IF EXISTS public.record_runtime_probe_verification" in sql
    assert "DROP POLICY IF EXISTS runtime_probe_control_receipts_switchboard" in sql
    assert "DROP TRIGGER IF EXISTS trg_runtime_probe_control_receipts_retention" in sql
    assert f"DROP TABLE IF EXISTS {_TABLE}" in sql
    assert sql.index("DROP POLICY") < sql.index("DROP TABLE")


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
    """A fresh 32-byte nonce, exactly as the capability carries."""
    return os.urandom(32)


@_integration
@_asyncio_session
async def test_receipt_table_has_exactly_the_five_recorded_columns(core_db_url: str) -> None:
    connection = await _connect(core_db_url)
    try:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND table_name = 'runtime_probe_control_receipts'"
        )
    finally:
        await connection.close()

    assert {row["column_name"] for row in rows} == {
        "audience",
        "nonce_digest",
        "kid",
        "capability_exp",
        "received_at",
    }


@_integration
@_asyncio_session
async def test_only_one_claim_per_nonce_survives(core_db_url: str) -> None:
    """The second use of a valid capability loses, and loses durably."""

    async def _as_switchboard(connection: asyncpg.Connection) -> None:
        # Per-connection: a pool hands out different connections, and SET ROLE
        # is connection state.
        await connection.execute(f'SET ROLE "{_SWITCHBOARD}"')

    pool = await asyncpg.create_pool(core_db_url, min_size=1, max_size=2, setup=_as_switchboard)
    try:
        receipts = RuntimeProbeControlReceipts(pool)
        nonce = _synthetic_nonce()
        expires_at = datetime.now(UTC) + timedelta(seconds=60)

        first = await receipts.claim(nonce=nonce, kid="probe-2026-05a", expires_at=expires_at)
        second = await receipts.claim(nonce=nonce, kid="probe-2026-05a", expires_at=expires_at)

        assert first is True
        assert second is False
        assert await receipts.is_consumed(nonce=nonce)
    finally:
        await pool.close()


@_integration
@_asyncio_session
async def test_receipt_stores_a_digest_rather_than_the_nonce(core_db_url: str) -> None:
    connection = await _connect(core_db_url, role=_SWITCHBOARD)
    nonce = _synthetic_nonce()
    try:
        await connection.execute(
            f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
            "VALUES ($1, $2, $3, $4)",
            _AUDIENCE,
            nonce_digest(nonce),
            "probe-2026-05a",
            datetime.now(UTC) + timedelta(seconds=60),
        )
        stored = await connection.fetchval(
            f"SELECT nonce_digest FROM {_TABLE} WHERE nonce_digest = $1", nonce_digest(nonce)
        )
    finally:
        await connection.close()

    assert stored == hashlib.sha256(nonce).digest()
    assert stored != nonce


@_integration
@_asyncio_session
async def test_a_foreign_audience_cannot_be_recorded(core_db_url: str) -> None:
    connection = await _connect(core_db_url, role=_SWITCHBOARD)
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
                "VALUES ($1, $2, $3, $4)",
                "switchboard.some.other.audience",
                nonce_digest(_synthetic_nonce()),
                "probe-2026-05a",
                datetime.now(UTC) + timedelta(seconds=60),
            )
    finally:
        await connection.close()


@_integration
@_asyncio_session
async def test_a_live_receipt_cannot_be_deleted(core_db_url: str) -> None:
    """A cleanup worker with a wrong predicate fails loudly, not silently."""
    connection = await _connect(core_db_url, role=_SWITCHBOARD)
    digest = nonce_digest(_synthetic_nonce())
    try:
        await connection.execute(
            f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
            "VALUES ($1, $2, $3, $4)",
            _AUDIENCE,
            digest,
            "probe-2026-05a",
            datetime.now(UTC) + timedelta(seconds=60),
        )

        with pytest.raises(asyncpg.RaiseError):
            await connection.execute(f"DELETE FROM {_TABLE} WHERE nonce_digest = $1", digest)
    finally:
        await connection.close()


@_integration
@_asyncio_session
async def test_a_receipt_past_its_retention_bound_is_removable(core_db_url: str) -> None:
    connection = await _connect(core_db_url, role=_SWITCHBOARD)
    expired = datetime.now(UTC) - RECEIPT_RETENTION_SKEW - timedelta(seconds=1)
    try:
        await connection.execute(
            f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
            "VALUES ($1, $2, $3, $4)",
            _AUDIENCE,
            nonce_digest(_synthetic_nonce()),
            "probe-2026-05a",
            expired,
        )

        purged = await RuntimeProbeControlReceipts(connection).purge_expired()
    finally:
        await connection.close()

    assert purged >= 1


@_integration
@_asyncio_session
@pytest.mark.parametrize("role", [_OTHER_BUTLER, _CONNECTOR])
async def test_no_other_runtime_role_can_touch_receipts(core_db_url: str, role: str) -> None:
    """Not a read, not a write, not a delete --- and init-db's broad public-schema
    grant does not change that."""
    connection = await _connect(core_db_url, role=role)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.fetch(f"SELECT 1 FROM {_TABLE}")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(
                f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
                "VALUES ($1, $2, $3, $4)",
                _AUDIENCE,
                nonce_digest(_synthetic_nonce()),
                "probe-2026-05a",
                datetime.now(UTC) + timedelta(seconds=60),
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute(f"DELETE FROM {_TABLE}")
    finally:
        await connection.close()


@_integration
@_asyncio_session
async def test_the_nightly_backup_can_still_read_the_table(core_db_url: str) -> None:
    """``pg_dump`` must not trip over row security on this table.

    ``deploy/backup/pg_dump.sh`` dumps as ``POSTGRES_USER`` (default
    ``butlers``), which is the migration user and therefore this table's OWNER,
    and ``scripts/init-db.sql`` forces that role ``NOSUPERUSER`` with no
    ``BYPASSRLS``.  ``pg_dump`` issues ``SET row_security = off`` before it
    copies anything, and in that mode PostgreSQL raises rather than silently
    returning a filtered result whenever a policy would apply to the reader.

    Under ``FORCE ROW LEVEL SECURITY`` policies apply to the owner too, so the
    dump would abort -- and because ``pg_dump.sh`` runs under ``set -o
    pipefail``, that aborts the WHOLE nightly backup, not just this table.

    This asserts on ``row_security = off`` rather than shelling out to
    ``pg_dump`` deliberately: it is the same mechanism, it needs no client
    binary matching the server major version, and a real ``pg_dump`` run
    against a freshly bootstrapped database currently fails earlier for an
    unrelated pre-existing reason (``init-db.sql`` revokes the migration
    user's access to the restore-drill boundary objects, which ``pg_dump``
    locks before dumping).  That is tracked separately; it must not silently
    become this table's problem later.
    """
    owner = await _connect(core_db_url)
    try:
        await owner.execute("SET row_security = off")
        # The value does not matter; not raising is the whole assertion.
        assert await owner.fetchval(f"SELECT count(*) FROM {_TABLE}") is not None
    finally:
        await owner.close()


@_integration
@_asyncio_session
async def test_forcing_row_security_would_break_that_backup(core_db_url: str) -> None:
    """Pin the trap itself, so nobody re-adds FORCE without seeing the cost.

    Everything happens inside a rolled-back transaction, so the table keeps the
    settings the migration gave it.
    """
    owner = await _connect(core_db_url)
    transaction = owner.transaction()
    await transaction.start()
    try:
        await owner.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
        await owner.execute("SET LOCAL row_security = off")
        with pytest.raises(asyncpg.PostgresError) as raised:
            await owner.fetchval(f"SELECT count(*) FROM {_TABLE}")
        assert "row-level security" in str(raised.value).lower()
    finally:
        await transaction.rollback()
        await owner.close()


@_integration
@_asyncio_session
async def test_row_security_survives_an_init_db_regrant(core_db_url: str) -> None:
    """This is what row security buys, and why the revokes alone are not enough.

    ``scripts/init-db.sql`` re-runs ``GRANT ... ON ALL TABLES IN SCHEMA public``
    for every runtime role on every invocation, so the migration's per-role
    REVOKEs are undone the next time an operator bootstraps.  The policy is not
    a grant and is not re-granted away.
    """
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
        # Exactly what init-db does, narrowed to one role.
        await owner.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {_TABLE} TO {_OTHER_BUTLER}"
        )
        regranted = await _connect(core_db_url, role=_OTHER_BUTLER)
        try:
            visible = await regranted.fetchval(
                f"SELECT count(*) FROM {_TABLE} WHERE nonce_digest = $1", digest
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await regranted.execute(
                    f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
                    "VALUES ($1, $2, $3, $4)",
                    _AUDIENCE,
                    nonce_digest(_synthetic_nonce()),
                    "probe-2026-05a",
                    datetime.now(UTC) + timedelta(seconds=60),
                )
        finally:
            await regranted.close()
            await owner.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_TABLE} FROM {_OTHER_BUTLER}")
    finally:
        await owner.close()

    assert visible == 0


# ---------------------------------------------------------------------------
# Verification persistence
# ---------------------------------------------------------------------------


async def _seed_catalog_entry(db_url: str) -> str:
    connection = await _connect(db_url)
    try:
        return await connection.fetchval(
            """
            INSERT INTO public.model_catalog (alias, runtime_type, model_id, priority)
            VALUES ($1, 'claude', 'synthetic-model', 7)
            RETURNING id
            """,
            f"synthetic-probe-{os.urandom(4).hex()}",
        )
    finally:
        await connection.close()


@_integration
@_asyncio_session
async def test_a_probe_writes_verification_evidence_and_nothing_else(core_db_url: str) -> None:
    entry_id = await _seed_catalog_entry(core_db_url)
    connection = await _connect(core_db_url, role=_SWITCHBOARD)
    try:
        recorded = await RuntimeProbeVerificationPersistence(connection).record(
            catalog_entry_id=entry_id, ok=True, latency_ms=42
        )
    finally:
        await connection.close()

    owner = await _connect(core_db_url)
    try:
        row = await owner.fetchrow(
            "SELECT enabled, priority, last_verified_ok, last_verified_latency_ms, "
            "last_verified_at, last_verified_error FROM public.model_catalog WHERE id = $1",
            entry_id,
        )
    finally:
        await owner.close()

    assert recorded is True
    assert row["last_verified_ok"] is True
    assert row["last_verified_latency_ms"] == 42
    assert row["last_verified_at"] is not None
    assert row["last_verified_error"] is None
    # A probe outcome must never reach dispatch state.
    assert row["enabled"] is True
    assert row["priority"] == 7


@_integration
@_asyncio_session
async def test_verification_persistence_is_switchboard_only(core_db_url: str) -> None:
    entry_id = await _seed_catalog_entry(core_db_url)
    connection = await _connect(core_db_url, role=_OTHER_BUTLER)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.fetchval(
                "SELECT public.record_runtime_probe_verification($1, true, 1, NULL)", entry_id
            )
    finally:
        await connection.close()


@_integration
@_asyncio_session
async def test_a_probe_cannot_update_the_catalog_directly(core_db_url: str) -> None:
    """The definer function is the only path, so its narrowness is the boundary."""
    entry_id = await _seed_catalog_entry(core_db_url)
    connection = await _connect(core_db_url, role=_SWITCHBOARD)
    try:
        recorded = await connection.fetchval(
            "SELECT public.record_runtime_probe_verification($1, false, NULL, $2)",
            entry_id,
            "synthetic failure detail",
        )
    finally:
        await connection.close()

    owner = await _connect(core_db_url)
    try:
        row = await owner.fetchrow(
            "SELECT last_verified_ok, last_verified_error, enabled "
            "FROM public.model_catalog WHERE id = $1",
            entry_id,
        )
    finally:
        await owner.close()

    assert recorded is True
    assert row["last_verified_ok"] is False
    assert row["last_verified_error"] == "synthetic failure detail"
    assert row["enabled"] is True
