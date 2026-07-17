"""Unit tests for butlers.api.fleet_events_bridge (bu-01r64.1).

Covers the dashboard-api side of the cross-process NOTIFY/LISTEN bridge
(RFC 0022) without a real database:
- ``_on_notify`` parses a valid envelope and bridges it onto the real
  in-process event bus (``emit_event``)
- Malformed / wrong-channel / non-dict payloads are dropped, not raised
- ``run_fleet_events_listener`` registers a listener via ``add_listener``,
  reconnects after the connection closes, and reconnects after a connect
  failure — using fake connections so no Docker/Postgres is required
  (the real cross-process delivery path is covered by the two-pool
  integration test in tests/integration/).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.api import db as api_db
from butlers.api import deps as api_deps
from butlers.api import fleet_events_bridge
from butlers.api.deps import ButlerConnectionInfo, init_db_manager
from butlers.api.fleet_events_bridge import (
    FLEET_EVENTS_CHANNEL,
    _connect_listener,
    _on_notify,
    run_fleet_events_listener,
)
from butlers.db import Database

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_bus():
    from butlers.api.routers.events import _reset_events_bus_for_tests

    _reset_events_bus_for_tests()
    yield
    _reset_events_bus_for_tests()


# ---------------------------------------------------------------------------
# _on_notify — payload parsing / bridging
# ---------------------------------------------------------------------------


def test_on_notify_bridges_valid_envelope_to_event_bus():
    from butlers.api.routers.events import _events_ring

    payload = json.dumps({"type": "session", "data": {"phase": "started", "session_id": "s1"}})

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, payload)

    assert len(_events_ring) == 1
    event = _events_ring[-1]
    assert event["type"] == "session"
    assert event["data"] == {"phase": "started", "session_id": "s1"}


def test_on_notify_ignores_other_channels():
    from butlers.api.routers.events import _events_ring

    payload = json.dumps({"type": "session", "data": {}})

    _on_notify(None, 1, "some_other_channel", payload)

    assert len(_events_ring) == 0


def test_on_notify_drops_malformed_json():
    from butlers.api.routers.events import _events_ring

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, "{not json")

    assert len(_events_ring) == 0


def test_on_notify_drops_non_object_payload():
    from butlers.api.routers.events import _events_ring

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, json.dumps([1, 2, 3]))

    assert len(_events_ring) == 0


def test_on_notify_drops_payload_missing_type():
    from butlers.api.routers.events import _events_ring

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, json.dumps({"data": {"a": 1}}))

    assert len(_events_ring) == 0


def test_on_notify_defaults_non_dict_data_to_empty():
    from butlers.api.routers.events import _events_ring

    _on_notify(None, 1, FLEET_EVENTS_CHANNEL, json.dumps({"type": "spend", "data": "oops"}))

    assert len(_events_ring) == 1
    assert _events_ring[-1]["data"] == {}


# ---------------------------------------------------------------------------
# _connect_listener — environment target selection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("database_url", "postgres_db", "fallback_db", "expected_database"),
    [
        pytest.param(
            "postgresql://fleet_user:fleet_password@db.example:5432/url_fleet_events",
            "ignored_postgres_database",
            "butlers",
            "url_fleet_events",
            id="database-url-overrides-postgres-db-and-roster-fallback",
        ),
        pytest.param(
            "postgresql://fleet_user:fleet_password@db.example:5432/encoded%5Ffleet%2Dtarget",
            None,
            "butlers",
            "encoded_fleet-target",
            id="database-url-path-is-percent-decoded",
        ),
        pytest.param(
            None,
            "configured_postgres_database",
            "butlers",
            "configured_postgres_database",
            id="postgres-db-fallback",
        ),
        pytest.param(
            None,
            None,
            "butlers",
            "butlers",
            id="shared-default-fallback",
        ),
    ],
)
async def test_listener_daemon_publisher_and_api_pools_resolve_the_same_database_target(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str | None,
    postgres_db: str | None,
    fallback_db: str,
    expected_database: str,
) -> None:
    """Every fleet-event participant must use one database-scoped NOTIFY target."""
    captured_listener_kwargs: dict[str, object] = {}
    pool_targets: list[str] = []
    sentinel = object()

    async def capture_listener_connect(**kwargs: Any) -> object:
        captured_listener_kwargs.update(kwargs)
        return sentinel

    async def capture_create_pool(**kwargs: Any) -> AsyncMock:
        pool_targets.append(str(kwargs["database"]))
        return AsyncMock()

    async def skip_provision(_: Database) -> None:
        """Keep the startup wiring test at the asyncpg connection boundary."""

    if database_url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", database_url)
    if postgres_db is None:
        monkeypatch.delenv("POSTGRES_DB", raising=False)
    else:
        monkeypatch.setenv("POSTGRES_DB", postgres_db)
    monkeypatch.delenv("BUTLER_SHARED_DB_NAME", raising=False)
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", capture_listener_connect)
    monkeypatch.setattr(api_db.asyncpg, "create_pool", capture_create_pool)
    monkeypatch.setattr(Database, "provision", skip_provision)
    monkeypatch.setattr(api_deps, "ensure_secrets_schema", AsyncMock())
    monkeypatch.setattr(api_deps, "_db_manager", None)

    publisher_db = Database.from_env(fallback_db)
    api_manager = None
    try:
        await publisher_db.connect()
        api_manager = await init_db_manager(
            [
                ButlerConnectionInfo(
                    name="switchboard",
                    port=41100,
                    db_name=fallback_db,
                    db_schema="switchboard",
                )
            ]
        )

        assert publisher_db.db_name == expected_database
        assert await _connect_listener() is sentinel
        assert captured_listener_kwargs["database"] == publisher_db.db_name
        assert pool_targets == [expected_database, expected_database, expected_database]
    finally:
        await publisher_db.close()
        if api_manager is not None:
            await api_manager.close()
        monkeypatch.setattr(api_deps, "_db_manager", None)


def test_daemon_publisher_rejects_pathless_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A supplied URL cannot silently send publishers to a different database."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example:5432/")
    monkeypatch.setenv("POSTGRES_DB", "not_a_url_fallback")

    with pytest.raises(ValueError, match="must include a database path"):
        Database.from_env("butlers")


async def test_api_startup_rejects_pathless_database_url_before_opening_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed target must fail API startup before any API pool can diverge."""
    pool_targets: list[str] = []
    provision_targets: list[str] = []

    async def unexpected_create_pool(**kwargs: Any) -> AsyncMock:
        pool_targets.append(str(kwargs["database"]))
        raise AssertionError("pathless DATABASE_URL opened an API pool")

    async def unexpected_provision(db: Database) -> None:
        provision_targets.append(db.db_name)
        raise AssertionError("pathless DATABASE_URL provisioned a database")

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example:5432/")
    monkeypatch.setenv("POSTGRES_DB", "not_a_url_fallback")
    monkeypatch.setattr(api_db.asyncpg, "create_pool", unexpected_create_pool)
    monkeypatch.setattr(Database, "provision", unexpected_provision)
    monkeypatch.setattr(api_deps, "_db_manager", None)

    try:
        with pytest.raises(ValueError, match="must include a database path"):
            await init_db_manager(
                [
                    ButlerConnectionInfo(
                        name="switchboard",
                        port=41100,
                        db_name="butlers",
                        db_schema="switchboard",
                    )
                ]
            )
    finally:
        await api_deps.shutdown_db_manager()

    assert provision_targets == []
    assert pool_targets == []


