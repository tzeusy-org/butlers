"""home_011 (pre_state, already_satisfied) (bu-jv1moa.123). Constraint test needs PostgreSQL."""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

_HOME_011 = (
    Path(__file__).resolve().parents[2]
    / "roster/home/migrations/011_pre_state_already_satisfied.py"
)
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _load():
    spec = importlib.util.spec_from_file_location("home_011", _HOME_011)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_the_revision_extends_home_010_and_adds_the_column_and_the_status() -> None:
    module = _load()
    up: list[str] = []
    down: list[str] = []
    for fn, sink in ((module.upgrade, up), (module.downgrade, down)):
        fake_op = MagicMock()
        fake_op.execute.side_effect = sink.append
        with patch.object(module, "op", fake_op):
            fn()

    assert (module.revision, module.down_revision) == ("home_011", "home_010")
    assert "ADD COLUMN IF NOT EXISTS pre_state JSONB" in up[0]
    assert "'already_satisfied'" in up[2]
    assert "'already_satisfied'" not in down[1]
    assert "DROP COLUMN IF EXISTS pre_state" in down[2]


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_already_satisfied_receipt_keeps_its_pre_state_and_no_other_status_is_new(db):
    pool = await db("core", "home", schemas={"home": "home"}, schema="home")
    insert = (
        "INSERT INTO ha_command_log (domain, service, attempt_id, status, pre_state) "
        "VALUES ('valve', 'close_valve', $1, $2, $3)"
    )
    await pool.execute(
        insert, uuid.uuid4(), "already_satisfied", {"valve.main": {"state": "closed"}}
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(insert, uuid.uuid4(), "done", None)

    row = await pool.fetchrow("SELECT status, pre_state FROM ha_command_log")
    assert row["status"] == "already_satisfied"
    assert row["pre_state"]["valve.main"]["state"] == "closed"
