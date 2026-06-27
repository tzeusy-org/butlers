"""OAuth scope registry for connector-oauth-scope-surface.

Defines per-connector required scope declarations and the applicability matrix
classifying every connector type as OAuth-bound or non-OAuth (unsupported).

Spec: openspec/changes/add-connector-oauth-scope-surface/specs/connector-oauth-scope-surface/spec.md

Design decisions:
- Required scopes are sourced from the existing _compose_provider_default_scopes /
  collect_toml_scopes path from oauth.py (bu-a2l98). This module provides the
  connector-type → provider mapping that feeds into that path plus per-scope
  metadata (serif_note, category).
- Granted scopes: captured from OAuth token refresh responses and stored in
  connector_registry.observed_scopes (TEXT[]). When NULL (never probed or
  non-OAuth connector), the auth_status is unconfigured / unsupported.
- Missing = required − granted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Scope declaration types
# ---------------------------------------------------------------------------

ScopeCategory = Literal["required", "optional", "sensitive", "extra"]
ScopeStatus = Literal["ok", "missing", "extra"]
AuthStatus = Literal["ok", "degraded", "expired", "rotation-needed", "unsupported", "unconfigured"]
AltSurfaceKind = Literal["session-validity", "static-token", "device-pairing"]
DriftClass = Literal["ok", "extra", "drift", "expired", "unsupported"]


@dataclass(frozen=True)
class ScopeDecl:
    """A single declared scope with user-facing metadata."""

    name: str
    """Provider scope string, e.g. 'user-read-recently-played'."""

    serif_note: str
    """Single sentence explaining the scope's purpose (no trailing period)."""

    category: ScopeCategory = "required"
    """Scope category: required | optional | sensitive."""

    approval_reason: str | None = None
    """For sensitive scopes: user-facing reason for the elevated grant."""


@dataclass(frozen=True)
class ScopeManifest:
    """Structured scope manifest for one OAuth connector type."""

    version: int
    """Forward-only counter; increment when required set changes."""

    required: list[ScopeDecl] = field(default_factory=list)
    optional: list[ScopeDecl] = field(default_factory=list)
    sensitive: list[ScopeDecl] = field(default_factory=list)

    def all_decls(self) -> list[ScopeDecl]:
        """All declared scopes in manifest order: required → optional → sensitive."""
        return [*self.required, *self.optional, *self.sensitive]

    def required_names(self) -> frozenset[str]:
        return frozenset(d.name for d in self.required)

    def optional_names(self) -> frozenset[str]:
        return frozenset(d.name for d in self.optional)

    def sensitive_names(self) -> frozenset[str]:
        return frozenset(d.name for d in self.sensitive)

    def all_declared_names(self) -> frozenset[str]:
        return self.required_names() | self.optional_names() | self.sensitive_names()

    def by_name(self) -> dict[str, ScopeDecl]:
        return {d.name: d for d in self.all_decls()}


# ---------------------------------------------------------------------------
# Per-connector scope manifests
# ---------------------------------------------------------------------------
#
# These are the required scope sets per connector type. The scope strings MUST
# match those returned by _compose_provider_default_scopes in oauth.py.
#
# When butler.toml [oauth.<provider>] scopes exist, they take precedence at
# OAuth start time (bu-a2l98). The manifests here define the *minimum required*
# set that this backend surface checks against observed_scopes.

