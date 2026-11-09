"""core_326: a rebind receipt may settle as skipped_forgotten, and only in its own shape."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.testing.migration import load_migration

pytestmark = [pytest.mark.integration, pytest.mark.db]

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_326_rebind_skipped_forgotten.py"
)
_ENTITY = (
    "INSERT INTO public.entities (canonical_name) "
    "VALUES ('rebind ' || gen_random_uuid()) RETURNING id"
)
_SETTLE = (
    "UPDATE public.entity_rebind_log SET status = $2, completed_at = now(), error_class = $3 "
    "WHERE rebind_id = $1"
)


def test_core_326_extends_core_325() -> None:
    module = load_migration(_PATH, "core_326")
    assert (module.revision, module.down_revision) == ("core_326", "core_325")


async def _run(pool, direction: str) -> None:
    module = load_migration(_PATH, "core_326")
    statements: list[str] = []
    op = MagicMock()
    op.execute.side_effect = statements.append
    with patch.object(module, "op", op):
        getattr(module, direction)()
    async with pool.acquire() as conn, conn.transaction():
        for statement in statements:
            await conn.execute(statement)


@pytest.mark.asyncio(loop_scope="session")
async def test_skipped_forgotten_is_allowed_only_without_an_error_class_and_downgrade_reverts(
    db,
) -> None:
    pool = await db("core")
    source, target = await pool.fetchval(_ENTITY), await pool.fetchval(_ENTITY)
    rebind = await pool.fetchval("SELECT gen_random_uuid()")
    await pool.execute(
        "INSERT INTO public.entity_rebind_log (rebind_id, source_entity_id, target_entity_id,"
        " target_schema) VALUES ($1, $2, $3, 'finance')",
        rebind,
        source,
        target,
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(_SETTLE, rebind, "skipped_forgotten", "EntityRebindTargetForgotten")
    await pool.execute(_SETTLE, rebind, "skipped_forgotten", None)

    await _run(pool, "downgrade")
    row = await pool.fetchrow(
        "SELECT status, error_class FROM public.entity_rebind_log WHERE rebind_id = $1", rebind
    )
    assert (row["status"], row["error_class"]) == ("failed", "EntityRebindTargetForgotten")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(_SETTLE, rebind, "skipped_forgotten", None)

    await _run(pool, "upgrade")
    await pool.execute(_SETTLE, rebind, "skipped_forgotten", None)


def _guarded_op(*, table_exists: bool, installed_schemas: int):
    """An op whose bind answers the table probe and the schema-chain census."""
    module = load_migration(_PATH, "core_326")
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
def test_core_326_changes_nothing_while_the_shared_table_is_absent(direction: str) -> None:
    """Another chain's core_243 downgrade can drop the shared table before this chain runs."""
    module, op, statements = _guarded_op(table_exists=False, installed_schemas=1)
    with patch.object(module, "op", op):
        getattr(module, direction)()
    assert all("ALTER TABLE" not in s and "UPDATE" not in s for s in statements)


def test_core_326_downgrade_waits_for_the_last_schema_to_leave() -> None:
    module, op, statements = _guarded_op(table_exists=True, installed_schemas=2)
    with patch.object(module, "op", op):
        module.downgrade()
    assert all("ALTER TABLE" not in s and "UPDATE" not in s for s in statements)

    module, op, statements = _guarded_op(table_exists=True, installed_schemas=1)
    with patch.object(module, "op", op):
        module.downgrade()
    assert any("UPDATE public.entity_rebind_log" in s for s in statements)
