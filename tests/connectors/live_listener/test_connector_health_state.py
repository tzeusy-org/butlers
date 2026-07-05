"""LiveListenerConnector.get_health_state() discretion auth-health wiring (bu-ur7go).

From bu-ofo3i's diagnosis: connector /status previously had no way to
surface a missing/invalid discretion-runtime auth file (e.g.
``~/.codex/auth.json`` never provisioned in a standalone connector
container) — every discretion call silently 401'd while /status reported
healthy. This exercises ``get_health_state()`` in isolation via a bare
instance (``object.__new__``) since ``LiveListenerConnector.__init__``
pulls in full env-parsed device config unrelated to this check.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from butlers.connectors.live_listener.connector import LiveListenerConnector, MicPipelineState

pytestmark = pytest.mark.unit


def _bare_connector(discretion_dispatcher: object | None) -> LiveListenerConnector:
    connector = object.__new__(LiveListenerConnector)
    mic_state = MicPipelineState("kitchen")
    mic_state.connected = True
    mic_state.transcription_healthy = True
    mic_state.discretion_healthy = True
    connector._mic_states = {"kitchen": mic_state}
    connector._discretion_dispatcher = discretion_dispatcher
    return connector


def test_get_health_state_degrades_on_discretion_auth_failure() -> None:
    """A degraded discretion auth-health snapshot surfaces as an overall
    "degraded" state even though every mic pipeline itself is healthy."""
    dispatcher = MagicMock()
    dispatcher.get_auth_health.return_value = {
        "runtime_type": "codex",
        "auth_file_present": False,
        "last_discretion_success_at": None,
        "last_auth_failure_at": "2026-07-06T00:00:00+00:00",
        "status": "degraded",
    }
    connector = _bare_connector(dispatcher)

    state, error_msg = connector.get_health_state()

    assert state == "degraded"
    assert "discretion auth degraded" in error_msg
    assert "codex" in error_msg


def test_get_health_state_healthy_when_discretion_auth_ok() -> None:
    dispatcher = MagicMock()
    dispatcher.get_auth_health.return_value = {
        "runtime_type": "codex",
        "auth_file_present": True,
        "last_discretion_success_at": "2026-07-06T00:00:00+00:00",
        "last_auth_failure_at": None,
        "status": "ok",
    }
    connector = _bare_connector(dispatcher)

    state, error_msg = connector.get_health_state()

    assert state == "healthy"
    assert "mic:kitchen=healthy" in error_msg


def test_get_health_state_ok_without_discretion_dispatcher() -> None:
    """No DB pool -> no dispatcher (fail-open); must not raise or degrade."""
    connector = _bare_connector(None)

    state, error_msg = connector.get_health_state()

    assert state == "healthy"
    assert "mic:kitchen=healthy" in error_msg
