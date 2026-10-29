"""Static connector identities and their explicit surface projections.

This module contains data only. A label alias is never a registration, an
accepted ingress channel, or permission to start an authentication flow.
Runtime configuration, credential values and deployment state live elsewhere.

Consumers project identity, policy, OAuth metadata and public recovery data.
Runtime identity also derives from strict canonical lookup. Operational config,
credential resolution and health/deployment mechanisms remain with their consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DeploymentTarget = Literal["local", "compose", "helm"]
SourcePair = tuple[str, str]


HistoryStrategy = Literal["realtime", "email", "none"]
RecoveryKind = Literal["google-oauth", "spotify-passport", "whatsapp-passport", "unsupported"]


@dataclass(frozen=True)
class ScopeDeclDescriptor:
    name: str
    serif_note: str
    category: Literal["required", "optional", "sensitive"] = "required"
    approval_reason: str | None = None


@dataclass(frozen=True)
class ScopeManifestDescriptor:
    version: int
    required: tuple[ScopeDeclDescriptor, ...] = ()
    optional: tuple[ScopeDeclDescriptor, ...] = ()
    sensitive: tuple[ScopeDeclDescriptor, ...] = ()


@dataclass(frozen=True)
class AuthDescriptor:
    """Classification only: never an observed credential, grant or expiry."""

    credential_model: str
    oauth_supported: bool
    alt_surface_kind: Literal["session-validity", "static-token", "device-pairing"] | None = None
    alt_surface_remediation_path: str | None = None
    note: str | None = None


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
    auth: AuthDescriptor = AuthDescriptor("unknown", False)
    auth_aliases: tuple[str, ...] = ()
    scope_manifest: ScopeManifestDescriptor | None = None
    policy_selectors: tuple[str, ...] = ()
    policy_required_keys: tuple[str, ...] = ("priority_action",)
    history: tuple[tuple[str, HistoryStrategy], ...] = ()
    recovery: RecoveryKind = "unsupported"
    recovery_aliases: tuple[str, ...] = ()
    health_scope_opt_in: bool = False

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
        auth=AuthDescriptor(credential_model="google_oauth", oauth_supported=True),
        scope_manifest=ScopeManifestDescriptor(
            version=1,
            required=(
                ScopeDeclDescriptor(
                    name="openid", serif_note="Used to verify your Google identity"
                ),
                ScopeDeclDescriptor(
                    name="email", serif_note="Used to read your Google account email address"
                ),
                ScopeDeclDescriptor(
                    name="profile", serif_note="Used to read your Google account display name"
                ),
                ScopeDeclDescriptor(
                    name="https://www.googleapis.com/auth/gmail.readonly",
                    serif_note="Used to read your Gmail messages for ingestion",
                ),
            ),
            optional=(
                ScopeDeclDescriptor(
                    name="https://www.googleapis.com/auth/contacts",
                    serif_note="Used to sync your Google Contacts into the butler contact registry",
                    category="optional",
                ),
                ScopeDeclDescriptor(
                    name="https://www.googleapis.com/auth/contacts.readonly",
                    serif_note="Used for read-only contact lookups",
                    category="optional",
                ),
            ),
            sensitive=(
                ScopeDeclDescriptor(
                    name="https://www.googleapis.com/auth/gmail.modify",
                    serif_note="Grants the butler ability to modify your Gmail messages",
                    category="sensitive",
                    approval_reason=(
                        "Required only if you ask the butler to archive, label, or reply to email"
                    ),
                ),
            ),
        ),
        policy_selectors=("email",),
        policy_required_keys=("priority_action", "max_age_days"),
        history=(("email", "email"),),
        recovery="google-oauth",
        recovery_aliases=("google",),
    ),
    ConnectorDescriptor(
        "telegram_bot",
        "butlers.connectors.telegram_bot",
        "telegram_bot",
        "telegram",
        "Telegram Bot",
        ("local", "compose", "helm"),
        scrub_aliases=("telegram",),
        auth=AuthDescriptor(
            credential_model="bot_token",
            oauth_supported=False,
            alt_surface_kind="static-token",
            alt_surface_remediation_path="/settings/secrets#telegram-bot",
            note="Telegram bot token — rotate via Settings",
        ),
        policy_selectors=("telegram", "telegram_bot"),
        history=(("telegram_bot", "realtime"),),
    ),
    ConnectorDescriptor(
        "telegram_user_client",
        "butlers.connectors.telegram_user_client",
        "telegram_user_client",
        "telegram",
        "Telegram User Client",
        ("local", "compose", "helm"),
        scrub_aliases=("telegram_user",),
        auth=AuthDescriptor(
            credential_model="tdlib_session",
            oauth_supported=False,
            alt_surface_kind="session-validity",
            alt_surface_remediation_path="/settings/connectors/telegram-user-client",
            note="Telegram user client TDLib session — re-pair via mobile flow",
        ),
        policy_selectors=("telegram_user_client",),
        history=(("telegram_user_client", "realtime"),),
    ),
    ConnectorDescriptor(
        "home_assistant",
        "butlers.connectors.home_assistant",
        "home_assistant",
        "home_assistant",
        "Home Assistant",
        ("local", "compose", "helm"),
        secondary_pairs=(("wellness", "home_assistant"),),
        auth=AuthDescriptor(
            credential_model="long_lived_access_token",
            oauth_supported=False,
            alt_surface_kind="static-token",
            alt_surface_remediation_path="/settings/secrets#home-assistant",
            note="Home Assistant long-lived access token — rotate via HA UI",
        ),
        policy_selectors=("home-assistant", "home_assistant"),
    ),
    ConnectorDescriptor(
        "spotify",
        "butlers.connectors.spotify",
        "spotify_user_client",
        "spotify",
        "Spotify",
        ("local", "compose", "helm"),
        auth=AuthDescriptor(credential_model="oauth2_pkce", oauth_supported=True),
        scope_manifest=ScopeManifestDescriptor(
            version=1,
            required=(
                ScopeDeclDescriptor(
                    name="user-read-email", serif_note="Used to identify the Spotify account"
                ),
                ScopeDeclDescriptor(
                    name="user-read-private", serif_note="Used to read account subscription level"
                ),
                ScopeDeclDescriptor(
                    name="user-read-recently-played",
                    serif_note="Used to poll listening history every 10 minutes",
                ),
                ScopeDeclDescriptor(
                    name="user-top-read", serif_note="Powers the weekly listening-summary skill"
                ),
                ScopeDeclDescriptor(
                    name="user-read-playback-state",
                    serif_note="Used to detect what is currently playing",
                ),
            ),
            optional=(
                ScopeDeclDescriptor(
                    name="user-modify-playback-state",
                    serif_note="Allows controlling playback on your behalf",
                    category="optional",
                ),
                ScopeDeclDescriptor(
                    name="user-library-read",
                    serif_note="Allows reading your saved tracks and albums",
                    category="optional",
                ),
            ),
            sensitive=(
                ScopeDeclDescriptor(
                    name="playlist-modify-public",
                    serif_note="Grants write access to your public Spotify playlists",
                    category="sensitive",
                    approval_reason=(
                        "Required only if you ask the butler to create or edit public playlists"
                    ),
                ),
                ScopeDeclDescriptor(
                    name="playlist-modify-private",
                    serif_note="Grants write access to your private Spotify playlists",
                    category="sensitive",
                    approval_reason=(
                        "Required only if you ask the butler to create or edit private playlists"
                    ),
                ),
            ),
        ),
        policy_selectors=("spotify",),
        history=(("spotify_user_client", "realtime"),),
        recovery="spotify-passport",
    ),
    ConnectorDescriptor(
        "owntracks",
        "butlers.connectors.owntracks",
        "owntracks",
        "owntracks",
        "OwnTracks",
        ("local", "compose", "helm"),
        auth=AuthDescriptor(
            credential_model="bearer_token",
            oauth_supported=False,
            alt_surface_kind="static-token",
            alt_surface_remediation_path="/settings/secrets#owntracks",
            note="OwnTracks bearer token — regenerate via Settings",
        ),
        policy_selectors=("owntracks",),
        history=(("owntracks", "realtime"),),
    ),
    ConnectorDescriptor(
        "whatsapp_user_client",
        "butlers.connectors.whatsapp_user_client",
        "whatsapp_user_client",
        "whatsapp",
        "WhatsApp User Client",
        ("local", "compose", "helm"),
        auth=AuthDescriptor(
            credential_model="whatsmeow_session",
            oauth_supported=False,
            alt_surface_kind="session-validity",
            alt_surface_remediation_path="/secrets?focus=u:whatsapp",
            note=(
                "WhatsApp managed session; QR pairing and session custody belong to"
                " the WhatsApp Passport and connector bridge"
            ),
        ),
        policy_selectors=("whatsapp",),
        history=(("whatsapp", "realtime"), ("whatsapp_user_client", "realtime")),
        auth_aliases=("whatsapp",),
        recovery_aliases=("whatsapp",),
        recovery="whatsapp-passport",
    ),
    ConnectorDescriptor(
        "steam",
        "butlers.connectors.steam",
        "gaming",
        "steam",
        "Steam",
        ("local", "compose", "helm"),
        auth=AuthDescriptor(
            credential_model="api_key",
            oauth_supported=False,
            alt_surface_kind="static-token",
            alt_surface_remediation_path="/settings/secrets#steam",
            note="Steam API key — regenerate via Steam Web API",
        ),
        policy_selectors=("steam",),
    ),
    ConnectorDescriptor(
        "google_calendar",
        "butlers.connectors.google_calendar",
        "google_calendar",
        "google_calendar",
        "Google Calendar",
        ("local", "compose", "helm"),
        auth=AuthDescriptor(credential_model="google_oauth", oauth_supported=True),
        scope_manifest=ScopeManifestDescriptor(
            version=1,
            required=(
                ScopeDeclDescriptor(
                    name="openid", serif_note="Used to verify your Google identity"
                ),
                ScopeDeclDescriptor(
                    name="email", serif_note="Used to read your Google account email address"
                ),
                ScopeDeclDescriptor(
                    name="profile", serif_note="Used to read your Google account display name"
                ),
                ScopeDeclDescriptor(
                    name="https://www.googleapis.com/auth/calendar",
                    serif_note="Used to read and write your Google Calendar events",
                ),
            ),
        ),
        policy_selectors=("google_calendar",),
        history=(("google_calendar", "realtime"),),
        recovery="google-oauth",
    ),
    ConnectorDescriptor(
        "google_drive",
        "butlers.connectors.google_drive",
        "google_drive",
        "google_drive",
        "Google Drive",
        ("local", "compose", "helm"),
        auth=AuthDescriptor(credential_model="google_oauth", oauth_supported=True),
        scope_manifest=ScopeManifestDescriptor(
            version=2,
            required=(
                ScopeDeclDescriptor(
                    name="openid", serif_note="Used to verify your Google identity"
                ),
                ScopeDeclDescriptor(
                    name="email", serif_note="Used to read your Google account email address"
                ),
                ScopeDeclDescriptor(
                    name="profile", serif_note="Used to read your Google account display name"
                ),
                ScopeDeclDescriptor(
                    name="https://www.googleapis.com/auth/drive",
                    serif_note="Used to read your Drive files for ingestion and to manage files",
                ),
            ),
        ),
        policy_selectors=("google_drive",),
        recovery="google-oauth",
    ),
    ConnectorDescriptor(
        "google_health",
        "butlers.connectors.google_health",
        "wellness",
        "google_health",
        "Google Health",
        ("local", "compose", "helm"),
        auth=AuthDescriptor(credential_model="google_oauth", oauth_supported=True),
        scope_manifest=ScopeManifestDescriptor(
            version=1,
            required=(
                ScopeDeclDescriptor(
                    name="openid", serif_note="Used to verify your Google identity"
                ),
                ScopeDeclDescriptor(
                    name="email", serif_note="Used to read your Google account email address"
                ),
                ScopeDeclDescriptor(
                    name="profile", serif_note="Used to read your Google account display name"
                ),
                ScopeDeclDescriptor(
                    name="https://www.googleapis.com/auth/googlehealth.sleep.readonly",
                    serif_note="Used to read your sleep data from Google Health",
                ),
                ScopeDeclDescriptor(
                    name=(
                        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
                    ),
                    serif_note="Used to read your activity and fitness data from Google Health",
                ),
                ScopeDeclDescriptor(
                    name=(
                        ""
                        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
                    ),
                    serif_note="Used to read your health metrics from Google Health",
                ),
            ),
        ),
        policy_selectors=("google_health",),
        recovery="google-oauth",
        health_scope_opt_in=True,
    ),
    ConnectorDescriptor(
        "activitywatch",
        "butlers.connectors.activitywatch",
        "activitywatch",
        "activitywatch",
        "ActivityWatch",
        ("local", "compose", "helm"),
        auth=AuthDescriptor(
            credential_model="environment_config",
            oauth_supported=False,
            note=(
                "Local ActivityWatch environment configuration; no remote"
                " OAuth or dashboard credential enrollment flow"
            ),
            alt_surface_remediation_path="/ingestion/connectors",
        ),
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
        auth=AuthDescriptor(
            credential_model="internal",
            oauth_supported=False,
            alt_surface_kind="static-token",
            alt_surface_remediation_path="/ingestion/connectors",
            note="Internal connector; no remote authorization required",
        ),
        auth_aliases=("live-listener",),
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


def connector_descriptor(connector_type: str) -> ConnectorDescriptor:
    """Return one active canonical transport; aliases never acquire a row."""
    for row in CONNECTOR_DESCRIPTORS:
        if row.connector_type == connector_type and not row.retired:
            return row
    raise KeyError(connector_type)


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


# Consent scope groups are separate from minimum-versioned manifest checks.
# In particular, Health is opt-in and Spotify's connector-owned PKCE authority
# remains unchanged; this catalog does not silently reconcile their populations.
GOOGLE_SCOPE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("base", ("openid", "email", "profile")),
    ("calendar", ("https://www.googleapis.com/auth/calendar",)),
    (
        "drive",
        ("https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive"),
    ),
    (
        "gmail",
        (
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
        ),
    ),
    (
        "contacts",
        (
            "https://www.googleapis.com/auth/contacts",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/contacts.other.readonly",
            "https://www.googleapis.com/auth/directory.readonly",
        ),
    ),
    (
        "health",
        (
            "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
            "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
            "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
        ),
    ),
)
GOOGLE_DEFAULT_SCOPE_GROUPS = ("base", "gmail", "calendar", "contacts", "drive")

# Stored-data readers and nonconnector ingress are not active registrations.
STORED_ONLY_HISTORY: tuple[tuple[str, HistoryStrategy], ...] = (
    ("slack", "realtime"),
    ("discord", "realtime"),
    ("api", "none"),
    ("mcp", "none"),
)
FILTERED_EVENTS_AUTH = AuthDescriptor(
    credential_model="internal",
    oauth_supported=False,
    alt_surface_kind="static-token",
    alt_surface_remediation_path="/ingestion/connectors",
    note="Internal connector — no remote auth required",
)
UNSUPPORTED_AUTH = AuthDescriptor(
    "unknown",
    False,
    "static-token",
    "/ingestion/connectors",
    "Connector type not recognized — no OAuth surface available",
)


def ingress_providers_by_channel() -> dict[str, frozenset[str]]:
    return {
        channel: frozenset(p for c, p in ingress_pairs() if c == channel)
        for channel, _ in sorted(ingress_pairs())
    }


def default_policy_required_keys() -> dict[str, set[str]]:
    return {
        selector: set(row.policy_required_keys)
        for row in active_connectors()
        for selector in row.policy_selectors
    }


def conversation_history_strategies() -> dict[str, HistoryStrategy]:
    return dict(
        (*STORED_ONLY_HISTORY, *(item for row in active_connectors() for item in row.history))
    )


def oauth_scope_manifests() -> dict[str, ScopeManifestDescriptor]:
    return {
        row.connector_type: row.scope_manifest
        for row in active_connectors()
        if row.scope_manifest is not None
    }


def auth_applicability() -> dict[str, AuthDescriptor]:
    return {
        **{
            selector: row.auth
            for row in active_connectors()
            for selector in (row.connector_type, *row.auth_aliases)
        },
        "filtered_events": FILTERED_EVENTS_AUTH,
    }


@dataclass(frozen=True)
class SelectorResolution:
    """Explicit namespaces preserve shared and unsupported alternatives."""

    namespace: Literal["connector", "provider", "channel"]
    selector: str
    kind: Literal["single", "alias", "group", "unsupported", "retired", "unknown"]
    members: tuple[str, ...] = ()
    pairs: tuple[SourcePair, ...] = ()
    unsupported_pairs: tuple[SourcePair, ...] = ()


def resolve_selector(
    namespace: Literal["connector", "provider", "channel"],
    selector: str,
) -> SelectorResolution:
    rows = active_connectors()
    if namespace == "connector":
        for row in rows:
            if selector == row.connector_type or selector in row.auth_aliases:
                kind = "single" if selector == row.connector_type else "alias"
                return SelectorResolution(namespace, selector, kind, (row.connector_type,))
        kind = "retired" if selector in {"discord", "discord_user"} else "unknown"
        return SelectorResolution(namespace, selector, kind)
    if namespace not in {"channel", "provider"}:
        raise ValueError("Unknown selector namespace")
    index = 0 if namespace == "channel" else 1
    pairs = tuple(sorted(pair for pair in ingress_pairs() if pair[index] == selector))
    members = tuple(
        row.connector_type
        for row in rows
        if any(pair in ((row.channel, row.provider), *row.secondary_pairs) for pair in pairs)
    )
    unmatched = tuple(pair for pair in pairs if pair not in connector_ingress_pairs())
    kind = (
        "unknown"
        if not pairs
        else "unsupported"
        if not members
        else "group"
        if len(members) > 1 or unmatched
        else "single"
    )
    return SelectorResolution(namespace, selector, kind, members, pairs, unmatched)


def public_recovery_capabilities() -> dict[str, tuple[RecoveryKind, bool]]:
    """Only closed public routing capabilities, with no credentials or URLs."""
    return {
        selector: (row.recovery, row.health_scope_opt_in)
        for row in active_connectors()
        if row.recovery != "unsupported"
        for selector in (row.connector_type, *row.recovery_aliases)
    }
