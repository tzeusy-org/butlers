"""Real-Postgres contract for core_228's runtime_config blind-spot kill switch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.testing.migration import load_migration

pytestmark = pytest.mark.integration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_228_runtime_config_blind_spot_flag.py"
)


async def _run_migration(pool, path: Path, direction: str) -> None:
    statements: list[str] = []
    module = load_migration(path, path.stem)
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with patch.object(module, "op", mocked_op):
        getattr(module, direction)()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.asyncio(loop_scope="session")
async def test_column_defaults_true_and_downgrade_drops_it(db) -> None:
    # The real runtime_config as core_228's predecessor left it.
    pool = await db("core", revisions={"core": "core_227"})
    await pool.execute("INSERT INTO runtime_config (butler_name, max_concurrent) VALUES ('x', 3)")

    await _run_migration(pool, _MIGRATION_PATH, "upgrade")

    value = await pool.fetchval(
        "SELECT blind_spot_preamble_enabled FROM runtime_config WHERE butler_name = 'x'"
    )
    assert value is True

    await _run_migration(pool, _MIGRATION_PATH, "downgrade")
    assert not await pool.fetchval(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'runtime_config' AND column_name = 'blind_spot_preamble_enabled'"
    )