async def test_api_startup_uses_resolved_targets_for_one_db_schema_topology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL target keeps shared credentials in public despite legacy roster names."""
    pool_kwargs: list[dict[str, Any]] = []

    async def capture_create_pool(**kwargs: Any) -> AsyncMock:
        pool_kwargs.append(kwargs)
        return AsyncMock()

    async def skip_provision(_: Database) -> None:
        """Keep this topology decision test at the pool-registration seam."""

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fleet_user:fleet_password@db.example:5432/canonical_target",
    )
    monkeypatch.setenv("POSTGRES_DB", "ignored_postgres_database")
    monkeypatch.delenv("BUTLER_SHARED_DB_NAME", raising=False)
    monkeypatch.setattr(api_db.asyncpg, "create_pool", capture_create_pool)
    monkeypatch.setattr(Database, "provision", skip_provision)
    monkeypatch.setattr(api_deps, "ensure_secrets_schema", AsyncMock())
    monkeypatch.setattr(api_deps, "_db_manager", None)

    api_manager = None
    try:
        api_manager = await init_db_manager(
            [
                ButlerConnectionInfo(
                    name="switchboard",
                    port=41100,
                    db_name="legacy_switchboard",
                    db_schema="switchboard",
                ),
                ButlerConnectionInfo(
                    name="general",
                    port=41101,
                    db_name="legacy_general",
                    db_schema="general",
                ),
            ]
        )

        assert [kwargs["database"] for kwargs in pool_kwargs] == [
            "canonical_target",
            "canonical_target",
            "canonical_target",
        ]
        assert pool_kwargs[-1]["server_settings"] == {"search_path": "public"}
    finally:
        if api_manager is not None:
            await api_manager.close()
        monkeypatch.setattr(api_deps, "_db_manager", None)


async def test_connect_listener_uses_decoded_database_url_path_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dedicated LISTEN connection must target DATABASE_URL's database."""
    captured_kwargs: dict[str, object] = {}
    sentinel = object()

    async def capture_connect(**kwargs: Any) -> object:
        captured_kwargs.update(kwargs)
        return sentinel

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fleet_user:fleet_password@db.example:5432/fleet%5Fevents%2Dtarget",
    )
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", capture_connect)

    assert await _connect_listener() is sentinel
    assert captured_kwargs["database"] == "fleet_events-target"


