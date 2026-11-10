"""core_327: the replay receipt admits the dashboard service-call audience, and only it."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.core.runtime_probe_control.keys import CONTROL_AUDIENCE, SERVICE_CALL_AUDIENCE
from butlers.core.runtime_probe_control.receipts import (
    RECEIPT_AUDIENCES,
    RuntimeProbeControlReceipts,
    nonce_digest,
)
from butlers.testing.migration import load_migration

_PATH = (
    Path(__file__).resolve().parents[2] / "alembic/versions/core/core_327_service_call_receipts.py"
)
_TABLE = "public.runtime_probe_control_receipts"
_SWITCHBOARD = "butler_switchboard_rw"

_integration = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


def test_core_327_extends_core_326() -> None:
    module = load_migration(_PATH, "core_327")
    assert (module.revision, module.down_revision) == ("core_327", "core_326")


def test_the_check_names_exactly_the_audiences_the_receipts_class_accepts() -> None:
    module = load_migration(_PATH, "core_327")
    assert {module._CONTROL_AUDIENCE, module._SERVICE_CALL_AUDIENCE} == RECEIPT_AUDIENCES
    assert {CONTROL_AUDIENCE, SERVICE_CALL_AUDIENCE} == RECEIPT_AUDIENCES


async def test_the_receipts_class_writes_and_reads_under_its_own_audience() -> None:
    class _Pool:
        def __init__(self) -> None:
            self.audiences: list[str] = []

        async def fetchval(self, _sql: str, audience: str, *_args: object) -> bool:
            self.audiences.append(audience)
            return True

    pool = _Pool()
    service = RuntimeProbeControlReceipts(pool, audience=SERVICE_CALL_AUDIENCE)
    await service.claim(nonce=os.urandom(32), kid="k", expires_at=datetime.now(UTC))
    await service.is_consumed(nonce=os.urandom(32))
    await RuntimeProbeControlReceipts(pool).is_consumed(nonce=os.urandom(32))

    assert pool.audiences == [SERVICE_CALL_AUDIENCE, SERVICE_CALL_AUDIENCE, CONTROL_AUDIENCE]


def test_the_receipts_class_refuses_an_unknown_audience() -> None:
    with pytest.raises(ValueError):
        RuntimeProbeControlReceipts(object(), audience="switchboard.some.other.audience")


def _guarded_op(*, table_exists: bool, installed_schemas: int):
    module = load_migration(_PATH, "core_327")
    statements: list[str] = []
    op = MagicMock()
    op.execute.side_effect = statements.append
    bind = op.get_bind.return_value

    def answer(sql: str, *args: object) -> MagicMock:
        result = MagicMock()
        if "to_regclass" in sql:
            result.scalar.return_value = table_exists
        elif "pg_class version_table" in sql:
            result.scalars.return_value = iter([f"s{i}" for i in range(installed_schemas)])
        else:
            result.scalar.return_value = True
        return result

    bind.exec_driver_sql.side_effect = answer
    bind.dialect.identifier_preparer.quote.side_effect = lambda name: name
    return module, op, statements


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_core_327_changes_nothing_while_the_shared_table_is_absent(direction: str) -> None:
    module, op, statements = _guarded_op(table_exists=False, installed_schemas=1)
    with patch.object(module, "op", op):
        getattr(module, direction)()
    assert statements and all("ALTER TABLE" not in s for s in statements)


def test_core_327_downgrade_waits_for_the_last_schema_to_leave() -> None:
    module, op, statements = _guarded_op(table_exists=True, installed_schemas=2)
    with patch.object(module, "op", op):
        module.downgrade()
    assert all("ALTER TABLE" not in s for s in statements)

    module, op, statements = _guarded_op(table_exists=True, installed_schemas=1)
    with patch.object(module, "op", op):
        module.downgrade()
    restored = [s for s in statements if "ADD CONSTRAINT" in s]
    assert restored == [
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT ck_runtime_probe_control_receipts_audience"
        f" CHECK (audience = '{CONTROL_AUDIENCE}') NOT VALID"
    ]


def test_upgrade_widens_the_check_to_exactly_the_two_audiences() -> None:
    module, op, statements = _guarded_op(table_exists=True, installed_schemas=1)
    with patch.object(module, "op", op):
        module.upgrade()
    (added,) = [s for s in statements if "ADD CONSTRAINT" in s]
    assert f"CHECK (audience IN ('{CONTROL_AUDIENCE}', '{SERVICE_CALL_AUDIENCE}'))" in added


@pytest.fixture
def core_db_url(fresh_db) -> str:
    return fresh_db("core")


async def _switchboard_pool(db_url: str) -> asyncpg.Pool:
    async def as_switchboard(connection: asyncpg.Connection) -> None:
        await connection.execute(f'SET ROLE "{_SWITCHBOARD}"')

    return await asyncpg.create_pool(db_url, min_size=1, max_size=2, setup=as_switchboard)


@_integration
@_asyncio_session
async def test_each_audience_consumes_its_own_nonces_once(core_db_url: str) -> None:
    pool = await _switchboard_pool(core_db_url)
    try:
        probe = RuntimeProbeControlReceipts(pool)
        service = RuntimeProbeControlReceipts(pool, audience=SERVICE_CALL_AUDIENCE)
        nonce = os.urandom(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=60)

        assert await service.claim(nonce=nonce, kid="probe-2026-11a", expires_at=expires_at)
        assert not await service.claim(nonce=nonce, kid="probe-2026-11a", expires_at=expires_at)
        # The same bytes under the other audience are a different receipt.
        assert not await probe.is_consumed(nonce=nonce)
        assert await service.is_consumed(nonce=nonce)
    finally:
        await pool.close()


@_integration
@_asyncio_session
async def test_a_third_audience_is_still_refused(core_db_url: str) -> None:
    connection = await asyncpg.connect(core_db_url)
    try:
        await connection.execute(f'SET ROLE "{_SWITCHBOARD}"')
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                f"INSERT INTO {_TABLE} (audience, nonce_digest, kid, capability_exp) "
                "VALUES ($1, $2, $3, $4)",
                "switchboard.some.other.audience",
                nonce_digest(os.urandom(32)),
                "probe-2026-11a",
                datetime.now(UTC) + timedelta(seconds=60),
            )
    finally:
        await connection.close()
