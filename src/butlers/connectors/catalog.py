"""Static connector identities and their explicit surface projections.

This module contains data only. A label alias is never a registration, an
accepted ingress channel, or permission to start an authentication flow.
Runtime configuration, credential values and deployment state live elsewhere.

S1 consumers are discovery and buffered-preview labels. Other consumers still
retain their existing mechanisms until their separately verified slices land.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DeploymentTarget = Literal["local", "compose", "helm"]
SourcePair = tuple[str, str]


@dataclass(frozen=True)
class ConnectorDescriptor:
    """One supported transport, independent of its deployed instances."""

    connector_type: str
    module: str
    channel: str
    provider: str
    display_name: str
    deployment_targets: tuple[DeploymentTarget, ...]
    scrub_aliases: tuple[str, ...] = ()
    secondary_pairs: tuple[SourcePair, ...] = ()
    stored_event_type: str | None = None
    retired: bool = False

    def discovery_profile(self) -> dict[str, str]:
        """The existing four-field public discovery contract, without secrets."""
        return {
            "connector_type": self.connector_type,
            "channel": self.channel,
            "provider": self.provider,
            "display_name": self.display_name,
        }


CONNECTOR_DESCRIPTORS: tuple[ConnectorDescriptor, ...] = (
    ConnectorDescriptor(
        "gmail",
        "butlers.connectors.gmail",
        "email",
        "gmail",
        "Gmail",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "telegram_bot",
        "butlers.connectors.telegram_bot",
        "telegram_bot",
        "telegram",
        "Telegram Bot",
        ("local", "compose", "helm"),
        scrub_aliases=("telegram",),
    ),
    ConnectorDescriptor(
        "telegram_user_client",
        "butlers.connectors.telegram_user_client",
        "telegram_user_client",
        "telegram",
        "Telegram User Client",
        ("local", "compose", "helm"),
        scrub_aliases=("telegram_user",),
    ),
    ConnectorDescriptor(
        "home_assistant",
        "butlers.connectors.home_assistant",
        "home_assistant",
        "home_assistant",
        "Home Assistant",
        ("local", "compose", "helm"),
        secondary_pairs=(("wellness", "home_assistant"),),
    ),
    ConnectorDescriptor(
        "spotify",
        "butlers.connectors.spotify",
        "spotify_user_client",
        "spotify",
        "Spotify",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "owntracks",
        "butlers.connectors.owntracks",
        "owntracks",
        "owntracks",
        "OwnTracks",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "whatsapp_user_client",
        "butlers.connectors.whatsapp_user_client",
        "whatsapp_user_client",
        "whatsapp",
        "WhatsApp User Client",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "steam",
        "butlers.connectors.steam",
        "gaming",
        "steam",
        "Steam",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "google_calendar",
        "butlers.connectors.google_calendar",
        "google_calendar",
        "google_calendar",
        "Google Calendar",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "google_drive",
        "butlers.connectors.google_drive",
        "google_drive",
        "google_drive",
        "Google Drive",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "google_health",
        "butlers.connectors.google_health",
        "wellness",
        "google_health",
        "Google Health",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "activitywatch",
        "butlers.connectors.activitywatch",
        "activitywatch",
        "activitywatch",
        "ActivityWatch",
        ("local", "compose", "helm"),
    ),
    ConnectorDescriptor(
        "live_listener",
        "butlers.connectors.live_listener",
        "voice",
        "live-listener",
        "Live Listener",
        ("local", "compose"),
        scrub_aliases=("live-listener",),
        stored_event_type="live-listener",
    ),
)

# Accepted ingress need not have a connector process. Keep these existing wire
# pairs separate from connector identity, discovery and deployment projections.
NON_CONNECTOR_INGRESS_PAIRS: frozenset[SourcePair] = frozenset(
    {
        ("email", "imap"),
        ("slack", "slack"),
        ("api", "internal"),
        ("mcp", "internal"),
        ("dashboard", "internal"),
    }
)


def active_connectors() -> tuple[ConnectorDescriptor, ...]:
    return tuple(row for row in CONNECTOR_DESCRIPTORS if not row.retired)


def canonical_connector_types() -> frozenset[str]:
    return frozenset(row.connector_type for row in active_connectors())


def scrub_connector_labels() -> frozenset[str]:
    """Closed display labels, including verified historical spellings only."""
    return frozenset(
        label for row in active_connectors() for label in (row.connector_type, *row.scrub_aliases)
    )


def discovery_connectors() -> tuple[ConnectorDescriptor, ...]:
    """Framework-deployable transports, including device-local connectors."""
    return tuple(row for row in active_connectors() if row.deployment_targets)


def helm_connectors() -> tuple[ConnectorDescriptor, ...]:
    """Chart-supported transports; this is not a claim that a pod is enabled."""
    return tuple(row for row in active_connectors() if "helm" in row.deployment_targets)


def connector_ingress_pairs() -> frozenset[SourcePair]:
    return frozenset(
        pair
        for row in active_connectors()
        for pair in ((row.channel, row.provider), *row.secondary_pairs)
    )


def ingress_pairs() -> frozenset[SourcePair]:
    """The unchanged accepted relation, not a connector-registration list."""
    return connector_ingress_pairs() | NON_CONNECTOR_INGRESS_PAIRS
