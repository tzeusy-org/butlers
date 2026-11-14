"""home_009 (departure_fixes) (bu-jv1moa.115). Constraint test needs real PostgreSQL."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

_HOME_009 = Path(__file__).resolve().parents[2] / "roster/home/migrations/009_departure_fixes.py"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _load():
    spec = importlib.util.spec_from_file_location("home_009", _HOME_009)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_the_revision_extends_home_008_and_downgrade_drops_the_table() -> None:
    module = _load()
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        module.downgrade()

    assert (module.revision, module.down_revision) == ("home_009", "home_008")
    assert statements == ["DROP TABLE IF EXISTS departure_fixes"]


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_one_fix_per_watched_entity_that_cannot_outlive_its_watch(db) -> None:
    pool = await db("core", "home", schemas={"home": "home"}, schema="home")
    insert = (
        "INSERT INTO departure_fixes (entity_id, domain, service, target_entity_id) "
        "VALUES ($1, 'light', 'turn_off', $1)"
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await pool.execute(insert, "light.garage")
    await pool.execute(
        "INSERT INTO departure_watch (entity_id, owner_label, secure_states) "
        "VALUES ('light.garage', 'Garage', ARRAY['off'])"
    )
    await pool.execute(insert, "light.garage")
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(insert, "light.garage")
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute("UPDATE departure_fixes SET target_entity_id = 'light.a, light.b'")

    await pool.execute("DELETE FROM departure_watch WHERE entity_id = 'light.garage'")

    assert await pool.fetchval("SELECT count(*) FROM departure_fixes") == 0
