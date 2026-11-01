"""Owner-zone readers resolve ``public.state``, not the butler's own ``state`` (bu-ko02so).

``settings.general`` lives in ``public.state`` (core_087) but a butler pool's ``search_path``
puts ``<schema>.state`` first, so an unqualified read silently fell back to UTC.
"""

from __future__ import annotations

import shutil
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from butlers.api.owner_time_bounds import owner_zoneinfo
from butlers.core.general_settings import load_general_settings, resolve_general_timezone

docker_available = shutil.which("docker") is not None

_CREATE_STATE = (
    "CREATE TABLE IF NOT EXISTS {}.state (key TEXT PRIMARY KEY, value JSONB NOT NULL, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), version INTEGER NOT NULL DEFAULT 1)"
)


def _pool(stored):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=stored)
    return pool


@pytest.mark.unit
async def test_load_general_settings_reads_schema_qualified_public_state() -> None:
    pool = _pool({"timezone": "Europe/Berlin", "currency": "EUR"})

    settings = await load_general_settings(pool)

    assert "public.state" in pool.fetchval.call_args[0][0]
    assert settings["timezone"] == "Europe/Berlin"
    assert settings["currency"] == "EUR"


@pytest.mark.unit
async def test_unset_defaults_are_unchanged() -> None:
    settings = await load_general_settings(_pool(None))

    assert settings["timezone"] == "UTC"
    assert settings["currency"] == "USD"


@pytest.mark.unit
async def test_resolve_general_timezone_and_owner_zoneinfo_use_the_qualified_read() -> None:
    pool = _pool({"timezone": "Asia/Singapore"})

    assert await resolve_general_timezone(pool) == "Asia/Singapore"
    assert (await owner_zoneinfo(pool)) == ZoneInfo("Asia/Singapore")
    assert all("public.state" in c[0][0] for c in pool.fetchval.call_args_list)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.parametrize("schema", ["finance", "health"])
async def test_butler_pool_with_its_own_state_still_resolves_the_owner_zone(
    provisioned_postgres_pool, schema: str
) -> None:
    """Both tables hold ``settings.general``; only ``public.state`` is the owner's."""
    async with provisioned_postgres_pool(schema=schema) as pool:
        await pool.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        await pool.execute(_CREATE_STATE.format("public"))
        await pool.execute(_CREATE_STATE.format(schema))
        await pool.execute(
            "INSERT INTO public.state (key, value) VALUES ('settings.general', $1)",
            {"timezone": "Europe/Berlin", "currency": "EUR"},
        )
        await pool.execute(
            f"INSERT INTO {schema}.state (key, value) VALUES ('settings.general', $1)",
            {"timezone": "America/Los_Angeles", "currency": "JPY"},
        )

        assert await resolve_general_timezone(pool) == "Europe/Berlin"
        settings = await load_general_settings(pool)
        assert settings["timezone"] == "Europe/Berlin"
        assert settings["currency"] == "EUR"
        assert (await owner_zoneinfo(pool)).key == "Europe/Berlin"
