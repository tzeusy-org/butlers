"""Regression guard for the tailnet-only dashboard health monitor runbook."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_RUNBOOK = Path("docs/operations/tailnet-health-monitoring.md")
_OPERATIONS_INDEX = Path("docs/operations/index.md")
_COMPOSE_SCRIPT = Path("scripts/compose.sh")
_COMPOSE_FILE = Path("docker-compose.yml")


def _runbook_text() -> str:
    assert _RUNBOOK.is_file(), "Tailnet health-monitor runbook is missing."
    return _RUNBOOK.read_text(encoding="utf-8")


def _compose_script_text() -> str:
    return _COMPOSE_SCRIPT.read_text(encoding="utf-8")


def _compose_services() -> dict:
    compose = yaml.safe_load(_COMPOSE_FILE.read_text(encoding="utf-8"))
    return compose["services"]


def test_tailnet_health_monitoring_runbook_exists() -> None:
    """Operators need a checked-in, reviewable monitoring contract."""
    assert _RUNBOOK.is_file(), (
        "The canonical tailnet health-monitor runbook is missing; document the "
        "production /butlers-api/api/health contract before asking an operator "
        "or Uptime Kuma agent to configure it."
    )


def test_operations_index_links_to_tailnet_health_monitoring_runbook() -> None:
    """The operator-facing contract must be discoverable from Operations."""
    index = _OPERATIONS_INDEX.read_text(encoding="utf-8")
    assert "[Tailnet Health Monitoring](tailnet-health-monitoring.md)" in index


def test_production_serve_mapping_exposes_the_canonical_health_route() -> None:
    """The production prefix must proxy the app's existing /api/health route."""
    text = _compose_script_text()

    assert 'API_PREFIX="butlers-api"' in text
    assert '"/${API_PREFIX}|http://localhost:${DASHBOARD_HOST_PORT}"' in text
    assert 'TAILSCALE_HTTPS_PORT="${TAILSCALE_HTTPS_PORT:-443}"' in text
    assert 'tailscale serve --yes --bg --https="${TAILSCALE_HTTPS_PORT}"' in text


def test_dashboard_api_keeps_the_serve_target_loopback_bound() -> None:
    """Tailnet TLS terminates at Serve; Compose must not publish the API to the LAN."""
    ports = _compose_services()["dashboard-api"]["ports"]
    assert "127.0.0.1:${DASHBOARD_HOST_PORT:-41200}:41200" in ports


def test_runbook_pins_tailnet_strict_tls_pull_monitor_contract() -> None:
    """The handoff must not weaken the proven route into a generic liveness check."""
    text = _runbook_text()

    required_fragments = (
        "https://<TAILNET_DNS_NAME>/butlers-api/api/health",
        "tailnet-only",
        "strict TLS",
        "HTTP(s) pull monitor",
        "HTTP 200",
        '"status": "ok"',
        '"status": "starting"',
        "60 seconds",
        "10 seconds",
        "two retries",
        "owner-supplied notification route",
        "bu-ln1v7",
    )
    for fragment in required_fragments:
        assert fragment in text, f"Runbook must retain {fragment!r}."

    forbidden_fragments = (
        "EXTERNAL_DEADMAN_URL",
        "Push monitor",
        "disable TLS verification",
    )
    lowered_text = text.casefold()
    for fragment in forbidden_fragments:
        assert fragment.casefold() not in lowered_text, (
            f"Runbook must not direct operators toward {fragment!r}."
        )
