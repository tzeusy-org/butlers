"""Tests for connector heartbeat background task."""

import asyncio
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from butlers.connectors.heartbeat import (
    DEFAULT_HEARTBEAT_INTERVAL_S,
    MAX_HEARTBEAT_INTERVAL_S,
    MIN_HEARTBEAT_INTERVAL_S,
    ConnectorHeartbeat,
    HeartbeatConfig,
)

pytestmark = pytest.mark.unit

# Real sleep used by fast_sleep to yield control without blocking
_real_sleep = asyncio.sleep


async def _fast_sleep(delay: float) -> None:
    """Mock sleep that yields control to the event loop without real delay."""
    await _real_sleep(0)


class TestHeartbeatConfig:
    """Test HeartbeatConfig.from_env()."""

    def test_from_env_defaults(self):
        """Test loading config with default values."""
        with patch.dict(os.environ, {}, clear=True):
            config = HeartbeatConfig.from_env(
                connector_type="test_connector",
                endpoint_identity="test@example.com",
            )

        assert config.connector_type == "test_connector"
        assert config.endpoint_identity == "test@example.com"
        assert config.version is None
        assert config.interval_s == DEFAULT_HEARTBEAT_INTERVAL_S
        assert config.enabled is True

    def test_from_env_custom_interval(self):
        """Test loading config with custom interval."""
        with patch.dict(
            os.environ,
            {"CONNECTOR_HEARTBEAT_INTERVAL_S": "60"},
            clear=True,
        ):
            config = HeartbeatConfig.from_env(
                connector_type="test_connector",
                endpoint_identity="test@example.com",
            )

        assert config.interval_s == 60

    def test_from_env_disabled(self):
        """Test loading config with heartbeat disabled."""
        for disabled_value in ["false", "0", "no", "off", "False"]:
            with patch.dict(
                os.environ,
                {"CONNECTOR_HEARTBEAT_ENABLED": disabled_value},
                clear=True,
            ):
                config = HeartbeatConfig.from_env(
                    connector_type="test_connector",
                    endpoint_identity="test@example.com",
                )

            assert config.enabled is False

    def test_from_env_interval_bounds_min(self):
        """Test interval bounded to minimum."""
        with patch.dict(
            os.environ,
            {"CONNECTOR_HEARTBEAT_INTERVAL_S": "10"},
            clear=True,
        ):
            config = HeartbeatConfig.from_env(
                connector_type="test_connector",
                endpoint_identity="test@example.com",
            )

        assert config.interval_s == MIN_HEARTBEAT_INTERVAL_S

    def test_from_env_interval_bounds_max(self):
        """Test interval bounded to maximum."""
        with patch.dict(
            os.environ,
            {"CONNECTOR_HEARTBEAT_INTERVAL_S": "1000"},
            clear=True,
        ):
            config = HeartbeatConfig.from_env(
                connector_type="test_connector",
                endpoint_identity="test@example.com",
            )

        assert config.interval_s == MAX_HEARTBEAT_INTERVAL_S

    def test_from_env_with_version(self):
        """Test loading config with version."""
        config = HeartbeatConfig.from_env(
            connector_type="test_connector",
            endpoint_identity="test@example.com",
            version="1.2.3",
        )

        assert config.version == "1.2.3"


