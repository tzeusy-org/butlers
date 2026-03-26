"""Tests for HA connector health state, heartbeat, and Prometheus metrics.

Covers openspec/changes/connector-home-assistant/tasks.md §8 (tasks 8.1–8.5):

8.1 — Health state derivation (healthy/degraded/error based on WS, REST, discretion)
8.2 — Heartbeat assembly with transport mode in status.error_message
8.3 — HA-specific Prometheus counters (ws_reconnects, rest_polls, events, discretion)
8.4 — HA-specific Prometheus gauges (filter_pass_rate, transport_mode, entities_tracked)
8.5 — HA-specific Prometheus histograms (event_latency_seconds, filter_pipeline_seconds)

No real network I/O is performed; all HA, MCP, and DB calls are mocked or bypassed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from butlers.connectors.home_assistant import (
    HAConnector,
    HAConnectorConfig,
    HAConnectorMetrics,
    ha_discretion_total,
    ha_entities_tracked,
    ha_event_latency_seconds,
    ha_events_total,
    ha_filter_pass_rate,
    ha_filter_pipeline_seconds,
    ha_rest_polls_total,
    ha_transport_mode,
    ha_ws_reconnects_total,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> HAConnectorConfig:
    """Minimal HAConnectorConfig for unit tests."""
    return HAConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        health_port=40087,
    )


@pytest.fixture
def connector(config: HAConnectorConfig) -> HAConnector:
    """HAConnector with endpoint identity set and metrics initialized."""
    c = HAConnector(config=config)
    c._set_endpoint_identity("http://ha.local:8123")
    return c


@pytest.fixture(autouse=True)
def clear_ha_metrics() -> None:
    """Clear HA-specific metric state before each test to avoid cross-test pollution."""
    for collector in [
        ha_ws_reconnects_total,
        ha_rest_polls_total,
        ha_events_total,
        ha_discretion_total,
    ]:
        collector._metrics.clear()

    for gauge_collector in [
        ha_filter_pass_rate,
        ha_transport_mode,
        ha_entities_tracked,
    ]:
        gauge_collector._metrics.clear()

    for hist_collector in [
        ha_event_latency_seconds,
        ha_filter_pipeline_seconds,
    ]:
        hist_collector._metrics.clear()


def _get_counter(counter, **labels) -> float:
    """Read a Prometheus counter value."""
    return counter.labels(**labels)._value.get()


def _get_gauge(gauge, **labels) -> float:
    """Read a Prometheus gauge value."""
    return gauge.labels(**labels)._value.get()


def _get_histogram_count(histogram, **labels) -> float:
    """Read the _count sample of a Prometheus histogram."""
    samples = list(histogram.collect()[0].samples)
    target = dict(labels)
    count_s = next(
        (s for s in samples if s.name.endswith("_count") and dict(s.labels) == target),
        None,
    )
    return count_s.value if count_s is not None else 0.0


def _get_histogram_sum(histogram, **labels) -> float:
    """Read the _sum sample of a Prometheus histogram."""
    samples = list(histogram.collect()[0].samples)
    target = dict(labels)
    sum_s = next(
        (s for s in samples if s.name.endswith("_sum") and dict(s.labels) == target),
        None,
    )
    return sum_s.value if sum_s is not None else 0.0


# ---------------------------------------------------------------------------
# Task 8.1 — Health state derivation
# ---------------------------------------------------------------------------


class TestHealthStateDerivation:
    """_get_health_state returns correct state based on WS/REST/discretion status."""

    def test_starting_state_before_any_connection(self, config: HAConnectorConfig) -> None:
        """Health is 'starting' when the connector has not attempted any connection yet."""
        c = HAConnector(config=config)
        c._set_endpoint_identity("http://ha.local:8123")
        # _starting is True by default
        state, error_msg = c._get_health_state()
        assert state == "starting"
        assert error_msg is not None
        assert "starting" in error_msg

    def test_healthy_when_ws_connected(self, connector: HAConnector) -> None:
        """Health is 'healthy' when WebSocket is connected."""
        connector.on_ws_connected()
        state, error_msg = connector._get_health_state()
        assert state == "healthy"
        assert error_msg is not None
        assert "transport=websocket" in error_msg

    def test_degraded_when_ws_down_rest_active(self, connector: HAConnector) -> None:
        """Health is 'degraded' when WebSocket is down but REST polling is active."""
        connector.on_rest_fallback_started()
        connector._ws_connected = False
        state, error_msg = connector._get_health_state()
        assert state == "degraded"
        assert error_msg is not None
        assert "rest_fallback" in error_msg

    def test_degraded_when_ws_connected_but_discretion_unavailable(
        self, connector: HAConnector
    ) -> None:
        """Health is 'degraded' when WS connected but discretion LLM is unavailable."""
        connector.on_ws_connected()
        connector.on_discretion_result(available=False)
        state, error_msg = connector._get_health_state()
        assert state == "degraded"
        assert error_msg is not None
        assert "Discretion LLM unavailable" in error_msg

    def test_error_when_ws_down_and_rest_failing(self, connector: HAConnector) -> None:
        """Health is 'error' when both WS is down and REST polling is failing."""
        connector.on_rest_fallback_started()
        connector._ws_connected = False
        connector.on_rest_poll_error()  # REST also failing
        state, error_msg = connector._get_health_state()
        assert state == "error"
        assert error_msg is not None
        assert "HA unreachable" in error_msg

    def test_healthy_restored_after_discretion_becomes_available(
        self, connector: HAConnector
    ) -> None:
        """Health returns to 'healthy' once discretion LLM becomes available again."""
        connector.on_ws_connected()
        connector.on_discretion_result(available=False)
        assert connector._get_health_state()[0] == "degraded"

        connector.on_discretion_result(available=True, verdict="forward")
        assert connector._get_health_state()[0] == "healthy"

    def test_degraded_transitions_to_healthy_on_ws_reconnect(self, connector: HAConnector) -> None:
        """Health transitions degraded → healthy when WebSocket reconnects."""
        connector.on_rest_fallback_started()
        connector._ws_connected = False
        assert connector._get_health_state()[0] == "degraded"

        connector.on_ws_connected()
        assert connector._get_health_state()[0] == "healthy"


# ---------------------------------------------------------------------------
# Task 8.2 — Heartbeat transport mode in status.error_message
# ---------------------------------------------------------------------------


class TestHeartbeatTransportMode:
    """Transport mode appears in heartbeat status.error_message."""

    def test_transport_websocket_message_when_connected(self, connector: HAConnector) -> None:
        """Transport message is 'transport=websocket' when WS is connected."""
        connector.on_ws_connected()
        msg = connector._build_transport_message()
        assert msg == "transport=websocket"

    def test_transport_rest_fallback_message_with_attempt_count(
        self, connector: HAConnector
    ) -> None:
        """Transport message includes attempt count when on REST fallback."""
        connector.on_ws_disconnected()  # increments to 1
        connector.on_rest_fallback_started()
        msg = connector._build_transport_message()
        assert "transport=rest_fallback" in msg
        assert "ws_reconnect_attempts=1" in msg

    def test_transport_message_accumulates_reconnect_attempts(self, connector: HAConnector) -> None:
        """Reconnect attempt count increases with each disconnect."""
        for _ in range(3):
            connector.on_ws_disconnected()
        connector.on_rest_fallback_started()
        msg = connector._build_transport_message()
        assert "ws_reconnect_attempts=3" in msg

    def test_transport_message_in_health_state_error_message(self, connector: HAConnector) -> None:
        """Transport message is embedded in _get_health_state error_message."""
        connector.on_ws_connected()
        _, error_msg = connector._get_health_state()
        assert error_msg is not None
        assert "transport=websocket" in error_msg

    def test_heartbeat_is_initialized_after_set_endpoint_identity(
        self, config: HAConnectorConfig
    ) -> None:
        """Heartbeat object is created when endpoint identity is set."""
        c = HAConnector(config=config)
        assert c._heartbeat is None  # not yet initialized
        c._set_endpoint_identity("http://ha.local:8123")
        assert c._heartbeat is not None
        assert c._heartbeat._config.connector_type == "home_assistant"
        assert c._heartbeat._config.endpoint_identity == "home_assistant:ha.local:8123"

    def test_heartbeat_get_health_state_callback_is_wired(self, config: HAConnectorConfig) -> None:
        """Heartbeat's get_health_state callback returns health state from connector."""
        c = HAConnector(config=config)
        c._set_endpoint_identity("http://ha.local:8123")
        c.on_ws_connected()

        # The heartbeat holds a reference to the connector's _get_health_state
        state, _ = c._heartbeat._get_health_state()
        assert state == "healthy"