_SPOTIFY_MANIFEST = ScopeManifest(
    version=1,
    required=[
        ScopeDecl(
            name="user-read-email",
            serif_note="Used to identify the Spotify account",
        ),
        ScopeDecl(
            name="user-read-private",
            serif_note="Used to read account subscription level",
        ),
        ScopeDecl(
            name="user-read-recently-played",
            serif_note="Used to poll listening history every 10 minutes",
        ),
        ScopeDecl(
            name="user-top-read",
            serif_note="Powers the weekly listening-summary skill",
        ),
        ScopeDecl(
            name="user-read-playback-state",
            serif_note="Used to detect what is currently playing",
        ),
    ],
    optional=[
        ScopeDecl(
            name="user-modify-playback-state",
            serif_note="Allows controlling playback on your behalf",
            category="optional",
        ),
        ScopeDecl(
            name="user-library-read",
            serif_note="Allows reading your saved tracks and albums",
            category="optional",
        ),
    ],
    sensitive=[
        ScopeDecl(
            name="playlist-modify-public",
            serif_note="Grants write access to your public Spotify playlists",
            category="sensitive",
            approval_reason=(
                "Required only if you ask the butler to create or edit public playlists"
            ),
        ),
        ScopeDecl(
            name="playlist-modify-private",
            serif_note="Grants write access to your private Spotify playlists",
            category="sensitive",
            approval_reason=(
                "Required only if you ask the butler to create or edit private playlists"
            ),
        ),
    ],
)

_GMAIL_MANIFEST = ScopeManifest(
    version=1,
    required=[
        ScopeDecl(
            name="openid",
            serif_note="Used to verify your Google identity",
        ),
        ScopeDecl(
            name="email",
            serif_note="Used to read your Google account email address",
        ),
        ScopeDecl(
            name="profile",
            serif_note="Used to read your Google account display name",
        ),
        ScopeDecl(
            name="https://www.googleapis.com/auth/gmail.readonly",
            serif_note="Used to read your Gmail messages for ingestion",
        ),
    ],
    optional=[
        ScopeDecl(
            name="https://www.googleapis.com/auth/contacts",
            serif_note="Used to sync your Google Contacts into the butler contact registry",
            category="optional",
        ),
        ScopeDecl(
            name="https://www.googleapis.com/auth/contacts.readonly",
            serif_note="Used for read-only contact lookups",
            category="optional",
        ),
    ],
    sensitive=[
        ScopeDecl(
            name="https://www.googleapis.com/auth/gmail.modify",
            serif_note="Grants the butler ability to modify your Gmail messages",
            category="sensitive",
            approval_reason=(
                "Required only if you ask the butler to archive, label, or reply to email"
            ),
        ),
    ],
)

_GOOGLE_CALENDAR_MANIFEST = ScopeManifest(
    version=1,
    required=[
        ScopeDecl(
            name="openid",
            serif_note="Used to verify your Google identity",
        ),
        ScopeDecl(
            name="email",
            serif_note="Used to read your Google account email address",
        ),
        ScopeDecl(
            name="profile",
            serif_note="Used to read your Google account display name",
        ),
        ScopeDecl(
            name="https://www.googleapis.com/auth/calendar",
            serif_note="Used to read and write your Google Calendar events",
        ),
    ],
)

_GOOGLE_DRIVE_MANIFEST = ScopeManifest(
    # version 2: required scope corrected from drive.readonly to full drive.
    # The google_drive module writes files and hard-fails startup without the
    # full drive scope (modules/google_drive: _DRIVE_SCOPE check), so the single
    # OAuth grant backing this connector must cover full read/write drive. The
    # connector ingestion path alone only reads, but the grant is shared.
    version=2,
    required=[
        ScopeDecl(
            name="openid",
            serif_note="Used to verify your Google identity",
        ),
        ScopeDecl(
            name="email",
            serif_note="Used to read your Google account email address",
        ),
        ScopeDecl(
            name="profile",
            serif_note="Used to read your Google account display name",
        ),
        ScopeDecl(
            name="https://www.googleapis.com/auth/drive",
            serif_note="Used to read your Drive files for ingestion and to manage files",
        ),
    ],
)

