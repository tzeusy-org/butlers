"""core_271: sender_authentication on public.ingestion_events (bu-s11n0s.20)."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_271_ingestion_events_sender_authentication.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_271", _MIGRATION_PATH)
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

    assert (mod.revision, mod.down_revision) == ("core_271", "core_268")
    assert mod.branch_labels is None and mod.depends_on is None


@pytest.mark.unit
def test_upgrade_adds_a_nullable_text_column_with_exactly_the_three_values() -> None:
    sql = _emitted("upgrade")

    assert "ADD COLUMN IF NOT EXISTS sender_authentication TEXT" in sql
    assert "NOT NULL" not in sql and "DEFAULT" not in sql
    assert "'authenticated', 'unverified', 'failed'" in sql


@pytest.mark.unit
def test_the_check_is_added_not_valid_then_validated_in_autocommit_under_a_lock_timeout() -> None:
    sql = _emitted("upgrade")

    assert "SET lock_timeout = '5s'" in sql
    assert "NOT VALID" in sql
    assert sql.index("NOT VALID") < sql.index(
        "VALIDATE CONSTRAINT ck_ingestion_events_sender_authentication"
    )


@pytest.mark.unit
def test_the_upgrade_runs_inside_an_autocommit_block() -> None:
    mod = _load_migration()
    mock_op = MagicMock()
    with patch.object(mod, "op", mock_op):
        mod.upgrade()

    mock_op.get_context.return_value.autocommit_block.assert_called_once()
    entered = mock_op.get_context.return_value.autocommit_block.return_value.__enter__
    entered.assert_called_once()


@pytest.mark.unit
def test_downgrade_drops_the_constraint_then_the_column() -> None:
    sql = _emitted("downgrade")

    assert sql.index("DROP CONSTRAINT IF EXISTS ck_ingestion_events_sender_authentication") < (
        sql.index("DROP COLUMN IF EXISTS sender_authentication")
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_the_column_and_its_constraint_exist(postgres_container) -> None:
    from butlers.testing.migration import create_migrated_test_pool

    pool = await create_migrated_test_pool(postgres_container, chains=["core"])
    try:
        column = await pool.fetchrow(
            """
            SELECT data_type, is_nullable FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'ingestion_events'
              AND column_name = 'sender_authentication'
            """
        )
        assert (column["data_type"], column["is_nullable"]) == ("text", "YES")
        definition = await pool.fetchval(
            """
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conname = 'ck_ingestion_events_sender_authentication'
              AND conrelid = 'public.ingestion_events'::regclass
            """
        )
        assert definition is not None
        for value in ("authenticated", "unverified", "failed"):
            assert f"'{value}'" in definition
    finally:
        await pool.close()