class TestConnectorHeartbeat:
    """Test ConnectorHeartbeat background task."""

    @pytest.fixture
    def mock_mcp_client(self):
        """Create a mock MCP client."""
        client = AsyncMock()
        client.call_tool = AsyncMock(return_value={"status": "accepted"})
        return client

    @pytest.fixture
    def mock_metrics(self):
        """Create a mock metrics collector."""
        metrics = MagicMock()
        return metrics

    @pytest.fixture
    def config(self):
        """Create a test heartbeat config."""
        return HeartbeatConfig(
            connector_type="test_connector",
            endpoint_identity="test@example.com",
            interval_s=1,  # Fast interval for testing
            enabled=True,
        )

    @pytest.fixture
    def get_health_state(self):
        """Create a mock health state getter."""
        return MagicMock(return_value=("healthy", None))

    @pytest.fixture
    def get_checkpoint(self):
        """Create a mock checkpoint getter."""
        return MagicMock(return_value=("checkpoint-cursor", datetime.now(UTC)))

    def test_init_generates_instance_id(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test that initialization generates a stable instance_id."""
        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        assert isinstance(heartbeat.instance_id, UUID)

    def test_instance_id_stable(self, config, mock_mcp_client, mock_metrics, get_health_state):
        """Test that instance_id remains stable across calls."""
        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        instance_id_1 = heartbeat.instance_id
        instance_id_2 = heartbeat.instance_id

        assert instance_id_1 == instance_id_2

    def test_instance_id_different_per_instance(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test that different instances get different instance_ids."""
        heartbeat1 = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        heartbeat2 = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        assert heartbeat1.instance_id != heartbeat2.instance_id

    def test_start_when_disabled(self, mock_mcp_client, mock_metrics, get_health_state):
        """Test that start() does nothing when heartbeat is disabled."""
        config = HeartbeatConfig(
            connector_type="test_connector",
            endpoint_identity="test@example.com",
            interval_s=1,
            enabled=False,
        )

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        heartbeat.start()

        # Task should not be created
        assert heartbeat._task is None

    @pytest.mark.asyncio
    async def test_heartbeat_sends_periodically(
        self, config, mock_mcp_client, mock_metrics, get_health_state, get_checkpoint
    ):
        """Test that heartbeat task sends heartbeats periodically."""
        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            # Mock metrics collection
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
                get_checkpoint=get_checkpoint,
            )

            heartbeat.start()

            # Yield control so fast_sleep can fire multiple heartbeat cycles
            await _real_sleep(0)
            await _real_sleep(0)
            await _real_sleep(0)

            await heartbeat.stop()

            # Should have called the tool at least 2 times
            assert mock_mcp_client.call_tool.call_count >= 2

            # Verify the tool name and envelope structure
            for call in mock_mcp_client.call_tool.call_args_list:
                tool_name, envelope = call.args
                assert tool_name == "connector.heartbeat"
                assert envelope["schema_version"] == "connector.heartbeat.v1"
                assert envelope["connector"]["connector_type"] == "test_connector"
                assert envelope["connector"]["endpoint_identity"] == "test@example.com"
                assert "instance_id" in envelope["connector"]
                assert envelope["status"]["state"] == "healthy"

    @pytest.mark.asyncio
    async def test_heartbeat_envelope_structure(
        self, config, mock_mcp_client, mock_metrics, get_health_state, get_checkpoint
    ):
        """Test that heartbeat envelope has correct structure."""
        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            # Mock metrics collection
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
                get_checkpoint=get_checkpoint,
            )

            heartbeat.start()
            await _real_sleep(0)
            await _real_sleep(0)
            await heartbeat.stop()

            # Get the envelope from the first call
            envelope = mock_mcp_client.call_tool.call_args_list[0].args[1]

            # Verify connector section
            assert envelope["connector"]["connector_type"] == "test_connector"
            assert envelope["connector"]["endpoint_identity"] == "test@example.com"
            assert isinstance(UUID(envelope["connector"]["instance_id"]), UUID)

            # Verify status section
            assert envelope["status"]["state"] in ("healthy", "degraded", "error")
            assert isinstance(envelope["status"]["uptime_s"], int)

            # Verify counters section
            assert "messages_ingested" in envelope["counters"]
            assert "messages_failed" in envelope["counters"]
            assert "source_api_calls" in envelope["counters"]
            assert "checkpoint_saves" in envelope["counters"]
            assert "dedupe_accepted" in envelope["counters"]

            # Verify checkpoint section
            assert "checkpoint" in envelope
            assert envelope["checkpoint"]["cursor"] == "checkpoint-cursor"

            # Verify sent_at timestamp
            assert "sent_at" in envelope

    @pytest.mark.asyncio
    async def test_heartbeat_includes_health_state(
        self, config, mock_mcp_client, mock_metrics, get_checkpoint
    ):
        """Test that heartbeat includes health state from get_health_state callback."""
        health_state = MagicMock(return_value=("error", "Source API unreachable"))

        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=health_state,
                get_checkpoint=get_checkpoint,
            )

            heartbeat.start()
            await _real_sleep(0)
            await _real_sleep(0)
            await heartbeat.stop()

            envelope = mock_mcp_client.call_tool.call_args_list[0].args[1]

            assert envelope["status"]["state"] == "error"
            assert envelope["status"]["error_message"] == "Source API unreachable"

    @pytest.mark.asyncio
    async def test_heartbeat_graceful_shutdown(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test that heartbeat task stops gracefully."""
        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
            )

            heartbeat.start()
            assert heartbeat._task is not None

            await heartbeat.stop()

            # Task should be None after stop
            assert heartbeat._task is None

    @pytest.mark.asyncio
    async def test_heartbeat_failure_does_not_crash(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test that heartbeat failures are logged but don't crash the loop."""
        # Make the MCP client raise an exception
        mock_mcp_client.call_tool = AsyncMock(side_effect=RuntimeError("MCP error"))

        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
            )

            heartbeat.start()

            # Yield control for multiple heartbeat cycles
            await _real_sleep(0)
            await _real_sleep(0)
            await _real_sleep(0)

            # Task should still be running despite failures
            assert heartbeat._task is not None
            assert not heartbeat._task.done()

            await heartbeat.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_without_checkpoint(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test heartbeat when get_checkpoint is not provided."""
        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
                get_checkpoint=None,  # No checkpoint callback
            )

            heartbeat.start()
            await _real_sleep(0)
            await _real_sleep(0)
            await heartbeat.stop()

            envelope = mock_mcp_client.call_tool.call_args_list[0].args[1]

            # Checkpoint should not be present in envelope
            assert "checkpoint" not in envelope or envelope.get("checkpoint") is None

    @pytest.mark.asyncio
    async def test_collect_counters_from_prometheus(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test that counters are collected from Prometheus registry."""
        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            # Mock Prometheus metrics
            mock_metric_ingest = MagicMock()
            mock_metric_ingest.name = "connector_ingest_submissions_total"
            mock_metric_ingest.samples = [
                MagicMock(
                    labels={
                        "connector_type": "test_connector",
                        "endpoint_identity": "test@example.com",
                        "status": "success",
                    },
                    value=42,
                ),
                MagicMock(
                    labels={
                        "connector_type": "test_connector",
                        "endpoint_identity": "test@example.com",
                        "status": "error",
                    },
                    value=3,
                ),
                MagicMock(
                    labels={
                        "connector_type": "test_connector",
                        "endpoint_identity": "test@example.com",
                        "status": "duplicate",
                    },
                    value=5,
                ),
            ]

            mock_metric_api = MagicMock()
            mock_metric_api.name = "connector_source_api_calls_total"
            mock_metric_api.samples = [
                MagicMock(
                    labels={
                        "connector_type": "test_connector",
                        "endpoint_identity": "test@example.com",
                        "api_method": "getUpdates",
                        "status": "success",
                    },
                    value=100,
                ),
            ]

            mock_metric_checkpoint = MagicMock()
            mock_metric_checkpoint.name = "connector_checkpoint_saves_total"
            mock_metric_checkpoint.samples = [
                MagicMock(
                    labels={
                        "connector_type": "test_connector",
                        "endpoint_identity": "test@example.com",
                        "status": "success",
                    },
                    value=10,
                ),
            ]

            mock_registry.collect.return_value = [
                mock_metric_ingest,
                mock_metric_api,
                mock_metric_checkpoint,
            ]

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
            )

            heartbeat.start()
            await _real_sleep(0)
            await _real_sleep(0)
            await heartbeat.stop()

            envelope = mock_mcp_client.call_tool.call_args_list[0].args[1]

            # Verify counters
            assert envelope["counters"]["messages_ingested"] == 42
            assert envelope["counters"]["messages_failed"] == 3
            assert envelope["counters"]["dedupe_accepted"] == 5
            assert envelope["counters"]["source_api_calls"] == 100
            assert envelope["counters"]["checkpoint_saves"] == 10

    @pytest.mark.asyncio
    async def test_heartbeat_includes_capabilities_when_provided(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test that heartbeat envelope includes capabilities when get_capabilities is provided."""
        get_capabilities = MagicMock(return_value={"backfill": True})

        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
                get_capabilities=get_capabilities,
            )

            heartbeat.start()
            await _real_sleep(0)
            await _real_sleep(0)
            await heartbeat.stop()

            envelope = mock_mcp_client.call_tool.call_args_list[0].args[1]
            assert "capabilities" in envelope
            assert envelope["capabilities"] == {"backfill": True}

    @pytest.mark.asyncio
    async def test_heartbeat_omits_capabilities_when_not_provided(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test that capabilities key is absent when get_capabilities is not provided."""
        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
                get_capabilities=None,
            )

            heartbeat.start()
            await _real_sleep(0)
            await _real_sleep(0)
            await heartbeat.stop()

            envelope = mock_mcp_client.call_tool.call_args_list[0].args[1]
            assert "capabilities" not in envelope

    @pytest.mark.asyncio
    async def test_heartbeat_omits_capabilities_when_empty_dict_returned(
        self, config, mock_mcp_client, mock_metrics, get_health_state
    ):
        """Test that capabilities key is absent when get_capabilities returns empty dict."""
        get_capabilities = MagicMock(return_value={})

        with (
            patch("prometheus_client.REGISTRY") as mock_registry,
            patch("butlers.connectors.heartbeat.asyncio.sleep", side_effect=_fast_sleep),
        ):
            mock_registry.collect.return_value = []

            heartbeat = ConnectorHeartbeat(
                config=config,
                mcp_client=mock_mcp_client,
                metrics=mock_metrics,
                get_health_state=get_health_state,
                get_capabilities=get_capabilities,
            )

            heartbeat.start()
            await _real_sleep(0)
            await _real_sleep(0)
            await heartbeat.stop()

            envelope = mock_mcp_client.call_tool.call_args_list[0].args[1]
            # Empty capabilities dict => omitted from envelope (falsy guard)
            assert "capabilities" not in envelope
