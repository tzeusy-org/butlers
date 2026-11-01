"""Real-Postgres coverage of the allowance endpoint's account/catalog join (synthetic data)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from butlers.api.db import DatabaseManager
from butlers.api.routers.model_settings import observe_model_allowance

pytestmark = [pytest.mark.integration, pytest.mark.db]

_MIGRATION = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_257_provider_allowance_states.py"
)


async def _upgrade(pool) -> None:
    spec = importlib.util.spec_from_file_location("core_257", _MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    mocked_op = MagicMock()
    mocked_op.execute.side_effect = statements.append
    with patch.object(migration, "op", mocked_op):
        migration.upgrade()
    for statement in statements:
        await pool.execute(statement)


@pytest.mark.asyncio(loop_scope="session")
async def test_entries_group_by_allowance_account_or_runtime_default(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool() as pool:
        await pool.execute(
            "CREATE TABLE public.model_catalog (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), "
            "alias TEXT NOT NULL, runtime_type TEXT NOT NULL)"
        )
        await _upgrade(pool)
        await pool.execute(
            "INSERT INTO public.model_catalog (alias, runtime_type, allowance_account) VALUES "
            "('fast', 'runtime-a', NULL), ('deep', 'runtime-a', NULL), "
            "('shared-one', 'runtime-b', 'team-plan'), ('shared-two', 'runtime-c', 'team-plan'), "
            "('lonely', 'runtime-d', NULL)"
        )
        await pool.execute(
            "INSERT INTO public.provider_allowance_states "
            "(account_key, state, reset_at, reset_source) VALUES "
            "('runtime-a', 'exhausted', now() + interval '1 hour', 'parsed'), "
            "('team-plan', 'available', NULL, 'unknown'), "
            "('empty-account', 'unknown', NULL, 'unknown')"
        )
        manager = MagicMock(spec=DatabaseManager)
        manager.credential_shared_pool.return_value = pool

        response = await observe_model_allowance(_owner="owner", db=manager)

    accounts = {a.account_key: a for a in response.data.accounts}
    assert response.data.available is True
    assert set(accounts) == {"runtime-a", "team-plan", "empty-account"}
    assert [e.alias for e in accounts["runtime-a"].entries] == ["deep", "fast"]
    assert [e.alias for e in accounts["team-plan"].entries] == ["shared-one", "shared-two"]
    assert accounts["empty-account"].entries == []
    assert accounts["runtime-a"].state == "exhausted"
    assert accounts["runtime-a"].reset_source == "parsed"
