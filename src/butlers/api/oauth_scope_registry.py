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

import re
from dataclasses import dataclass, field
from typing import Literal

from butlers.connectors.catalog import (
    UNSUPPORTED_AUTH,
    auth_applicability,
    oauth_scope_manifests,
)

# ---------------------------------------------------------------------------
# Scope declaration types
# ---------------------------------------------------------------------------

ScopeCategory = Literal["required", "optional", "sensitive", "extra"]
ScopeStatus = Literal["ok", "missing", "extra"]
AuthStatus = Literal["ok", "degraded", "expired", "rotation-needed", "unsupported", "unconfigured"]
AltSurfaceKind = Literal["session-validity", "static-token", "device-pairing"]
DriftClass = Literal["ok", "extra", "drift", "expired", "unsupported"]


# Provider scope identifiers are structured labels (for example a short Spotify
# permission or a Google URL).  A long opaque value made only of token-safe
# characters is not a usable scope label and must never be reflected from the
# registry onto the dashboard wire.  This leaves legitimate extra scopes
# visible while containing malformed credential-shaped observations.
_OPAQUE_SCOPE_VALUE = re.compile(
    r"^(?:[A-Za-z0-9._~+/=-]{40,}|Bearer\s+\S+)$",
    flags=re.IGNORECASE,
)


def _public_observed_scope_names(observed_scopes: list[str] | None) -> list[str] | None:
    """Return observed scope identifiers that are safe for dashboard projection."""
    if observed_scopes is None:
        return None
    return [
        scope_name
        for scope_name in observed_scopes
        if not _OPAQUE_SCOPE_VALUE.fullmatch(scope_name)
    ]


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


# Neutral immutable descriptors are adapted into the existing public API types.
_OAUTH_MANIFESTS: dict[str, ScopeManifest] = {
    key: ScopeManifest(
        version=manifest.version,
        required=[
            ScopeDecl(d.name, d.serif_note, d.category, d.approval_reason)
            for d in manifest.required
        ],
        optional=[
            ScopeDecl(d.name, d.serif_note, d.category, d.approval_reason)
            for d in manifest.optional
        ],
        sensitive=[
            ScopeDecl(d.name, d.serif_note, d.category, d.approval_reason)
            for d in manifest.sensitive
        ],
    )
    for key, manifest in oauth_scope_manifests().items()
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
    selector: ConnectorAuthEntry(
        connector_type=selector,
        credential_model=auth.credential_model,
        oauth_supported=auth.oauth_supported,
        alt_surface_kind=auth.alt_surface_kind,
        alt_surface_remediation_path=auth.alt_surface_remediation_path,
        note=auth.note,
    )
    for selector, auth in auth_applicability().items()
}

_UNSUPPORTED_FALLBACK = ConnectorAuthEntry(
    connector_type="unknown",
    credential_model=UNSUPPORTED_AUTH.credential_model,
    oauth_supported=UNSUPPORTED_AUTH.oauth_supported,
    alt_surface_kind=UNSUPPORTED_AUTH.alt_surface_kind,
    alt_surface_remediation_path=UNSUPPORTED_AUTH.alt_surface_remediation_path,
    note=UNSUPPORTED_AUTH.note,
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

    public_observed_scopes = _public_observed_scope_names(observed_scopes)
    if public_observed_scopes is None:
        # Never probed — treat as unconfigured (not drift), handled by auth_status
        return "ok"

    observed = frozenset(public_observed_scopes)
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

    public_observed_scopes = _public_observed_scope_names(observed_scopes)
    if public_observed_scopes is None:
        return "unconfigured"

    # Check manifest version drift (rotation-needed when row is behind manifest)
    version_drift = required_scopes_version is None or required_scopes_version < manifest.version

    drift = classify_drift(manifest, public_observed_scopes)

    if drift == "drift" or version_drift:
        return "rotation-needed"

    # Check optional scope coverage for degraded
    observed = frozenset(public_observed_scopes)
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
    Safe extra scopes (in observed but not in any manifest category) are
    appended last; opaque credential-shaped observations are withheld.

    Args:
        manifest: The connector's scope manifest.
        observed_scopes: Last-known granted scopes. None = never probed.

    Returns:
        List of ScopeRow entries in canonical order.
    """
    public_observed_scopes = _public_observed_scope_names(observed_scopes)
    if public_observed_scopes is None:
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

    observed = frozenset(public_observed_scopes)
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
    for scope_name in public_observed_scopes:
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
