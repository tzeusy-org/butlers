"""home_010 (critical_entities) (bu-jv1moa.122). Constraint test needs real PostgreSQL."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

_HOME_010 = Path(__file__).resolve().parents[2] / "roster/home/migrations/010_critical_entities.py"
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _load():
    spec = importlib.util.spec_from_file_location("home_010", _HOME_010)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_the_revision_extends_home_009_and_downgrade_drops_the_table() -> None:
    module = _load()
    statements: list[str] = []
    fake_op = MagicMock()
    fake_op.execute.side_effect = statements.append
    with patch.object(module, "op", fake_op):
        module.downgrade()

    assert (module.revision, module.down_revision) == ("home_010", "home_009")
    assert statements == ["DROP TABLE IF EXISTS critical_entities"]


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_one_row_per_explicit_entity_with_a_bounded_label(db) -> None:
    pool = await db("core", "home", schemas={"home": "home"}, schema="home")
    insert = (
        "INSERT INTO critical_entities (entity_id, owner_label, added_by) VALUES ($1, $2, 'owner')"
    )
    await pool.execute(insert, "switch.freezer", "Freezer plug")
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(insert, "switch.freezer", "Again")
    for bad_id in ("freezer", "switch.a, switch.b", "Switch.Freezer", "all"):
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(insert, bad_id, "Label")
    for bad_label in ("", "   ", "x" * 81):
        with pytest.raises(asyncpg.CheckViolationError):
            await pool.execute(insert, "switch.other", bad_label)

    assert await pool.fetchval("SELECT count(*) FROM critical_entities") == 1