# ---------------------------------------------------------------------------
# Task 8.3 — HA-specific counters
# ---------------------------------------------------------------------------


class TestHACounters:
    """HA-specific Prometheus counters increment correctly."""

    def test_ws_reconnects_total_increments_on_disconnect(self, connector: HAConnector) -> None:
        """connector_ha_ws_reconnects_total increments when WS disconnects."""
        connector.on_ws_connected()
        connector.on_ws_disconnected()

        count = _get_counter(
            ha_ws_reconnects_total,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert count == 1.0

    def test_ws_reconnects_accumulates(self, connector: HAConnector) -> None:
        """ws_reconnects_total accumulates across multiple disconnects."""
        for _ in range(3):
            connector.on_ws_connected()
            connector.on_ws_disconnected()

        count = _get_counter(
            ha_ws_reconnects_total,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert count == 3.0

    def test_rest_polls_success(self, connector: HAConnector) -> None:
        """connector_ha_rest_polls_total increments on successful REST poll."""
        connector.on_rest_poll_success()

        count = _get_counter(
            ha_rest_polls_total,
            endpoint_identity="home_assistant:ha.local:8123",
            status="success",
        )
        assert count == 1.0

    def test_rest_polls_error(self, connector: HAConnector) -> None:
        """connector_ha_rest_polls_total increments on failed REST poll."""
        connector.on_rest_poll_error()

        count = _get_counter(
            ha_rest_polls_total,
            endpoint_identity="home_assistant:ha.local:8123",
            status="error",
        )
        assert count == 1.0

    def test_events_total_domain_filter_passed(self, connector: HAConnector) -> None:
        """connector_ha_events_total increments for domain_filter stage."""
        assert connector._ha_metrics is not None
        connector._ha_metrics.inc_events(stage="domain_filter", outcome="passed")

        count = _get_counter(ha_events_total, stage="domain_filter", outcome="passed")
        assert count == 1.0

    def test_events_total_domain_filter_filtered(self, connector: HAConnector) -> None:
        """connector_ha_events_total increments for domain_filter filtered events."""
        assert connector._ha_metrics is not None
        connector._ha_metrics.inc_events(stage="domain_filter", outcome="filtered")

        count = _get_counter(ha_events_total, stage="domain_filter", outcome="filtered")
        assert count == 1.0

    def test_events_total_all_stages(self, connector: HAConnector) -> None:
        """connector_ha_events_total covers domain_filter, significance_filter, discretion."""
        assert connector._ha_metrics is not None
        for stage in ("domain_filter", "significance_filter", "discretion"):
            connector._ha_metrics.inc_events(stage=stage, outcome="passed")

        for stage in ("domain_filter", "significance_filter", "discretion"):
            count = _get_counter(ha_events_total, stage=stage, outcome="passed")
            assert count == 1.0, f"Stage {stage!r} count should be 1.0"

    def test_discretion_total_forward_verdict(self, connector: HAConnector) -> None:
        """connector_ha_discretion_total increments for forward verdicts."""
        connector.on_discretion_result(available=True, verdict="forward")

        count = _get_counter(
            ha_discretion_total,
            endpoint_identity="home_assistant:ha.local:8123",
            verdict="forward",
        )
        assert count == 1.0

    def test_discretion_total_ignore_verdict(self, connector: HAConnector) -> None:
        """connector_ha_discretion_total increments for ignore verdicts."""
        connector.on_discretion_result(available=True, verdict="ignore")

        count = _get_counter(
            ha_discretion_total,
            endpoint_identity="home_assistant:ha.local:8123",
            verdict="ignore",
        )
        assert count == 1.0

    def test_discretion_total_error_forward_verdict(self, connector: HAConnector) -> None:
        """connector_ha_discretion_total increments for error_forward verdicts."""
        connector.on_discretion_result(available=False, verdict="error_forward")

        count = _get_counter(
            ha_discretion_total,
            endpoint_identity="home_assistant:ha.local:8123",
            verdict="error_forward",
        )
        assert count == 1.0


# ---------------------------------------------------------------------------
# Task 8.4 — HA-specific gauges
# ---------------------------------------------------------------------------


class TestHAGauges:
    """HA-specific Prometheus gauges are set correctly."""

    def test_transport_mode_gauge_websocket_on_connect(self, connector: HAConnector) -> None:
        """connector_ha_transport_mode is 1 when WebSocket is connected."""
        connector.on_ws_connected()

        value = _get_gauge(
            ha_transport_mode,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert value == 1.0

    def test_transport_mode_gauge_rest_on_disconnect(self, connector: HAConnector) -> None:
        """connector_ha_transport_mode is 0 when WebSocket disconnects."""
        connector.on_ws_connected()
        connector.on_ws_disconnected()

        value = _get_gauge(
            ha_transport_mode,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert value == 0.0

    def test_transport_mode_gauge_initialized_to_websocket(self, config: HAConnectorConfig) -> None:
        """Transport mode gauge is initialized to 1 (websocket) when identity is set."""
        c = HAConnector(config=config)
        c._set_endpoint_identity("http://ha.local:8123")

        value = _get_gauge(
            ha_transport_mode,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert value == 1.0

    def test_filter_pass_rate_zero_initially(self, connector: HAConnector) -> None:
        """Filter pass rate is 0 before any events are processed."""
        # Gauge starts at 0 for unset labels — but we also check it's not artificially high
        connector.on_event_received(passed_all_filters=False)
        value = _get_gauge(
            ha_filter_pass_rate,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert value == 0.0

    def test_filter_pass_rate_all_pass(self, connector: HAConnector) -> None:
        """Filter pass rate is 1.0 when all events pass all filters."""
        for _ in range(5):
            connector.on_event_received(passed_all_filters=True)

        value = _get_gauge(
            ha_filter_pass_rate,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert value == pytest.approx(1.0)

    def test_filter_pass_rate_partial(self, connector: HAConnector) -> None:
        """Filter pass rate reflects the actual fraction of passed events."""
        # 2 of 4 events pass
        connector.on_event_received(passed_all_filters=True)
        connector.on_event_received(passed_all_filters=True)
        connector.on_event_received(passed_all_filters=False)
        connector.on_event_received(passed_all_filters=False)

        value = _get_gauge(
            ha_filter_pass_rate,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert value == pytest.approx(0.5)

    def test_filter_pass_rate_accumulates_correctly(self, connector: HAConnector) -> None:
        """Filter pass rate is updated incrementally as events are processed."""
        # First 3 events: 1 passes → rate = 1/3
        connector.on_event_received(passed_all_filters=True)
        connector.on_event_received(passed_all_filters=False)
        connector.on_event_received(passed_all_filters=False)
        assert _get_gauge(
            ha_filter_pass_rate, endpoint_identity="home_assistant:ha.local:8123"
        ) == pytest.approx(1 / 3)

        # Next 3 events: all pass → rate = 4/6 = 2/3
        for _ in range(3):
            connector.on_event_received(passed_all_filters=True)
        assert _get_gauge(
            ha_filter_pass_rate, endpoint_identity="home_assistant:ha.local:8123"
        ) == pytest.approx(4 / 6)

    def test_entities_tracked_gauge(self, connector: HAConnector) -> None:
        """connector_ha_entities_tracked gauge reflects the count of seen entities."""
        connector.on_entities_tracked_update(42)

        value = _get_gauge(
            ha_entities_tracked,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert value == 42.0

    def test_entities_tracked_gauge_updates(self, connector: HAConnector) -> None:
        """Entities-tracked gauge is updated on successive calls."""
        connector.on_entities_tracked_update(10)
        connector.on_entities_tracked_update(25)

        value = _get_gauge(
            ha_entities_tracked,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert value == 25.0


# ---------------------------------------------------------------------------
# Task 8.5 — HA-specific histograms
# ---------------------------------------------------------------------------


class TestHAHistograms:
    """HA-specific Prometheus histograms record observations correctly."""

    def test_event_latency_histogram_records_observation(self, connector: HAConnector) -> None:
        """connector_ha_event_latency_seconds records event latency."""
        assert connector._ha_metrics is not None
        connector._ha_metrics.observe_event_latency(0.5)

        count = _get_histogram_count(
            ha_event_latency_seconds,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        total = _get_histogram_sum(
            ha_event_latency_seconds,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert count == 1.0
        assert total == pytest.approx(0.5)

    def test_event_latency_histogram_accumulates(self, connector: HAConnector) -> None:
        """Event latency histogram accumulates across multiple observations."""
        assert connector._ha_metrics is not None
        for latency in (0.1, 0.2, 0.3):
            connector._ha_metrics.observe_event_latency(latency)

        count = _get_histogram_count(
            ha_event_latency_seconds,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        total = _get_histogram_sum(
            ha_event_latency_seconds,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert count == 3.0
        assert total == pytest.approx(0.6)

    def test_filter_pipeline_histogram_records_observation(self, connector: HAConnector) -> None:
        """connector_ha_filter_pipeline_seconds records filter pipeline duration."""
        assert connector._ha_metrics is not None
        connector._ha_metrics.observe_filter_pipeline(0.025)

        count = _get_histogram_count(
            ha_filter_pipeline_seconds,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        total = _get_histogram_sum(
            ha_filter_pipeline_seconds,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert count == 1.0
        assert total == pytest.approx(0.025)

    def test_filter_pipeline_histogram_accumulates(self, connector: HAConnector) -> None:
        """Filter pipeline histogram accumulates across multiple observations."""
        assert connector._ha_metrics is not None
        durations = [0.01, 0.05, 0.1, 0.02]
        for d in durations:
            connector._ha_metrics.observe_filter_pipeline(d)

        count = _get_histogram_count(
            ha_filter_pipeline_seconds,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert count == 4.0

    def test_event_latency_histogram_large_value(self, connector: HAConnector) -> None:
        """Event latency histogram handles latency > 10s (beyond normal buckets)."""
        assert connector._ha_metrics is not None
        connector._ha_metrics.observe_event_latency(25.0)

        count = _get_histogram_count(
            ha_event_latency_seconds,
            endpoint_identity="home_assistant:ha.local:8123",
        )
        assert count == 1.0


# ---------------------------------------------------------------------------
# HAConnectorMetrics helper
# ---------------------------------------------------------------------------


class TestHAConnectorMetrics:
    """HAConnectorMetrics wraps module-level metrics with fixed endpoint_identity."""

    @pytest.fixture
    def ha_metrics(self) -> HAConnectorMetrics:
        return HAConnectorMetrics(endpoint_identity="home_assistant:myha:8123")

    def test_inc_events_domain_filter(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.inc_events(stage="domain_filter", outcome="passed")
        assert _get_counter(ha_events_total, stage="domain_filter", outcome="passed") == 1.0

    def test_inc_ws_reconnect(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.inc_ws_reconnect()
        assert (
            _get_counter(ha_ws_reconnects_total, endpoint_identity="home_assistant:myha:8123")
            == 1.0
        )

    def test_inc_rest_poll_success(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.inc_rest_poll(status="success")
        assert (
            _get_counter(
                ha_rest_polls_total,
                endpoint_identity="home_assistant:myha:8123",
                status="success",
            )
            == 1.0
        )

    def test_set_filter_pass_rate(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.set_filter_pass_rate(0.75)
        assert _get_gauge(
            ha_filter_pass_rate, endpoint_identity="home_assistant:myha:8123"
        ) == pytest.approx(0.75)

    def test_set_transport_mode_websocket(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.set_transport_mode(websocket=True)
        assert _get_gauge(ha_transport_mode, endpoint_identity="home_assistant:myha:8123") == 1.0

    def test_set_transport_mode_rest(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.set_transport_mode(websocket=False)
        assert _get_gauge(ha_transport_mode, endpoint_identity="home_assistant:myha:8123") == 0.0

    def test_set_entities_tracked(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.set_entities_tracked(17)
        assert _get_gauge(ha_entities_tracked, endpoint_identity="home_assistant:myha:8123") == 17.0

    def test_observe_event_latency(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.observe_event_latency(1.23)
        count = _get_histogram_count(
            ha_event_latency_seconds, endpoint_identity="home_assistant:myha:8123"
        )
        assert count == 1.0

    def test_observe_filter_pipeline(self, ha_metrics: HAConnectorMetrics) -> None:
        ha_metrics.observe_filter_pipeline(0.042)
        count = _get_histogram_count(
            ha_filter_pipeline_seconds, endpoint_identity="home_assistant:myha:8123"
        )
        assert count == 1.0


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


class TestHAConnectorConfig:
    """HAConnectorConfig.from_env() loads values from environment variables."""

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() uses defaults for optional variables."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.delenv("CONNECTOR_HEALTH_PORT", raising=False)
        monkeypatch.delenv("HA_POLL_INTERVAL_S", raising=False)
        monkeypatch.delenv("HA_DOMAIN_ALLOWLIST", raising=False)

        config = HAConnectorConfig.from_env()

        assert config.switchboard_mcp_url == "http://localhost:41100/sse"
        assert config.health_port == 40087
        assert config.poll_interval_s == 60
        assert "light" in config.domain_allowlist
        assert "sensor" in config.domain_allowlist

    def test_from_env_custom_health_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() respects CONNECTOR_HEALTH_PORT override."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40099")

        config = HAConnectorConfig.from_env()
        assert config.health_port == 40099

    def test_from_env_custom_domain_allowlist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() parses HA_DOMAIN_ALLOWLIST correctly."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("HA_DOMAIN_ALLOWLIST", "light,sensor,lock")

        config = HAConnectorConfig.from_env()
        assert config.domain_allowlist == frozenset({"light", "sensor", "lock"})

    def test_from_env_requires_switchboard_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() raises ValueError when SWITCHBOARD_MCP_URL is missing."""
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)

        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL is required"):
            HAConnectorConfig.from_env()

    def test_from_env_custom_poll_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env() respects HA_POLL_INTERVAL_S override."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
        monkeypatch.setenv("HA_POLL_INTERVAL_S", "120")

        config = HAConnectorConfig.from_env()
        assert config.poll_interval_s == 120


# ---------------------------------------------------------------------------
# Endpoint identity derivation
# ---------------------------------------------------------------------------


class TestEndpointIdentityDerivation:
    """_derive_endpoint_identity constructs the correct identity string."""

    def test_http_url_default_port(self) -> None:
        """http:// URL without explicit port uses 8123."""
        eid = HAConnector._derive_endpoint_identity("http://homeassistant.local:8123")
        assert eid == "home_assistant:homeassistant.local:8123"

    def test_http_url_custom_port(self) -> None:
        """http:// URL with custom port uses the given port."""
        eid = HAConnector._derive_endpoint_identity("http://ha.home:9090")
        assert eid == "home_assistant:ha.home:9090"

    def test_https_url(self) -> None:
        """https:// URL derives correct identity."""
        eid = HAConnector._derive_endpoint_identity("https://ha.example.com:443")
        assert eid == "home_assistant:ha.example.com:443"

    def test_ip_address_url(self) -> None:
        """IP address URL is handled correctly."""
        eid = HAConnector._derive_endpoint_identity("http://192.168.1.100:8123")
        assert eid == "home_assistant:192.168.1.100:8123"


# ---------------------------------------------------------------------------
# Health server
# ---------------------------------------------------------------------------


class TestHealthServer:
    """Health server initializes correctly and binds to localhost."""

    def test_health_server_uses_make_health_socket(self, connector: HAConnector) -> None:
        """start_health_server calls make_health_socket with the configured port."""
        mock_socket = MagicMock()
        mock_server_instance = MagicMock()
        mock_thread_instance = MagicMock()

        with (
            patch("butlers.connectors.home_assistant.make_health_socket", return_value=mock_socket),
            patch("butlers.connectors.home_assistant.Thread", return_value=mock_thread_instance),
        ):
            import uvicorn

            with patch.object(uvicorn, "Server", return_value=mock_server_instance):
                with patch.object(uvicorn, "Config"):
                    connector.start_health_server()

        # Verify side effects: connector has a health server reference
        assert connector._health_server is not None or connector._health_thread is not None

    def test_health_server_binds_to_localhost(self, connector: HAConnector) -> None:
        """start_health_server uses 127.0.0.1 as the bind address."""
        captured_calls: list[tuple] = []

        def _mock_make_socket(host: str, port: int) -> MagicMock:
            captured_calls.append((host, port))
            return MagicMock()

        mock_thread_instance = MagicMock()
        mock_server_instance = MagicMock()

        with (
            patch(
                "butlers.connectors.home_assistant.make_health_socket",
                side_effect=_mock_make_socket,
            ),
            patch("butlers.connectors.home_assistant.Thread", return_value=mock_thread_instance),
        ):
            import uvicorn

            with patch.object(uvicorn, "Server", return_value=mock_server_instance):
                with patch.object(uvicorn, "Config"):
                    connector.start_health_server()

        assert len(captured_calls) == 1
        assert captured_calls[0][0] == "127.0.0.1"
        assert captured_calls[0][1] == 40087