_GOOGLE_HEALTH_MANIFEST = ScopeManifest(
    version=1,
    required=[
        ScopeDecl(
            name="openid",
            serif_note="Used to verify your Google identity",
        ),
        ScopeDecl(
            name="email",
            serif_note="Used to read your Google account email address",
        ),
        ScopeDecl(
            name="profile",
            serif_note="Used to read your Google account display name",
        ),
        ScopeDecl(
            name="https://www.googleapis.com/auth/googlehealth.sleep",
            serif_note="Used to read your sleep data from Google Health",
        ),
        ScopeDecl(
            name="https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
            serif_note="Used to read your activity and fitness data from Google Health",
        ),
        ScopeDecl(
            name="https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
            serif_note="Used to read your health metrics from Google Health",
        ),
    ],
)

_DISCORD_MANIFEST = ScopeManifest(
    version=1,
    required=[
        ScopeDecl(
            name="identify",
            serif_note="Used to read your Discord user ID and username",
        ),
        ScopeDecl(
            name="guilds",
            serif_note="Used to list the servers you belong to",
        ),
        ScopeDecl(
            name="messages.read",
            serif_note="Used to read messages from channels you have access to",
        ),
    ],
)

# ---------------------------------------------------------------------------
# Scope manifest registry — maps connector_type → ScopeManifest
# ---------------------------------------------------------------------------

_OAUTH_MANIFESTS: dict[str, ScopeManifest] = {
    "spotify": _SPOTIFY_MANIFEST,
    "gmail": _GMAIL_MANIFEST,
    "google_calendar": _GOOGLE_CALENDAR_MANIFEST,
    "google_drive": _GOOGLE_DRIVE_MANIFEST,
    "google_health": _GOOGLE_HEALTH_MANIFEST,
    "discord": _DISCORD_MANIFEST,
    "discord_user": _DISCORD_MANIFEST,  # same OAuth surface, different connector name
}


def get_scope_manifest(connector_type: str) -> ScopeManifest | None:
    """Return the ScopeManifest for *connector_type*, or None for non-OAuth connectors.

    Unrecognized connector_type values return None, which the API treats as
    auth_status = 'unsupported'.
    """
    return _OAUTH_MANIFESTS.get(connector_type)


# ---------------------------------------------------------------------------
# Per-connector applicability matrix
# ---------------------------------------------------------------------------
#
# Normative matrix per spec §Per-connector applicability matrix.
# Every connector_type must have an entry; unrecognized types default to
# unsupported at runtime (but the matrix completeness test catches gaps).


@dataclass(frozen=True)
class ConnectorAuthEntry:
    """Single row in the per-connector applicability matrix."""

    connector_type: str
    credential_model: str
    """e.g. 'oauth2_pkce', 'google_oauth', 'bot_token', 'tdlib_session', etc."""

    oauth_supported: bool
    """True when reauth via the OAuth flow is supported."""

    alt_surface_kind: AltSurfaceKind | None = None
    """For non-OAuth connectors: kind of alternative credential surface."""

    alt_surface_remediation_path: str | None = None
    """Dashboard route to the alternative credential surface."""

    note: str | None = None
    """Provider-specific copy for the auth.note field in non-OAuth responses."""


