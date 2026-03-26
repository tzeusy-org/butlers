"""Tests for the HA connector REST polling fallback (tasks 4.1–4.4).

Covers:
- ``HAStateCache``: diff-based change detection, apply, clear, len
- ``EntityStateSnapshot``: construction from HA state dict, state-change detection
- ``build_rest_state_changed_event``: synthetic event dict shape
- ``HARestPoller``: poll_once, poll_loop, start/stop idempotency
- ``HAFallbackController``: threshold-based activation, deactivation on success, reset
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from butlers.connectors.home_assistant_rest import (
    EntityStateSnapshot,
    HAFallbackController,
    HARestPoller,
    HAStateCache,
    build_rest_state_changed_event,
)

pytestmark = pytest.mark.unit

_PATCH_TARGET = "butlers.connectors.home_assistant_rest.aiohttp.ClientSession"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ha_state(
    entity_id: str,
    state: str = "on",
    attributes: dict[str, Any] | None = None,
    last_changed: str = "2024-01-01T12:00:00+00:00",
    last_updated: str = "2024-01-01T12:00:00+00:00",
) -> dict[str, Any]:
    """Build a minimal HA state dict as returned by GET /api/states."""
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attributes or {},
        "last_changed": last_changed,
        "last_updated": last_updated,
    }


def _snap(entity_id: str, state: str = "on") -> EntityStateSnapshot:
    """Build a minimal EntityStateSnapshot for test assertions."""
    return EntityStateSnapshot(entity_id=entity_id, state=state)


# ---------------------------------------------------------------------------
# aiohttp mock fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ha_session() -> Generator[tuple[MagicMock, AsyncMock], None, None]:
    """Patch aiohttp.ClientSession for HARestPoller tests.

    Yields:
        (mock_session, mock_resp) — callers may override ``mock_resp.json``
        return value per test.
    """
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=[])
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch(_PATCH_TARGET, return_value=mock_session):
        yield mock_session, mock_resp


# ---------------------------------------------------------------------------
# EntityStateSnapshot tests
# ---------------------------------------------------------------------------


class TestEntityStateSnapshot:
    def test_from_ha_state_maps_all_fields(self) -> None:
        """from_ha_state populates all fields from a HA state dict."""
        raw = _ha_state(
            "light.kitchen",
            state="on",
            attributes={"brightness": 128},
            last_changed="2024-01-01T08:00:00+00:00",
            last_updated="2024-01-01T08:05:00+00:00",
        )
        snap = EntityStateSnapshot.from_ha_state(raw)

        assert snap.entity_id == "light.kitchen"
        assert snap.state == "on"
        assert snap.attributes == {"brightness": 128}
        assert snap.last_changed == "2024-01-01T08:00:00+00:00"
        assert snap.last_updated == "2024-01-01T08:05:00+00:00"

    def test_from_ha_state_missing_fields_use_defaults(self) -> None:
        """from_ha_state handles missing optional fields gracefully."""
        snap = EntityStateSnapshot.from_ha_state({"entity_id": "sensor.x", "state": "off"})
        assert snap.entity_id == "sensor.x"
        assert snap.state == "off"
        assert snap.attributes == {}
        assert snap.last_changed == ""
        assert snap.last_updated == ""

    def test_has_state_changed_returns_true_on_different_state(self) -> None:
        old = _snap("light.x", "off")
        new = _snap("light.x", "on")
        assert old.has_state_changed(new) is True

    def test_has_state_changed_returns_false_on_same_state(self) -> None:
        old = _snap("light.x", "on")
        new = _snap("light.x", "on")
        assert old.has_state_changed(new) is False

    def test_has_state_changed_on_numeric_sensors(self) -> None:
        old = _snap("sensor.temp", "22.5")
        new_changed = _snap("sensor.temp", "23.0")
        new_same = _snap("sensor.temp", "22.5")
        assert old.has_state_changed(new_changed) is True
        assert old.has_state_changed(new_same) is False


# ---------------------------------------------------------------------------
# HAStateCache tests
# ---------------------------------------------------------------------------


class TestHAStateCache:
    def test_empty_cache_len_is_zero(self) -> None:
        cache = HAStateCache()
        assert len(cache) == 0

    def test_update_adds_entity(self) -> None:
        cache = HAStateCache()
        snap = _snap("light.kitchen")
        cache.update(snap)
        assert len(cache) == 1
        assert "light.kitchen" in cache

    def test_get_returns_none_for_missing_entity(self) -> None:
        cache = HAStateCache()
        assert cache.get("sensor.unknown") is None

    def test_get_returns_snapshot_for_known_entity(self) -> None:
        cache = HAStateCache()
        snap = _snap("light.bedroom", "on")
        cache.update(snap)
        result = cache.get("light.bedroom")
        assert result is not None
        assert result.state == "on"

    def test_diff_returns_empty_for_unchanged_states(self) -> None:
        cache = HAStateCache()
        cache.update(_snap("light.kitchen", "on"))
        new_states = [_snap("light.kitchen", "on")]
        diffs = cache.diff(new_states)
        assert diffs == []

    def test_diff_returns_change_for_different_state(self) -> None:
        cache = HAStateCache()
        cache.update(_snap("light.kitchen", "off"))
        new_states = [_snap("light.kitchen", "on")]
        diffs = cache.diff(new_states)
        assert len(diffs) == 1
        old, new = diffs[0]
        assert old is not None
        assert old.state == "off"
        assert new.state == "on"

    def test_diff_treats_new_entity_as_change(self) -> None:
        cache = HAStateCache()
        new_states = [_snap("light.new_entity", "on")]
        diffs = cache.diff(new_states)
        assert len(diffs) == 1
        old, new = diffs[0]
        assert old is None
        assert new.entity_id == "light.new_entity"

    def test_diff_does_not_modify_cache(self) -> None:
        cache = HAStateCache()
        cache.update(_snap("light.x", "off"))
        _ = cache.diff([_snap("light.x", "on")])
        # Cache should still reflect old state
        assert cache.get("light.x") is not None
        assert cache.get("light.x").state == "off"  # type: ignore[union-attr]

    def test_apply_replaces_all_entities(self) -> None:
        cache = HAStateCache()
        cache.update(_snap("light.old", "on"))
        new_states = [_snap("light.new", "off")]
        cache.apply(new_states)
        assert "light.old" not in cache
        assert "light.new" in cache
        assert len(cache) == 1

    def test_apply_empty_list_clears_cache(self) -> None:
        cache = HAStateCache()
        cache.update(_snap("light.x", "on"))
        cache.apply([])
        assert len(cache) == 0

    def test_clear_empties_cache(self) -> None:
        cache = HAStateCache()
        cache.update(_snap("light.x"))
        cache.update(_snap("light.y"))
        cache.clear()
        assert len(cache) == 0

    def test_diff_with_multiple_entities(self) -> None:
        """Diff correctly handles mixed changed and unchanged entities."""
        cache = HAStateCache()
        cache.update(_snap("light.a", "on"))
        cache.update(_snap("light.b", "off"))
        cache.update(_snap("light.c", "on"))

        new_states = [
            _snap("light.a", "on"),  # unchanged
            _snap("light.b", "on"),  # changed
            _snap("light.c", "off"),  # changed
        ]
        diffs = cache.diff(new_states)
        changed_entities = {new.entity_id for _, new in diffs}
        assert "light.a" not in changed_entities
        assert "light.b" in changed_entities
        assert "light.c" in changed_entities
        assert len(diffs) == 2


# ---------------------------------------------------------------------------
# build_rest_state_changed_event tests
# ---------------------------------------------------------------------------


class TestBuildRestStateChangedEvent:
    def test_event_type_is_state_changed(self) -> None:
        new = _snap("light.x", "on")
        event = build_rest_state_changed_event(None, new, "2024-01-01T00:00:00+00:00")
        assert event["event_type"] == "state_changed"

    def test_time_fired_is_set(self) -> None:
        new = _snap("light.x", "on")
        time_fired = "2024-06-15T10:30:00+00:00"
        event = build_rest_state_changed_event(None, new, time_fired)
        assert event["time_fired"] == time_fired

    def test_entity_id_in_data(self) -> None:
        new = _snap("sensor.temp", "22.5")
        event = build_rest_state_changed_event(None, new, "2024-01-01T00:00:00+00:00")
        assert event["data"]["entity_id"] == "sensor.temp"

    def test_old_state_is_none_when_no_old_snap(self) -> None:
        new = _snap("light.x", "on")
        event = build_rest_state_changed_event(None, new, "2024-01-01T00:00:00+00:00")
        assert event["data"]["old_state"] is None

    def test_old_state_populated_from_old_snap(self) -> None:
        old = EntityStateSnapshot(
            entity_id="light.x",
            state="off",
            attributes={"brightness": 0},
            last_changed="2024-01-01T11:00:00+00:00",
            last_updated="2024-01-01T11:00:00+00:00",
        )
        new = _snap("light.x", "on")
        event = build_rest_state_changed_event(old, new, "2024-01-01T12:00:00+00:00")
        old_state = event["data"]["old_state"]
        assert old_state is not None
        assert old_state["state"] == "off"
        assert old_state["entity_id"] == "light.x"

    def test_new_state_populated(self) -> None:
        new = EntityStateSnapshot(
            entity_id="sensor.temp",
            state="25.0",
            attributes={"unit_of_measurement": "°C"},
            last_changed="2024-01-01T12:00:00+00:00",
            last_updated="2024-01-01T12:00:00+00:00",
        )
        event = build_rest_state_changed_event(None, new, "2024-01-01T12:00:00+00:00")
        new_state = event["data"]["new_state"]
        assert new_state["state"] == "25.0"
        assert new_state["attributes"] == {"unit_of_measurement": "°C"}

    def test_new_state_last_changed_falls_back_to_time_fired(self) -> None:
        """When last_changed is empty, time_fired is used as fallback."""
        new = EntityStateSnapshot(entity_id="light.x", state="on", last_changed="")
        time_fired = "2024-01-01T12:00:00+00:00"
        event = build_rest_state_changed_event(None, new, time_fired)
        assert event["data"]["new_state"]["last_changed"] == time_fired

    def test_origin_is_local(self) -> None:
        new = _snap("light.x", "on")
        event = build_rest_state_changed_event(None, new, "2024-01-01T00:00:00+00:00")
        assert event["origin"] == "LOCAL"


# ---------------------------------------------------------------------------
# HARestPoller tests
# ---------------------------------------------------------------------------


class TestHARestPoller:
    def _make_poller(
        self,
        cache: HAStateCache | None = None,
        on_state_changed: Any | None = None,
        on_poll_success: Any | None = None,
        on_poll_error: Any | None = None,
        poll_interval_s: int = 60,
    ) -> HARestPoller:
        return HARestPoller(
            base_url="http://ha.local:8123",
            access_token="test-token",
            state_cache=cache if cache is not None else HAStateCache(),
            poll_interval_s=poll_interval_s,
            on_state_changed=on_state_changed,
            on_poll_success=on_poll_success,
            on_poll_error=on_poll_error,
        )

    def test_is_not_running_initially(self) -> None:
        poller = self._make_poller()
        assert poller.is_running is False

    async def test_start_creates_background_task(self) -> None:
        poller = self._make_poller(poll_interval_s=1000)
        try:
            poller.start()
            assert poller.is_running is True
        finally:
            poller.stop()

    async def test_start_is_idempotent(self) -> None:
        """Calling start() twice does not create a second task."""
        poller = self._make_poller(poll_interval_s=1000)
        try:
            poller.start()
            task1 = poller._task
            poller.start()  # should be a no-op
            assert poller._task is task1
        finally:
            poller.stop()

    async def test_stop_cancels_task(self) -> None:
        poller = self._make_poller(poll_interval_s=1000)
        poller.start()
        assert poller.is_running is True
        poller.stop()
        assert poller.is_running is False

    def test_stop_is_idempotent_when_not_running(self) -> None:
        """stop() does not raise when called on an idle poller."""
        poller = self._make_poller()
        poller.stop()  # should not raise

    async def test_poll_once_fetches_states_and_updates_cache(
        self, mock_ha_session: tuple[MagicMock, AsyncMock]
    ) -> None:
        """poll_once calls /api/states and updates the cache."""
        _, mock_resp = mock_ha_session
        cache = HAStateCache()
        poller = self._make_poller(cache=cache)

        mock_resp.json = AsyncMock(
            return_value=[
                _ha_state("light.kitchen", "on"),
                _ha_state("sensor.temp", "22.5"),
            ]
        )

        diffs = await poller.poll_once()

        # Both entities are new → 2 diffs (first-poll baseline)
        assert len(diffs) == 2
        assert len(cache) == 2
        assert cache.get("light.kitchen") is not None
        assert cache.get("sensor.temp") is not None

    async def test_poll_once_detects_state_changes(
        self, mock_ha_session: tuple[MagicMock, AsyncMock]
    ) -> None:
        """poll_once detects state changes after the first poll."""
        _, mock_resp = mock_ha_session
        cache = HAStateCache()
        cache.apply([_snap("light.kitchen", "off")])
        poller = self._make_poller(cache=cache)

        mock_resp.json = AsyncMock(return_value=[_ha_state("light.kitchen", "on")])

        diffs = await poller.poll_once()

        assert len(diffs) == 1
        old, new = diffs[0]
        assert old is not None and old.state == "off"
        assert new.state == "on"

    async def test_poll_once_invokes_on_state_changed_callback(
        self, mock_ha_session: tuple[MagicMock, AsyncMock]
    ) -> None:
        """poll_once invokes on_state_changed for each detected change."""
        _, mock_resp = mock_ha_session
        cache = HAStateCache()
        cache.apply([_snap("light.x", "off")])

        changes_received: list[tuple] = []

        async def _cb(old: Any, new: Any, event: Any) -> None:
            changes_received.append((old, new, event))

        poller = self._make_poller(cache=cache, on_state_changed=_cb)

        mock_resp.json = AsyncMock(return_value=[_ha_state("light.x", "on")])

        await poller.poll_once()

        assert len(changes_received) == 1
        _old, _new, event = changes_received[0]
        assert _old is not None
        assert _new.entity_id == "light.x"
        assert event["event_type"] == "state_changed"

    async def test_poll_once_invokes_on_poll_success(
        self, mock_ha_session: tuple[MagicMock, AsyncMock]
    ) -> None:
        """poll_once invokes on_poll_success after a successful poll."""
        success_calls = []

        def _on_success() -> None:
            success_calls.append(1)

        poller = self._make_poller(on_poll_success=_on_success)

        await poller.poll_once()

        assert len(success_calls) == 1

    async def test_poll_once_raises_on_http_error(
        self, mock_ha_session: tuple[MagicMock, AsyncMock]
    ) -> None:
        """poll_once propagates HTTP errors to the caller."""
        _, mock_resp = mock_ha_session
        mock_resp.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=401,
            )
        )
        poller = self._make_poller()

        with pytest.raises(aiohttp.ClientResponseError):
            await poller.poll_once()

    async def test_poll_loop_calls_on_poll_error_on_failure(self) -> None:
        """The poll loop invokes on_poll_error when poll_once raises."""
        errors_received: list[Exception] = []

        def _on_error(exc: Exception) -> None:
            errors_received.append(exc)

        poller = self._make_poller(poll_interval_s=0, on_poll_error=_on_error)

        call_count = 0

        async def _mock_poll_once() -> list:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise aiohttp.ClientConnectionError("connection refused")
            # Stop the loop after first error + one more pass
            poller._shutdown = True
            return []

        poller.poll_once = _mock_poll_once  # type: ignore[method-assign]

        await poller._poll_loop()

        assert len(errors_received) == 1
        assert isinstance(errors_received[0], aiohttp.ClientConnectionError)

    async def test_poll_loop_stops_on_shutdown(self) -> None:
        """The poll loop exits immediately when _shutdown is set."""
        poller = self._make_poller(poll_interval_s=0)

        poll_count = 0

        async def _mock_poll_once() -> list:
            nonlocal poll_count
            poll_count += 1
            poller._shutdown = True
            return []

        poller.poll_once = _mock_poll_once  # type: ignore[method-assign]
        poller._shutdown = False

        # Run one cycle then auto-stop
        await poller._poll_loop()
        assert poll_count == 1

    async def test_bearer_token_included_in_requests(self) -> None:
        """poll_once uses the configured access token as a Bearer header."""
        captured_headers: list[dict] = []

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value=[])
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()

        def _get(url: str, headers: dict, **kwargs: Any) -> Any:
            captured_headers.append(headers)
            return mock_resp

        mock_session.get = _get
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        poller = HARestPoller(
            base_url="http://ha.local:8123",
            access_token="secret-token-xyz",
            state_cache=HAStateCache(),
        )

        with patch(_PATCH_TARGET, return_value=mock_session):
            await poller.poll_once()

        assert len(captured_headers) == 1
        assert captured_headers[0]["Authorization"] == "Bearer secret-token-xyz"


# ---------------------------------------------------------------------------
# HAFallbackController tests (task 4.3)
# ---------------------------------------------------------------------------


class TestHAFallbackController:
    def test_initial_state(self) -> None:
        controller = HAFallbackController()
        assert controller.consecutive_failures == 0
        assert controller.fallback_active is False

    def test_single_failure_does_not_activate_fallback(self) -> None:
        controller = HAFallbackController(ws_failure_threshold=3)
        activated = controller.on_ws_failure()
        assert activated is False
        assert controller.fallback_active is False
        assert controller.consecutive_failures == 1

    def test_two_failures_do_not_activate_fallback(self) -> None:
        controller = HAFallbackController(ws_failure_threshold=3)
        controller.on_ws_failure()
        activated = controller.on_ws_failure()
        assert activated is False
        assert controller.fallback_active is False

    def test_third_failure_activates_fallback(self) -> None:
        """Exactly 3 consecutive failures trigger fallback activation."""
        fallback_started = []

        def _start() -> None:
            fallback_started.append(1)

        controller = HAFallbackController(
            ws_failure_threshold=3,
            on_fallback_start=_start,
        )
        controller.on_ws_failure()
        controller.on_ws_failure()
        activated = controller.on_ws_failure()

        assert activated is True
        assert controller.fallback_active is True
        assert len(fallback_started) == 1

    def test_fourth_failure_does_not_call_start_twice(self) -> None:
        """Once fallback is active, further failures do not invoke on_fallback_start again."""
        start_calls = []

        def _start() -> None:
            start_calls.append(1)

        controller = HAFallbackController(ws_failure_threshold=3, on_fallback_start=_start)
        for _ in range(4):
            controller.on_ws_failure()

        assert len(start_calls) == 1
        assert controller.consecutive_failures == 4

    def test_success_resets_counter(self) -> None:
        controller = HAFallbackController()
        controller.on_ws_failure()
        controller.on_ws_failure()
        controller.on_ws_success()
        assert controller.consecutive_failures == 0

    def test_success_deactivates_fallback(self) -> None:
        """on_ws_success stops active REST fallback and returns True."""
        fallback_stopped = []

        def _stop() -> None:
            fallback_stopped.append(1)

        controller = HAFallbackController(ws_failure_threshold=2, on_fallback_stop=_stop)
        controller.on_ws_failure()
        controller.on_ws_failure()
        assert controller.fallback_active is True

        deactivated = controller.on_ws_success()

        assert deactivated is True
        assert controller.fallback_active is False
        assert len(fallback_stopped) == 1

    def test_success_without_active_fallback_returns_false(self) -> None:
        """on_ws_success returns False when no fallback was active."""
        controller = HAFallbackController()
        deactivated = controller.on_ws_success()
        assert deactivated is False

    def test_success_does_not_call_stop_when_no_fallback(self) -> None:
        """on_ws_success does not call on_fallback_stop when fallback is not active."""
        stop_calls = []

        def _stop() -> None:
            stop_calls.append(1)

        controller = HAFallbackController(on_fallback_stop=_stop)
        controller.on_ws_success()

        assert len(stop_calls) == 0

    def test_reset_clears_state_without_callbacks(self) -> None:
        """reset() clears state without invoking start/stop callbacks."""
        start_calls = []
        stop_calls = []

        controller = HAFallbackController(
            ws_failure_threshold=3,
            on_fallback_start=lambda: start_calls.append(1),
            on_fallback_stop=lambda: stop_calls.append(1),
        )
        controller.on_ws_failure()
        controller.on_ws_failure()
        controller.on_ws_failure()
        assert controller.fallback_active is True

        controller.reset()

        assert controller.consecutive_failures == 0
        assert controller.fallback_active is False
        assert len(stop_calls) == 0  # no callback

    def test_custom_threshold(self) -> None:
        """HAFallbackController respects a custom ws_failure_threshold."""
        controller = HAFallbackController(ws_failure_threshold=1)
        activated = controller.on_ws_failure()
        assert activated is True
        assert controller.fallback_active is True

    def test_failure_threshold_5(self) -> None:
        """Verify exact threshold behaviour for threshold=5."""
        controller = HAFallbackController(ws_failure_threshold=5)
        for i in range(4):
            result = controller.on_ws_failure()
            assert result is False, f"Expected False on failure {i + 1}"
        result = controller.on_ws_failure()
        assert result is True

    def test_reconnect_after_fallback_resets_counter(self) -> None:
        """Counter is zeroed after reconnect; subsequent failures accumulate fresh."""
        controller = HAFallbackController(ws_failure_threshold=3)
        controller.on_ws_failure()
        controller.on_ws_failure()
        controller.on_ws_failure()  # activates fallback
        controller.on_ws_success()  # deactivates, resets counter

        # Two more failures should not re-activate
        controller.on_ws_failure()
        result = controller.on_ws_failure()
        assert result is False
        assert controller.fallback_active is False

    def test_fallback_reactivates_after_second_ws_outage(self) -> None:
        """After reconnect → second outage of 3 failures re-activates fallback."""
        start_calls = []
        controller = HAFallbackController(
            ws_failure_threshold=3,
            on_fallback_start=lambda: start_calls.append(1),
        )

        # First outage
        for _ in range(3):
            controller.on_ws_failure()
        controller.on_ws_success()  # reconnect

        # Second outage
        for _ in range(3):
            controller.on_ws_failure()

        assert controller.fallback_active is True
        assert len(start_calls) == 2  # activated twice


# ---------------------------------------------------------------------------
# HA_POLL_INTERVAL_S configuration test (task 4.4)
# ---------------------------------------------------------------------------


class TestPollIntervalConfig:
    def test_ha_poll_interval_s_used_in_poller(self) -> None:
        """HARestPoller respects the poll_interval_s parameter."""
        cache = HAStateCache()
        poller = HARestPoller(
            base_url="http://ha.local:8123",
            access_token="tok",
            state_cache=cache,
            poll_interval_s=120,
        )
        assert poller._poll_interval_s == 120

    def test_default_poll_interval_is_60(self) -> None:
        """Default poll interval is 60 seconds."""
        cache = HAStateCache()
        poller = HARestPoller(
            base_url="http://ha.local:8123",
            access_token="tok",
            state_cache=cache,
        )
        assert poller._poll_interval_s == 60

    def test_ha_connector_config_poll_interval_env(self) -> None:
        """HAConnectorConfig reads HA_POLL_INTERVAL_S from environment."""
        import os

        from butlers.connectors.home_assistant import HAConnectorConfig

        env = {"SWITCHBOARD_MCP_URL": "http://localhost/sse", "HA_POLL_INTERVAL_S": "120"}
        with patch.dict(os.environ, env, clear=False):
            config = HAConnectorConfig.from_env()
        assert config.poll_interval_s == 120

    def test_ha_connector_config_default_poll_interval(self) -> None:
        """HAConnectorConfig defaults poll_interval_s to 60 when env var is absent."""
        import os

        from butlers.connectors.home_assistant import HAConnectorConfig

        env = {"SWITCHBOARD_MCP_URL": "http://localhost/sse"}
        # Remove HA_POLL_INTERVAL_S if set
        env_clean = {k: v for k, v in os.environ.items() if k != "HA_POLL_INTERVAL_S"}
        env_clean.update(env)
        with patch.dict(os.environ, env_clean, clear=True):
            config = HAConnectorConfig.from_env()
        assert config.poll_interval_s == 60
