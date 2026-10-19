"""Steam connector health is read from the recorded heartbeat, never probed over HTTP.

The connector's health listener is bound to ``127.0.0.1`` and runs in another
container or pod on Compose and k3s, so ``GET /api/steam/connector/health`` used to
report ``not_running`` there whatever the connector's real state (bu-c4fweu). It now
reads the connector's row in ``switchboard.connector_registry`` and derives liveness
with the shared ``derive_liveness`` policy, like the other connector status cards.

Every test runs with outbound sockets forbidden, so a reintroduced loopback probe
fails the test instead of being swallowed as "connector unreachable".
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import butlers.api.routers.steam as steam_router

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_STEAM_ID = 76561198000000001
_OTHER_STEAM_ID = 76561198000000002


@pytest.fixture(autouse=True)
def forbid_sockets(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    attempts: list[Any] = []

    def _refuse(self: socket.socket, address: Any, *args: Any) -> None:
        attempts.append(address)
        raise AssertionError(f"outbound connection attempted: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    return attempts


def _heartbeat(
    *,
    state: str = "healthy",
    age: timedelta = timedelta(seconds=30),
    identity: str = f"steam:user:{_STEAM_ID}",
    uptime_s: int | None = 4200,
) -> dict[str, Any]:
    return {
        "endpoint_identity": identity,
        "state": state,
        "error_message": None,
        "uptime_s": uptime_s,
        "last_heartbeat_at": datetime.now(UTC) - age,
    }


def _db_manager(row: dict[str, Any] | None, *, query_error: Exception | None = None) -> Any:
    pool = MagicMock()
    if query_error is not None:
        pool.fetchrow = AsyncMock(side_effect=query_error)
    else:
        pool.fetchrow = AsyncMock(return_value=row)
    manager = MagicMock()
    manager.pool = MagicMock(return_value=pool)
    manager.fetchrow = pool.fetchrow
    return manager


# ---------------------------------------------------------------------------
# GET /api/steam/connector/health
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["healthy", "degraded", "error"])
async def test_online_heartbeat_reports_the_recorded_state(
    state: str, forbid_sockets: list[Any]
) -> None:
    manager = _db_manager(_heartbeat(state=state))

    response = await steam_router.get_steam_connector_health(db_manager=manager)

    assert response.status == state
    assert response.uptime_seconds == 4200
    assert response.active_accounts == 1
    assert response.last_heartbeat_at is not None
    assert response.source == "registry"
    assert response.connector_url is None
    assert response.account_health == []
    assert forbid_sockets == []


@pytest.mark.parametrize(
    "age", [timedelta(minutes=6), timedelta(minutes=20), timedelta(days=3)], ids=str
)
async def test_a_stale_or_offline_heartbeat_is_not_running_whatever_state_it_recorded(
    age: timedelta,
) -> None:
    manager = _db_manager(_heartbeat(state="healthy", age=age))

    response = await steam_router.get_steam_connector_health(db_manager=manager)

    assert response.status == "not_running"
    assert response.uptime_seconds is None
    assert response.active_accounts is None


async def test_a_future_dated_heartbeat_is_not_trusted() -> None:
    manager = _db_manager(_heartbeat(age=timedelta(hours=-2)))

    response = await steam_router.get_steam_connector_health(db_manager=manager)

    assert response.status == "not_running"


async def test_a_naive_timestamp_is_read_as_utc() -> None:
    row = _heartbeat()
    row["last_heartbeat_at"] = row["last_heartbeat_at"].replace(tzinfo=None)

    response = await steam_router.get_steam_connector_health(db_manager=_db_manager(row))

    assert response.status == "healthy"


@pytest.mark.parametrize(
    "manager",
    [
        pytest.param(None, id="no-db-manager"),
        pytest.param(_db_manager(None), id="no-registry-row"),
        pytest.param(_db_manager(None, query_error=RuntimeError("down")), id="query-error"),
    ],
)
async def test_no_usable_registry_answer_is_not_running(manager: Any) -> None:
    response = await steam_router.get_steam_connector_health(db_manager=manager)

    assert response.status == "not_running"
    assert response.connector_url is None


async def test_the_no_accounts_identity_counts_zero_accounts() -> None:
    manager = _db_manager(_heartbeat(state="degraded", identity="steam:no_accounts"))

    response = await steam_router.get_steam_connector_health(db_manager=manager)

    assert response.status == "degraded"
    assert response.active_accounts == 0


async def test_several_accounts_are_counted_from_the_joined_identity() -> None:
    identity = f"steam:user:{_STEAM_ID},steam:user:{_OTHER_STEAM_ID}"
    manager = _db_manager(_heartbeat(identity=identity))

    response = await steam_router.get_steam_connector_health(db_manager=manager)

    assert response.active_accounts == 2


async def test_the_query_targets_steam_heartbeats_and_skips_the_config_row() -> None:
    manager = _db_manager(_heartbeat())

    await steam_router.get_steam_connector_health(db_manager=manager)

    query, *args = manager.fetchrow.await_args.args
    assert "switchboard.connector_registry" in query
    assert "connector_type = 'steam'" in query
    assert "ORDER BY last_heartbeat_at DESC" in query
    assert args == ["steam:config"]


# ---------------------------------------------------------------------------
# Per-account connector_health on the account status route
# ---------------------------------------------------------------------------


async def test_account_health_is_the_recorded_state_for_a_tracked_account(
    forbid_sockets: list[Any],
) -> None:
    manager = _db_manager(_heartbeat(state="degraded"))

    health = await steam_router._probe_connector_account_health(manager, _STEAM_ID)

    assert health == "degraded"
    assert forbid_sockets == []


async def test_account_health_is_unknown_for_an_untracked_account() -> None:
    manager = _db_manager(_heartbeat(identity=f"steam:user:{_OTHER_STEAM_ID}"))

    assert await steam_router._probe_connector_account_health(manager, _STEAM_ID) is None


async def test_account_health_is_unknown_when_the_connector_is_not_online() -> None:
    manager = _db_manager(_heartbeat(age=timedelta(hours=1)))

    assert await steam_router._probe_connector_account_health(manager, _STEAM_ID) is None


async def test_account_health_does_not_match_a_longer_id_by_substring() -> None:
    manager = _db_manager(_heartbeat(identity=f"steam:user:{_STEAM_ID}0"))

    assert await steam_router._probe_connector_account_health(manager, _STEAM_ID) is None
