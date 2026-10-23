"""Channel kind is registered per connector (bu-xmueeq, REQ-connector-base-spec-003).

Device and telemetry feeds register ``capabilities.channel_kind = "feed"`` on their heartbeat, so
the kind is a Switchboard-owned registry fact that goal admission resolves by connector identity.
An ingest envelope cannot set it, and an unregistered or undeclared channel is a message channel.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors import google_calendar, google_drive, spotify
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.core.goal_admission import EvidenceRefused
from butlers.ingestion_spans import effective_channel_kind
from butlers.tools.switchboard.connector.heartbeat import (
    parse_connector_heartbeat,
    registered_channel_kind,
)

pytestmark = pytest.mark.unit

_CONNECTORS = Path(__file__).resolve().parents[2] / "src" / "butlers" / "connectors"
_FEED_MODULES = ("activitywatch", "google_health", "home_assistant", "owntracks", "steam")
_MESSAGE_MODULES = ("gmail", "telegram_bot", "telegram_user_client", "whatsapp_user_client")


def _heartbeat(**kwargs: Any) -> ConnectorHeartbeat:
    config = HeartbeatConfig(
        connector_type="home_assistant",
        endpoint_identity="home_assistant:house",
        interval_s=120,
    )
    metrics = MagicMock()
    return ConnectorHeartbeat(
        config, MagicMock(), metrics, get_health_state=lambda: ("healthy", None), **kwargs
    )


def test_heartbeat_registers_the_declared_kind_beside_other_capabilities() -> None:
    envelope = _heartbeat(
        channel_kind="feed", get_capabilities=lambda: {"backfill": True}
    )._build_envelope()

    assert envelope["capabilities"] == {"backfill": True, "channel_kind": "feed"}
    parsed = parse_connector_heartbeat(envelope)
    assert effective_channel_kind(parsed.capabilities) == "feed"


def test_heartbeat_without_a_kind_registers_nothing() -> None:
    assert "capabilities" not in _heartbeat()._build_envelope()


def test_switchboard_drops_an_unknown_kind_so_the_channel_stays_a_message_channel() -> None:
    envelope = _heartbeat(get_capabilities=lambda: {"channel_kind": "sensor"})._build_envelope()

    parsed = parse_connector_heartbeat(envelope)

    assert parsed.capabilities == {}
    assert effective_channel_kind(parsed.capabilities) == "message"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ({"channel_kind": "feed", "backfill": True}, "feed"),
        ('{"channel_kind": "feed"}', "feed"),
        ({"backfill": True}, "message"),
        (None, "message"),
    ],
)
async def test_admission_resolves_the_kind_from_the_registry_row(stored, expected) -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=stored)

    kind = await registered_channel_kind(pool, "home_assistant", "home_assistant:house")

    assert kind == expected
    assert pool.fetchval.await_args.args[1:] == ("home_assistant", "home_assistant:house")


async def test_unreadable_registry_refuses_admission() -> None:
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=OSError("registry down"))

    with pytest.raises(EvidenceRefused, match="channel_registry_unavailable"):
        await registered_channel_kind(pool, "steam", "steam:1")


def _heartbeat_kinds(module: str) -> list[str | None]:
    """The ``channel_kind=`` argument of every ``ConnectorHeartbeat(...)`` call in a module."""
    tree = ast.parse((_CONNECTORS / f"{module}.py").read_text())
    kinds: list[str | None] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ConnectorHeartbeat"
        ):
            arg = next((k.value for k in node.keywords if k.arg == "channel_kind"), None)
            kinds.append(arg.id if isinstance(arg, ast.Name) else None)
    return kinds


@pytest.mark.parametrize("module", _FEED_MODULES)
def test_feed_connectors_register_the_feed_kind_on_every_heartbeat(module: str) -> None:
    kinds = _heartbeat_kinds(module)

    assert kinds
    assert set(kinds) == {"CHANNEL_KIND_FEED"}


@pytest.mark.parametrize("module", _MESSAGE_MODULES)
def test_message_connectors_leave_the_kind_undeclared(module: str) -> None:
    assert set(_heartbeat_kinds(module)) <= {None}


@pytest.mark.parametrize(
    ("connector", "expected"),
    [("spotify", "feed"), ("google_calendar", "message"), ("google_drive", "feed")],
)
async def test_native_connector_registers_kind_through_its_heartbeat(
    connector: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise each real producer, then the Switchboard heartbeat parser. No source scan."""
    heartbeats: list[ConnectorHeartbeat] = []
    monkeypatch.setattr(ConnectorHeartbeat, "start", lambda hb: heartbeats.append(hb))
    if connector == "spotify":
        runtime = spotify.SpotifyConnector(
            spotify.SpotifyConnectorConfig(switchboard_mcp_url="http://switchboard.test/sse")
        )
        runtime._endpoint_identity = "spotify:user:synthetic"
        runtime._endpoint_identity_ready()
        heartbeats.append(runtime._heartbeat)
    elif connector == "google_calendar":
        runtime = google_calendar.CalendarConnectorRuntime(
            google_calendar.CalendarAccountConfig(
                email="synthetic@example.invalid",
                client_id="synthetic-client",
                client_secret="synthetic-secret",
                refresh_token="synthetic-refresh",
                switchboard_mcp_url="http://switchboard.test/sse",
            )
        )
        runtime._ingestion_policy.ensure_loaded = AsyncMock()
        runtime._global_ingestion_policy.ensure_loaded = AsyncMock()
        runtime._drain_replay_pending = AsyncMock()
        runtime._run_poll_loop = AsyncMock()
        await runtime.start()
    else:
        manager = google_drive.GDriveConnectorManager(db_pool=None, heartbeat_interval_s=0)
        manager._mcp_client = MagicMock()
        manager._start_heartbeat()
        await manager._account_heartbeat_task

        # Real Drive account identities bypass the MCP heartbeat and update their row directly.
        endpoint = "google_drive:user:synthetic@example.invalid"
        manager.get_health = MagicMock(
            return_value=SimpleNamespace(
                status="healthy",
                account_health=[SimpleNamespace(endpoint_identity=endpoint, status="healthy")],
            )
        )
        conn = MagicMock()

        async def updated(*_args):
            manager._running = False

        conn.execute = AsyncMock(side_effect=updated)
        manager._cursor_pool = MagicMock()
        manager._cursor_pool.acquire.return_value.__aenter__.return_value = conn
        manager._running = True
        await manager._account_heartbeat_loop()
        query, *args = conn.execute.await_args.args
        assert "capabilities = COALESCE(capabilities, '{}'::jsonb)" in query
        assert "jsonb_build_object('channel_kind', $6::text)" in query
        assert args[3:] == ["google_drive", endpoint, expected]
        assert "operational_role = 'runtime_instance'" in query

    assert len(heartbeats) == 1
    envelope = heartbeats[0]._build_envelope()
    assert envelope["capabilities"]["channel_kind"] == expected
    parsed = parse_connector_heartbeat(envelope)
    assert effective_channel_kind(parsed.capabilities) == expected
    if connector == "google_drive":
        assert parsed.capabilities["metadata_only"] is True
        assert parsed.capabilities["multi_account"] is True