APPLICABILITY_MATRIX: dict[str, ConnectorAuthEntry] = {
    "spotify": ConnectorAuthEntry(
        connector_type="spotify",
        credential_model="oauth2_pkce",
        oauth_supported=True,
    ),
    "gmail": ConnectorAuthEntry(
        connector_type="gmail",
        credential_model="google_oauth",
        oauth_supported=True,
    ),
    "google_calendar": ConnectorAuthEntry(
        connector_type="google_calendar",
        credential_model="google_oauth",
        oauth_supported=True,
    ),
    "google_drive": ConnectorAuthEntry(
        connector_type="google_drive",
        credential_model="google_oauth",
        oauth_supported=True,
    ),
    "google_health": ConnectorAuthEntry(
        connector_type="google_health",
        credential_model="google_oauth",
        oauth_supported=True,
    ),
    "discord": ConnectorAuthEntry(
        connector_type="discord",
        credential_model="oauth2",
        oauth_supported=True,
    ),
    "discord_user": ConnectorAuthEntry(
        connector_type="discord_user",
        credential_model="oauth2",
        oauth_supported=True,
    ),
    "telegram_bot": ConnectorAuthEntry(
        connector_type="telegram_bot",
        credential_model="bot_token",
        oauth_supported=False,
        alt_surface_kind="static-token",
        alt_surface_remediation_path="/settings/secrets#telegram-bot",
        note="Telegram bot token — rotate via Settings",
    ),
    "telegram_user_client": ConnectorAuthEntry(
        connector_type="telegram_user_client",
        credential_model="tdlib_session",
        oauth_supported=False,
        alt_surface_kind="session-validity",
        alt_surface_remediation_path="/settings/connectors/telegram-user-client",
        note="Telegram user client TDLib session — re-pair via mobile flow",
    ),
    "owntracks": ConnectorAuthEntry(
        connector_type="owntracks",
        credential_model="bearer_token",
        oauth_supported=False,
        alt_surface_kind="static-token",
        alt_surface_remediation_path="/settings/secrets#owntracks",
        note="OwnTracks bearer token — regenerate via Settings",
    ),
    "home_assistant": ConnectorAuthEntry(
        connector_type="home_assistant",
        credential_model="long_lived_access_token",
        oauth_supported=False,
        alt_surface_kind="static-token",
        alt_surface_remediation_path="/settings/secrets#home-assistant",
        note="Home Assistant long-lived access token — rotate via HA UI",
    ),
    "whatsapp": ConnectorAuthEntry(
        connector_type="whatsapp",
        credential_model="meta_business_app",
        oauth_supported=False,
        alt_surface_kind="session-validity",
        alt_surface_remediation_path="/settings/connectors/whatsapp",
        note="WhatsApp Meta business app credentials — rotate via Meta Business",
    ),
    "steam": ConnectorAuthEntry(
        connector_type="steam",
        credential_model="api_key",
        oauth_supported=False,
        alt_surface_kind="static-token",
        alt_surface_remediation_path="/settings/secrets#steam",
        note="Steam API key — regenerate via Steam Web API",
    ),
    "live-listener": ConnectorAuthEntry(
        connector_type="live-listener",
        credential_model="internal",
        oauth_supported=False,
        alt_surface_kind="static-token",
        alt_surface_remediation_path="/settings/connectors",
        note="Internal connector — no remote auth required",
    ),
    "filtered_events": ConnectorAuthEntry(
        connector_type="filtered_events",
        credential_model="internal",
        oauth_supported=False,
        alt_surface_kind="static-token",
        alt_surface_remediation_path="/settings/connectors",
        note="Internal connector — no remote auth required",
    ),
}

_UNSUPPORTED_FALLBACK = ConnectorAuthEntry(
    connector_type="unknown",
    credential_model="unknown",
    oauth_supported=False,
    alt_surface_kind="static-token",
    alt_surface_remediation_path="/settings/connectors",
    note="Connector type not recognized — no OAuth surface available",
)


def get_applicability(connector_type: str) -> ConnectorAuthEntry:
    """Return the applicability matrix entry for *connector_type*.

    Falls back to an 'unsupported' entry for unrecognized types (fail-safe
    per spec §Per-connector applicability matrix).
    """
    return APPLICABILITY_MATRIX.get(connector_type, _UNSUPPORTED_FALLBACK)


# ---------------------------------------------------------------------------
# Drift classification and auth status computation
# ---------------------------------------------------------------------------


def classify_drift(
    manifest: ScopeManifest,
    observed_scopes: list[str] | None,
    *,
    token_rejected: bool = False,
) -> DriftClass:
    """Compute the connector-level drift class.

    Args:
        manifest: The connector's scope manifest.
        observed_scopes: Scopes last seen from the provider. None = never probed.
        token_rejected: True when the most recent introspection returned 401/invalid_grant.

    Returns:
        One of: 'ok', 'extra', 'drift', 'expired', 'unsupported'.
    """
    if token_rejected:
        return "expired"

    if observed_scopes is None:
        # Never probed — treat as unconfigured (not drift), handled by auth_status
        return "ok"

    observed = frozenset(observed_scopes)
    required = manifest.required_names()

    if not required.issubset(observed):
        return "drift"

    # Extra: observed contains scopes outside the full declared set
    if observed - manifest.all_declared_names():
        return "extra"

    return "ok"


