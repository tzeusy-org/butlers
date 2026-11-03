"""PostgreSQL-backed coverage for the Spotify spoken capture-only rule seed."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.testing.migration import load_migration

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "switchboard"
    / "migrations"
    / "030_switchboard_spotify_spoken_metadata_only.py"
)


async def _apply(pool, operation: str) -> None:
    module = load_migration(_MIGRATION_PATH, "sw_030")
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        getattr(module, operation)()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_upgrade_seeds_only_spotify_spoken_metadata_rule_and_downgrade_removes_it(
    db,
) -> None:
    # The real switchboard.ingestion_rules as sw_030's predecessor left it.
    pool = await db(
        "core",
        "switchboard",
        schemas={"core": "switchboard", "switchboard": "switchboard"},
        schema="switchboard",
        revisions={"switchboard": "sw_029"},
    )
    # Earlier revisions seed their own rules; clear them so only sw_030's row is observed.
    await pool.execute("DELETE FROM ingestion_rules")

    await _apply(pool, "upgrade")
    await _apply(pool, "upgrade")
    row = await pool.fetchrow(
        "SELECT scope, rule_type, condition, action, enabled FROM ingestion_rules"
    )
    assert row is not None
    assert dict(row) == {
        "scope": "global",
        "rule_type": "substring",
        "condition": {"pattern": "spotify:spoken:"},
        "action": "metadata_only",
        "enabled": True,
    }

    await _apply(pool, "downgrade")
    assert await pool.fetchval("SELECT count(*) FROM ingestion_rules") == 0
