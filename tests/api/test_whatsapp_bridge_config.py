"""WhatsApp bridge socket configuration contract tests."""

from pathlib import Path

import pytest
import yaml

from butlers.api.routers.whatsapp import _get_bridge_socket_path
from butlers.connectors.bridge_manager import (
    DEFAULT_SHARED_BRIDGE_SOCKET,
    DEFAULT_STANDALONE_BRIDGE_SOCKET,
    BridgeConfig,
)
from butlers.connectors.whatsapp_user_client import WhatsAppUserClientConnectorConfig
from butlers.modules.whatsapp import WhatsAppConfig

pytestmark = pytest.mark.unit


_REPO_ROOT = Path(__file__).parents[2]


def test_compose_clients_share_connector_owned_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every compose client targets and mounts the connector-owned socket."""
    monkeypatch.delenv("WHATSAPP_BRIDGE_SOCKET", raising=False)

    assert DEFAULT_SHARED_BRIDGE_SOCKET == "/tmp/wa-bridge/bridge.sock"
    assert _get_bridge_socket_path() == DEFAULT_SHARED_BRIDGE_SOCKET
    assert WhatsAppConfig().bridge_socket == DEFAULT_SHARED_BRIDGE_SOCKET

    services = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]
    configured_clients = {
        "connector-whatsapp-user": "WA_BRIDGE_SOCKET",
        "dashboard-api": "WHATSAPP_BRIDGE_SOCKET",
        "dashboard-api-hotreload": "WHATSAPP_BRIDGE_SOCKET",
        "butlers-up": "WHATSAPP_BRIDGE_SOCKET",
    }

    for service_name, env_name in configured_clients.items():
        service = services[service_name]
        assert service["environment"][env_name] == DEFAULT_SHARED_BRIDGE_SOCKET
        assert "wa_bridge_socket:/tmp/wa-bridge" in service["volumes"]


def test_dashboard_socket_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-compose deployments can explicitly select their shared socket."""
    monkeypatch.setenv("WHATSAPP_BRIDGE_SOCKET", "/run/butlers/whatsapp.sock")

    assert _get_bridge_socket_path() == "/run/butlers/whatsapp.sock"
    assert WhatsAppConfig().bridge_socket == "/run/butlers/whatsapp.sock"


def test_standalone_bridge_owner_defaults_remain_self_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standalone owner uses a flat /tmp socket that needs no shared-volume directory."""
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/mcp")
    monkeypatch.delenv("WA_BRIDGE_SOCKET", raising=False)

    assert DEFAULT_STANDALONE_BRIDGE_SOCKET == "/tmp/wa-bridge.sock"
    assert BridgeConfig().bridge_socket == DEFAULT_STANDALONE_BRIDGE_SOCKET
    assert (
        WhatsAppUserClientConnectorConfig.from_env().bridge_socket
        == DEFAULT_STANDALONE_BRIDGE_SOCKET
    )


def test_compose_operator_commands_use_shared_socket() -> None:
    """Compose troubleshooting commands must address the socket mounted in the container."""
    setup_guide = (_REPO_ROOT / "docs/whatsapp-setup.md").read_text(encoding="utf-8")

    assert "--unix-socket /tmp/wa-bridge.sock" not in setup_guide
    assert "--unix-socket /tmp/wa-bridge/bridge.sock" in setup_guide


def test_setup_guide_uses_dashboard_invalidated_session_recovery() -> None:
    """The setup guide must not send operators back to manual session-store edits."""
    setup_guide = (_REPO_ROOT / "docs/whatsapp-setup.md").read_text(encoding="utf-8")

    assert "POST /api/connectors/whatsapp/pair/start" in setup_guide
    assert "Do **not** manually delete `public.whatsmeow_device`" in setup_guide
    assert "UPDATE messenger.whatsapp_sessions SET active = false" not in setup_guide
