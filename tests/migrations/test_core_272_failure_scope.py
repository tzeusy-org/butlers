"""core_272 fails loudly: only the sequence GRANT may be skipped for lack of privilege.

Bead bu-kqnum.3.6.1 corrections. A handler around the whole upgrade would roll back every
DDL statement on a privilege error while Alembic still stamped core_272 applied, leaving
holds that write ``wake_*`` columns that do not exist.
"""

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
    / "core_272_deferred_notification_wake_provenance.py"
)


def _upgrade_sql() -> str:
    spec = importlib.util.spec_from_file_location("core_272", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mock_op = MagicMock()
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return "\n".join(call.args[0] for call in mock_op.execute.call_args_list)


@pytest.mark.unit
def test_only_the_grant_sits_inside_the_privilege_handler() -> None:
    sql = _upgrade_sql()

    assert sql.count("insufficient_privilege") == 1
    handler = sql.index("insufficient_privilege")
    block_start = sql.rindex("BEGIN", 0, handler)
    guarded = sql[block_start:handler]
    assert "GRANT USAGE, SELECT ON SEQUENCE" in guarded
    for ddl in ("CREATE SEQUENCE", "ALTER TABLE", "CREATE TRIGGER", "CREATE OR REPLACE FUNCTION"):
        assert ddl not in guarded
        assert sql.index(ddl) < block_start


@pytest.fixture
async def pool(postgres_container):
    from butlers.testing.migration import create_migrated_test_pool

    p = await create_migrated_test_pool(
        postgres_container,
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
        pool_schema="switchboard",
    )
    try:
        yield p
    finally:
        await p.close()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available")
async def test_partial_provenance_is_refused_by_the_all_or_nothing_check(pool) -> None:
    import asyncpg

    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO deferred_notifications"
            " (butler_name, channel, message, envelope, deliver_at, wake_policy_window_key)"
            " VALUES ('b', 'telegram', 'm', '{}'::jsonb, now(), 'w')"
        )
