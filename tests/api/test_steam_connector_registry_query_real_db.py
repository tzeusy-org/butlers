"""Real-DB regression: the Steam health query reads only Steam heartbeats (bu-c4fweu).

The registry holds one row per connector type and endpoint identity, plus a ``steam:config``
settings row. A WHERE clause that lost its connector-type filter or its config-row exclusion
would report another connector's newest heartbeat, or the settings row, as Steam's. The unit
tests only see the query text through a mock pool, so this runs it against Postgres.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

import butlers.api.routers.steam as steam_router

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """The slice of ``switchboard.connector_registry`` the Steam health query reads."""
    async with provisioned_postgres_pool(min_pool_size=1, max_pool_size=4) as p:
        await p.execute("CREATE SCHEMA IF NOT EXISTS switchboard")
        await p.execute("""
            CREATE TABLE IF NOT EXISTS switchboard.connector_registry (
                connector_type    TEXT NOT NULL,
                endpoint_identity TEXT NOT NULL,
                state             TEXT NOT NULL DEFAULT 'unknown',
                error_message     TEXT,
                uptime_s          INTEGER,
                last_heartbeat_at TIMESTAMPTZ,
                settings          JSONB,
                PRIMARY KEY (connector_type, endpoint_identity)
            )
        """)
        await p.execute("DELETE FROM switchboard.connector_registry")
        yield p


async def _beat(pool, connector_type: str, identity: str, state: str, age: timedelta) -> None:
    await pool.execute(
        "INSERT INTO switchboard.connector_registry"
        " (connector_type, endpoint_identity, state, uptime_s, last_heartbeat_at)"
        " VALUES ($1, $2, $3, 100, $4)",
        connector_type,
        identity,
        state,
        datetime.now(UTC) - age,
    )


async def test_the_newest_steam_heartbeat_wins_over_other_connectors_and_the_config_row(
    pool,
) -> None:
    await _beat(pool, "steam", "steam:user:1", "degraded", timedelta(minutes=3))
    await _beat(pool, "steam", "steam:user:1,steam:user:2", "healthy", timedelta(minutes=1))
    # Newer than every Steam heartbeat, but not Steam's: another connector type, and the
    # Steam settings row that is stored under the same connector type.
    await _beat(pool, "telegram", "tg:bot", "error", timedelta(seconds=5))
    await _beat(pool, "steam", "steam:config", "error", timedelta(seconds=2))

    heartbeat = await steam_router._load_connector_heartbeat(pool)

    assert heartbeat is not None
    assert heartbeat["endpoint_identity"] == "steam:user:1,steam:user:2"
    assert heartbeat["state"] == "healthy"


async def test_a_registry_with_only_other_connectors_has_no_steam_heartbeat(pool) -> None:
    await _beat(pool, "telegram", "tg:bot", "healthy", timedelta(seconds=5))
    await _beat(pool, "steam", "steam:config", "healthy", timedelta(seconds=5))

    assert await steam_router._load_connector_heartbeat(pool) is None


async def test_the_route_reports_the_steam_state_not_a_newer_connectors(pool) -> None:
    await _beat(pool, "steam", "steam:user:1", "healthy", timedelta(seconds=30))
    await _beat(pool, "telegram", "tg:bot", "error", timedelta(seconds=5))
    manager = MagicMock()
    manager.pool = MagicMock(return_value=pool)

    response = await steam_router.get_steam_connector_health(db_manager=manager)

    assert response.status == "healthy"
    assert response.registry_available is True