def compute_auth_status(
    connector_type: str,
    manifest: ScopeManifest | None,
    observed_scopes: list[str] | None,
    required_scopes_version: int | None,
    *,
    token_rejected: bool = False,
) -> AuthStatus:
    """Compute the auth_status rollup for a connector.

    Spec: §Auth status computation.

    Args:
        connector_type: Used to check applicability matrix.
        manifest: The connector's scope manifest. None for non-OAuth connectors.
        observed_scopes: Last-known granted scopes. None = never probed.
        required_scopes_version: Manifest version at last reauth. None = never reauthed.
        token_rejected: True when last introspection was an auth failure.

    Returns:
        One of the six AuthStatus enum values.
    """
    applicability = get_applicability(connector_type)
    if not applicability.oauth_supported or manifest is None:
        return "unsupported"

    if token_rejected:
        return "expired"

    if observed_scopes is None:
        return "unconfigured"

    # Check manifest version drift (rotation-needed when row is behind manifest)
    version_drift = required_scopes_version is None or required_scopes_version < manifest.version

    drift = classify_drift(manifest, observed_scopes)

    if drift == "drift" or version_drift:
        return "rotation-needed"

    # Check optional scope coverage for degraded
    observed = frozenset(observed_scopes)
    optional_missing = manifest.optional_names() - observed
    if optional_missing:
        return "degraded"

    return "ok"


# ---------------------------------------------------------------------------
# Scope surface computation — builds the API response blocks
# ---------------------------------------------------------------------------


@dataclass
class ScopeRow:
    """One row in the scopes[] block of the connector-detail response."""

    name: str
    category: ScopeCategory
    status: ScopeStatus
    sensitive_granted: bool
    granted_at: str | None
    required_since: str | None
    serif_note: str


def build_scope_rows(
    manifest: ScopeManifest,
    observed_scopes: list[str] | None,
) -> list[ScopeRow]:
    """Compute the scopes[] block per spec §Dashboard API response shape.

    Ordering: required → optional → sensitive → extra.
    Extra scopes (in observed but not in any manifest category) are appended last.

    Args:
        manifest: The connector's scope manifest.
        observed_scopes: Last-known granted scopes. None = never probed.

    Returns:
        List of ScopeRow entries in canonical order.
    """
    if observed_scopes is None:
        # No observation — return required scopes as missing
        return [
            ScopeRow(
                name=d.name,
                category=d.category,
                status="missing",
                sensitive_granted=False,
                granted_at=None,
                required_since=None,
                serif_note=d.serif_note,
            )
            for d in manifest.all_decls()
        ]

    observed = frozenset(observed_scopes)
    sensitive_names = manifest.sensitive_names()
    rows: list[ScopeRow] = []

    # Declared scopes in manifest order
    for decl in manifest.all_decls():
        granted = decl.name in observed
        rows.append(
            ScopeRow(
                name=decl.name,
                category=decl.category,
                status="ok" if granted else "missing",
                sensitive_granted=granted and decl.name in sensitive_names,
                granted_at=None,  # v1: not backfilled from audit log
                required_since=None,  # v1: not tracked per-scope
                serif_note=decl.serif_note,
            )
        )

    # Extra scopes — observed but not in any manifest category
    declared_names = manifest.all_declared_names()
    for scope_name in observed_scopes:
        if scope_name not in declared_names:
            rows.append(
                ScopeRow(
                    name=scope_name,
                    category="extra",
                    status="extra",
                    sensitive_granted=False,
                    granted_at=None,
                    required_since=None,
                    serif_note=(
                        "Granted beyond the declared requirement; harmless but visible for audit"
                    ),
                )
            )

    return rows