async def test_connect_listener_database_url_overrides_postgres_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSTGRES_DB cannot redirect a listener when DATABASE_URL is supplied."""
    captured_kwargs: dict[str, object] = {}

    async def capture_connect(**kwargs: Any) -> object:
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://fleet_user:fleet_password@db.example:5432/url_fleet_events",
    )
    monkeypatch.setenv("POSTGRES_DB", "ignored_fallback_database")
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", capture_connect)

    await _connect_listener()

    assert captured_kwargs["database"] == "url_fleet_events"


async def test_connect_listener_uses_postgres_db_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSTGRES_DB remains the explicit fallback when DATABASE_URL is absent."""
    captured_kwargs: dict[str, object] = {}

    async def capture_connect(**kwargs: Any) -> object:
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_DB", "configured_fleet_events_database")
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", capture_connect)

    await _connect_listener()

    assert captured_kwargs["database"] == "configured_fleet_events_database"


async def test_connect_listener_rejects_database_url_without_database_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pathless URL must not silently connect the listener elsewhere."""
    connect_calls: list[dict[str, Any]] = []

    async def unexpected_connect(**kwargs: Any) -> object:
        connect_calls.append(kwargs)
        raise AssertionError("pathless DATABASE_URL opened a listener connection")

    monkeypatch.setenv("DATABASE_URL", "postgresql://db.example:5432/")
    monkeypatch.setenv("POSTGRES_DB", "not_a_url_fallback")
    monkeypatch.setattr(fleet_events_bridge.asyncpg, "connect", unexpected_connect)

    with pytest.raises(ValueError, match="must include a database path"):
        await _connect_listener()

    assert connect_calls == []


# ---------------------------------------------------------------------------
# run_fleet_events_listener — connection lifecycle (fake connections)
# ---------------------------------------------------------------------------


class _FakeConnection:
    """Minimal stand-in for asyncpg.Connection: add_listener + is_closed/close."""

    def __init__(self) -> None:
        self._closed = False
        self.listeners: list[tuple[str, object]] = []

    async def add_listener(self, channel: str, callback: object) -> None:
        self.listeners.append((channel, callback))

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self._closed = True


async def test_run_fleet_events_listener_registers_listener_on_connect():
    conn = _FakeConnection()

    async def connect() -> _FakeConnection:
        return conn

    task = asyncio.create_task(run_fleet_events_listener(connect, health_poll_interval_s=0.01))
    try:
        for _ in range(200):
            if conn.listeners:
                break
            await asyncio.sleep(0.01)
        assert conn.listeners == [(FLEET_EVENTS_CHANNEL, _on_notify)]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_fleet_events_listener_reconnects_after_connection_closes():
    connections: list[_FakeConnection] = []

    async def connect() -> _FakeConnection:
        conn = _FakeConnection()
        connections.append(conn)
        return conn

    task = asyncio.create_task(
        run_fleet_events_listener(connect, health_poll_interval_s=0.01, reconnect_backoff_s=0.01)
    )
    try:
        # Wait for the first connection, then simulate it dying.
        for _ in range(200):
            if connections:
                break
            await asyncio.sleep(0.01)
        assert len(connections) == 1
        connections[0]._closed = True

        # A second connection should be established after the health poll
        # notices the first is closed and the reconnect backoff elapses.
        for _ in range(400):
            if len(connections) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(connections) >= 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_fleet_events_listener_reconnects_after_connect_failure():
    attempts = {"count": 0}

    async def connect() -> _FakeConnection:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionRefusedError("db not ready yet")
        return _FakeConnection()

    task = asyncio.create_task(
        run_fleet_events_listener(connect, health_poll_interval_s=0.01, reconnect_backoff_s=0.01)
    )
    try:
        for _ in range(400):
            if attempts["count"] >= 2:
                break
            await asyncio.sleep(0.01)
        assert attempts["count"] >= 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
