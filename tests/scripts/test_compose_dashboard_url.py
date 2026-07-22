"""Regression guard for public dashboard URLs in daemon-originated alerts.

The daemon constructs owner-facing dashboard links (model breaker, fleet halt,
approval, and secret-lifecycle attention).  It runs in a different Compose
service from the dashboard API, so the public path-prefixed URL must be passed
into both baked and hotreload daemon services explicitly.  Otherwise the
runtime falls back to ``localhost`` and links sent through Telegram are unusable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_DAEMON_SERVICES = ("butlers-up", "butlers-up-hotreload")
_PUBLIC_DASHBOARD_URL = "${DASHBOARD_URL:-${OAUTH_DASHBOARD_URL:-http://localhost:41200}}"


@pytest.fixture(scope="module")
def compose_services() -> dict:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]


@pytest.mark.parametrize("service_name", _DAEMON_SERVICES)
def test_daemon_receives_configured_public_dashboard_url(
    compose_services: dict, service_name: str
) -> None:
    environment = compose_services[service_name]["environment"]
    assert environment["DASHBOARD_URL"] == _PUBLIC_DASHBOARD_URL
