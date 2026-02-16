"""Integration tests for connector heartbeat observability stack.

These tests verify end-to-end behavior of:
- Heartbeat background task with real MCP client mocks
- Concurrent heartbeats from multiple connectors
- Failure resilience (switchboard unavailable, network errors)
- Metrics collection across multiple heartbeat cycles
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.heartbeat import (
    ConnectorHeartbeat,
    HeartbeatConfig,
)


@pytest.mark.asyncio
async def test_heartbeat_disabled_via_env_no_task_created():
    """Integration test: When disabled via env var, no background task is created."""
    with patch.dict(
        os.environ,
        {"CONNECTOR_HEARTBEAT_ENABLED": "false"},
        clear=True,
    ):
        config = HeartbeatConfig.from_env(
            connector_type="test_connector",
            endpoint_identity="test@example.com",
        )

        mock_mcp_client = AsyncMock()
        mock_metrics = MagicMock()
        get_health_state = MagicMock(return_value=("healthy", None))

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        # Start should be a no-op
        heartbeat.start()

        # Give it some time to potentially start
        await asyncio.sleep(0.5)

        # Verify no task was created
        assert heartbeat._task is None

        # Verify no MCP calls were made
        mock_mcp_client.call_tool.assert_not_called()

        # Stop should also be a no-op
        await heartbeat.stop()


@pytest.mark.asyncio
async def test_concurrent_heartbeats_from_multiple_connectors():
    """Integration test: Multiple connectors can send heartbeats concurrently."""
    config1 = HeartbeatConfig(
        connector_type="telegram_bot",
        endpoint_identity="bot@123",
        interval_s=1,
        enabled=True,
    )

    config2 = HeartbeatConfig(
        connector_type="gmail",
        endpoint_identity="user@example.com",
        interval_s=1,
        enabled=True,
    )

    mock_mcp_client1 = AsyncMock()
    mock_mcp_client1.call_tool = AsyncMock(return_value={"status": "accepted"})

    mock_mcp_client2 = AsyncMock()
    mock_mcp_client2.call_tool = AsyncMock(return_value={"status": "accepted"})

    mock_metrics1 = MagicMock()
    mock_metrics2 = MagicMock()

    get_health_state1 = MagicMock(return_value=("healthy", None))
    get_health_state2 = MagicMock(return_value=("healthy", None))

    with patch("prometheus_client.REGISTRY") as mock_registry:
        mock_registry.collect.return_value = []

        heartbeat1 = ConnectorHeartbeat(
            config=config1,
            mcp_client=mock_mcp_client1,
            metrics=mock_metrics1,
            get_health_state=get_health_state1,
        )

        heartbeat2 = ConnectorHeartbeat(
            config=config2,
            mcp_client=mock_mcp_client2,
            metrics=mock_metrics2,
            get_health_state=get_health_state2,
        )

        # Start both heartbeats
        heartbeat1.start()
        heartbeat2.start()

        # Wait for at least 2 cycles
        await asyncio.sleep(2.5)

        # Stop both heartbeats
        await heartbeat1.stop()
        await heartbeat2.stop()

        # Verify both sent heartbeats
        assert mock_mcp_client1.call_tool.call_count >= 2
        assert mock_mcp_client2.call_tool.call_count >= 2

        # Verify each sent correct connector info
        envelope1 = mock_mcp_client1.call_tool.call_args_list[0].args[1]
        assert envelope1["connector"]["connector_type"] == "telegram_bot"
        assert envelope1["connector"]["endpoint_identity"] == "bot@123"

        envelope2 = mock_mcp_client2.call_tool.call_args_list[0].args[1]
        assert envelope2["connector"]["connector_type"] == "gmail"
        assert envelope2["connector"]["endpoint_identity"] == "user@example.com"

        # Verify instance IDs are different
        assert envelope1["connector"]["instance_id"] != envelope2["connector"]["instance_id"]


@pytest.mark.asyncio
async def test_heartbeat_resilience_switchboard_connection_errors():
    """Integration test: Heartbeat task continues running despite switchboard errors."""
    config = HeartbeatConfig(
        connector_type="test_connector",
        endpoint_identity="test@example.com",
        interval_s=1,
        enabled=True,
    )

    # Mock MCP client that alternates between success and failure
    call_count = 0

    async def alternating_call_tool(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 0:
            # Even calls fail
            raise RuntimeError("Switchboard connection failed")
        # Odd calls succeed
        return {"status": "accepted"}

    mock_mcp_client = AsyncMock()
    mock_mcp_client.call_tool = AsyncMock(side_effect=alternating_call_tool)

    mock_metrics = MagicMock()
    get_health_state = MagicMock(return_value=("healthy", None))

    with patch("prometheus_client.REGISTRY") as mock_registry:
        mock_registry.collect.return_value = []

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        heartbeat.start()

        # Wait for multiple cycles (some will fail, some succeed)
        await asyncio.sleep(3.5)

        # Task should still be running despite failures
        assert heartbeat._task is not None
        assert not heartbeat._task.done()

        await heartbeat.stop()

        # Verify it attempted multiple heartbeats despite failures
        assert mock_mcp_client.call_tool.call_count >= 3


@pytest.mark.asyncio
async def test_heartbeat_resilience_all_calls_fail():
    """Integration test: Heartbeat task continues even when all calls fail."""
    config = HeartbeatConfig(
        connector_type="test_connector",
        endpoint_identity="test@example.com",
        interval_s=1,
        enabled=True,
    )

    # Mock MCP client that always fails
    mock_mcp_client = AsyncMock()
    mock_mcp_client.call_tool = AsyncMock(side_effect=RuntimeError("Switchboard unreachable"))

    mock_metrics = MagicMock()
    get_health_state = MagicMock(return_value=("degraded", "Cannot reach switchboard"))

    with patch("prometheus_client.REGISTRY") as mock_registry:
        mock_registry.collect.return_value = []

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        heartbeat.start()

        # Wait for multiple failed attempts
        await asyncio.sleep(2.5)

        # Task should still be running
        assert heartbeat._task is not None
        assert not heartbeat._task.done()

        await heartbeat.stop()

        # Verify it attempted multiple heartbeats
        assert mock_mcp_client.call_tool.call_count >= 2


@pytest.mark.asyncio
async def test_heartbeat_metrics_collection_across_multiple_cycles():
    """Integration test: Metrics are collected correctly across multiple cycles."""
    config = HeartbeatConfig(
        connector_type="test_connector",
        endpoint_identity="test@example.com",
        interval_s=1,
        enabled=True,
    )

    mock_mcp_client = AsyncMock()
    mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})

    mock_metrics = MagicMock()
    get_health_state = MagicMock(return_value=("healthy", None))

    # Track how counter values change across cycles
    cycle_counter_values = []

    with patch("prometheus_client.REGISTRY") as mock_registry:
        # Mock Prometheus metrics that change over time
        call_iteration = [0]

        def mock_collect():
            call_iteration[0] += 1
            current_iteration = call_iteration[0]

            # Simulate increasing counter values
            mock_metric = MagicMock()
            mock_metric.name = "connector_ingest_submissions_total"
            mock_metric.samples = [
                MagicMock(
                    labels={
                        "connector_type": "test_connector",
                        "endpoint_identity": "test@example.com",
                        "status": "success",
                    },
                    value=10 * current_iteration,  # Increases each cycle
                ),
            ]
            return [mock_metric]

        mock_registry.collect.side_effect = mock_collect

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        heartbeat.start()

        # Wait for 3 cycles
        await asyncio.sleep(3.5)

        await heartbeat.stop()

        # Verify we got at least 3 heartbeat calls
        assert mock_mcp_client.call_tool.call_count >= 3

        # Extract counter values from each call
        for call in mock_mcp_client.call_tool.call_args_list:
            envelope = call.args[1]
            cycle_counter_values.append(envelope["counters"]["messages_ingested"])

        # Verify counter values increased across cycles
        assert len(cycle_counter_values) >= 3
        assert cycle_counter_values[1] > cycle_counter_values[0]
        assert cycle_counter_values[2] > cycle_counter_values[1]


@pytest.mark.asyncio
async def test_heartbeat_instance_id_stability_across_cycles():
    """Integration test: Instance ID remains stable across multiple heartbeat cycles."""
    config = HeartbeatConfig(
        connector_type="test_connector",
        endpoint_identity="test@example.com",
        interval_s=1,
        enabled=True,
    )

    mock_mcp_client = AsyncMock()
    mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})

    mock_metrics = MagicMock()
    get_health_state = MagicMock(return_value=("healthy", None))

    with patch("prometheus_client.REGISTRY") as mock_registry:
        mock_registry.collect.return_value = []

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        heartbeat.start()

        # Wait for multiple cycles
        await asyncio.sleep(2.5)

        await heartbeat.stop()

        # Extract instance IDs from all calls
        instance_ids = set()
        for call in mock_mcp_client.call_tool.call_args_list:
            envelope = call.args[1]
            instance_ids.add(envelope["connector"]["instance_id"])

        # All heartbeats should have the same instance_id
        assert len(instance_ids) == 1


@pytest.mark.asyncio
async def test_heartbeat_uptime_counter_increases():
    """Integration test: Uptime counter increases monotonically across cycles."""
    config = HeartbeatConfig(
        connector_type="test_connector",
        endpoint_identity="test@example.com",
        interval_s=1,
        enabled=True,
    )

    mock_mcp_client = AsyncMock()
    mock_mcp_client.call_tool = AsyncMock(return_value={"status": "accepted"})

    mock_metrics = MagicMock()
    get_health_state = MagicMock(return_value=("healthy", None))

    with patch("prometheus_client.REGISTRY") as mock_registry:
        mock_registry.collect.return_value = []

        heartbeat = ConnectorHeartbeat(
            config=config,
            mcp_client=mock_mcp_client,
            metrics=mock_metrics,
            get_health_state=get_health_state,
        )

        heartbeat.start()

        # Wait for multiple cycles
        await asyncio.sleep(2.5)

        await heartbeat.stop()

        # Extract uptime values from all calls
        uptime_values = []
        for call in mock_mcp_client.call_tool.call_args_list:
            envelope = call.args[1]
            uptime_values.append(envelope["status"]["uptime_s"])

        # Verify we got at least 2 cycles
        assert len(uptime_values) >= 2

        # Verify uptime increases monotonically
        for i in range(1, len(uptime_values)):
            assert uptime_values[i] > uptime_values[i - 1]
