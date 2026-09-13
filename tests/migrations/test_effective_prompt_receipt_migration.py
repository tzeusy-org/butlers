"""Real-Postgres lifecycle for core_234/core_235 prompt and purpose receipts."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.db]

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_234_effective_prompt_receipt.py"
)
_PURPOSE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic/versions/core/core_235_session_purpose_lane.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_234", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prompt_receipt_migrations_extend_the_live_core_head() -> None:
    receipt = _load_migration()
    purpose_spec = importlib.util.spec_from_file_location("core_235", _PURPOSE_MIGRATION_PATH)
    assert purpose_spec is not None and purpose_spec.loader is not None
    purpose = importlib.util.module_from_spec(purpose_spec)
    purpose_spec.loader.exec_module(purpose)

    assert (receipt.revision, receipt.down_revision) == ("core_234", "core_233")
    assert (purpose.revision, purpose.down_revision) == ("core_235", "core_234")


async def _run_migration(pool, direction: str) -> None:
    statements: list[str] = []
    module = _load_migration()
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with (
        patch.object(module, "op", mocked_op),
        patch.object(module, "preflight_runtime_attention_downgrade"),
    ):
        getattr(module, direction)()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.asyncio(loop_scope="session")
async def test_receipt_columns_are_additive_atomic_and_rollback_safe(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE TABLE sessions (id UUID PRIMARY KEY)")
        legacy_id = uuid.uuid4()
        await pool.execute("INSERT INTO sessions (id) VALUES ($1)", legacy_id)

        await _run_migration(pool, "upgrade")
        legacy = await pool.fetchrow(
            "SELECT effective_system_prompt, prompt_digest, prompt_provenance "
            "FROM sessions WHERE id = $1",
            legacy_id,
        )
        assert legacy is not None
        assert tuple(legacy) == (None, None, None)

        receipt_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO sessions "
            "(id, effective_system_prompt, prompt_digest, prompt_provenance) "
            "VALUES ($1, 'exact prompt', $2, $3::jsonb)",
            receipt_id,
            "a" * 64,
            [
                {
                    "source": "roster:synthetic/CLAUDE.md",
                    "status": "present",
                    "bytes": 12,
                    "sha": "b" * 64,
                }
            ],
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO sessions (id, effective_system_prompt) VALUES ($1, 'partial')",
                uuid.uuid4(),
            )

        with pytest.raises(asyncpg.RaiseError, match="effective prompt receipts exist"):
            await _run_migration(pool, "downgrade")

        await pool.execute(
            "UPDATE sessions SET effective_system_prompt = NULL, "
            "prompt_digest = NULL, prompt_provenance = NULL WHERE id = $1",
            receipt_id,
        )
        await _run_migration(pool, "downgrade")
        columns = await pool.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'sessions'"
        )
        assert {row["column_name"] for row in columns} == {"id"}
        assert await pool.fetchval("SELECT count(*) FROM sessions") == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_purpose_lane_is_closed_additive_and_rollback_safe(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute("CREATE TABLE sessions (id UUID PRIMARY KEY)")
        legacy_id = uuid.uuid4()
        await pool.execute("INSERT INTO sessions (id) VALUES ($1)", legacy_id)

        statements: list[str] = []
        spec = importlib.util.spec_from_file_location("core_235", _PURPOSE_MIGRATION_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mocked_op = MagicMock()
        mocked_op.execute.side_effect = statements.append
        with (
            patch.object(module, "op", mocked_op),
            patch.object(module, "preflight_runtime_attention_downgrade"),
        ):
            module.upgrade()
        for statement in statements:
            await pool.execute(statement)

        assert (
            await pool.fetchval("SELECT purpose_lane FROM sessions WHERE id = $1", legacy_id)
            is None
        )
        private_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO sessions (id, purpose_lane) VALUES ($1, 'private_content')",
            private_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(
                "INSERT INTO sessions (id, purpose_lane) VALUES ($1, 'other')",
                uuid.uuid4(),
            )

        statements.clear()
        with (
            patch.object(module, "op", mocked_op),
            patch.object(module, "preflight_runtime_attention_downgrade"),
        ):
            module.downgrade()
        with pytest.raises(asyncpg.RaiseError, match="session purpose evidence exists"):
            for statement in statements:
                await pool.execute(statement)

        await pool.execute(
            "UPDATE sessions SET purpose_lane = NULL WHERE id = $1",
            private_id,
        )
        for statement in statements:
            await pool.execute(statement)
        assert not await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'sessions'
                   AND column_name = 'purpose_lane'
            )
            """
        )
