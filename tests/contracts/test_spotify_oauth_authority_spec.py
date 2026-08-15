"""Specification consistency checks for Spotify OAuth authority (bu-27dxl.7.6.3).

These checks deliberately inspect the normative artifacts rather than the
currently transitional implementation.  The active OAuth scope-surface change
and the canonical dashboard/credential contracts must agree before its two
serialized implementation beads can safely remove the generic OAuth Spotify
exemplar and add the Passport projection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CARRIER_SPEC = (
    _REPO_ROOT
    / "openspec"
    / "changes"
    / "add-connector-oauth-scope-surface"
    / "specs"
    / "connector-oauth-scope-surface"
    / "spec.md"
)
_CARRIER_TASKS = (
    _REPO_ROOT / "openspec" / "changes" / "add-connector-oauth-scope-surface" / "tasks.md"
)
_CARRIER_DESIGN = (
    _REPO_ROOT / "openspec" / "changes" / "add-connector-oauth-scope-surface" / "design.md"
)
_CARRIER_PROPOSAL = (
    _REPO_ROOT / "openspec" / "changes" / "add-connector-oauth-scope-surface" / "proposal.md"
)
_DASHBOARD_INGESTION_LIFECYCLE_DELTA = (
    _REPO_ROOT
    / "openspec"
    / "changes"
    / "add-connector-oauth-scope-surface"
    / "specs"
    / "dashboard-ingestion-dispatch-console"
    / "spec.md"
)
_INGESTION_SPEC = (
    _REPO_ROOT / "openspec" / "specs" / "dashboard-ingestion-dispatch-console" / "spec.md"
)
_STALE_LIFECYCLE_DELTA = (
    _REPO_ROOT
    / "openspec"
    / "changes"
    / "add-connector-oauth-scope-surface"
    / "specs"
    / "connector-lifecycle-ceremony"
    / "spec.md"
)
_DASHBOARD_API_SPEC = _REPO_ROOT / "openspec" / "specs" / "dashboard-api" / "spec.md"
_PASSPORT_SPEC = _REPO_ROOT / "openspec" / "specs" / "butler-secrets" / "spec.md"
_SPOTIFY_SETUP_SPEC = _REPO_ROOT / "openspec" / "specs" / "dashboard-spotify-setup" / "spec.md"
_SPOTIFY_CONNECTOR_SPEC = _REPO_ROOT / "openspec" / "specs" / "connector-spotify" / "spec.md"
_CREDENTIALS_SPEC = _REPO_ROOT / "openspec" / "specs" / "core-credentials" / "spec.md"
_SPOTIFY_MODULE_SPEC = _REPO_ROOT / "openspec" / "specs" / "module-spotify" / "spec.md"
_RFC_0006 = (
    _REPO_ROOT / "about" / "legends-and-lore" / "rfcs" / "0006-database-schema-and-isolation.md"
)


def _read(path: Path) -> str:
    assert path.exists(), f"expected normative artifact is missing: {path.relative_to(_REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _requirement(document: str, title: str) -> str:
    marker = f"### Requirement: {title}"
    start = document.index(marker)
    end = document.find("\n### Requirement:", start + len(marker))
    return document[start:] if end == -1 else document[start:end]


def test_active_carrier_declares_spotify_connector_and_passport_boundaries() -> None:
    """The active carrier must make the authority decision implementation-ready."""
    carrier = _read(_CARRIER_SPEC)

    assert "### Requirement: Spotify connector authority and Passport projection" in carrier
    assert "Spotify connector PKCE is the only production Spotify authorization flow." in carrier
    assert "Spotify access and refresh tokens are RFC 0006 Tier 2 credentials." in carrier
    assert "`public.entity_info`" in carrier
    assert "owner entity" in carrier
    assert "`resolve_owner_entity_info()`" in carrier
    assert "The Passport projection is content-blind and connector-owned." in carrier


def test_canonical_specs_do_not_route_spotify_through_generic_oauth() -> None:
    """Spotify recovery must enter the connector PKCE flow through its Passport projection."""
    ingestion = _read(_INGESTION_SPEC)
    passport = _read(_PASSPORT_SPEC)
    setup = _read(_SPOTIFY_SETUP_SPEC)
    connector = _read(_SPOTIFY_CONNECTOR_SPEC)
    credentials = _read(_CREDENTIALS_SPEC)

    assert "### Requirement: Connector-owned Spotify recovery" in ingestion
    assert "`POST /api/connectors/spotify/oauth/start`" in ingestion
    assert "`GET /api/oauth/{google|spotify}/start`" not in ingestion
    assert "registered `spotify` OAuth provider" not in ingestion

    assert "### Requirement: Connector-owned Passport projections" in passport
    assert "`u:spotify` is a connector-owned Passport projection" in passport
    assert "`listening-history`" in passport
    assert "Spotify is different: its connector-owned PKCE flow begins" in passport

    assert "Spotify connector PKCE is the only production Spotify authorization flow." in setup
    assert "it SHALL NOT expose `missing_scopes`" in setup
    assert "The connector owns the production Spotify PKCE route" in connector
    assert (
        "Spotify access and refresh tokens SHALL remain RFC 0006 Tier 2 credentials" in credentials
    )
    assert "`resolve_owner_entity_info()`" in credentials


def test_dashboard_api_excludes_spotify_from_generic_oauth_and_user_credential_authority() -> None:
    """Generic provider and entity-info rules cannot reclaim Spotify authority."""
    dashboard_api = _read(_DASHBOARD_API_SPEC)
    mutations = _requirement(dashboard_api, "Secrets Mutation Endpoints")
    generic_oauth = _requirement(dashboard_api, "OAuth Per-Provider Generalisation")

    assert "`POST /api/secrets/user/spotify/reauthorize` SHALL NOT" in mutations
    assert "`public.entity_info` record" in mutations
    assert "`POST /api/connectors/spotify/oauth/start`" in mutations

    assert "The production generic OAuth provider registry is Google-only." in generic_oauth
    assert "`GET /api/oauth/spotify/start`" in generic_oauth
    assert "MUST NOT persist Spotify token material to `public.entity_info`" in generic_oauth
    assert "`GET /api/connectors/spotify/oauth/callback`" in generic_oauth


def test_carrier_serializes_the_two_downstream_implementation_lanes() -> None:
    """The cleanup must depend on the projection in artifacts and tracker handoff."""
    carrier = _collapse_whitespace(_read(_CARRIER_SPEC))
    tasks = _collapse_whitespace(_read(_CARRIER_TASKS))
    design = _collapse_whitespace(_read(_CARRIER_DESIGN))

    assert "`bu-fj7lx` implements the Passport projection; `bu-3ifcj` then removes" in design
    assert (
        "A `discovered-from` relation is provenance only; it is not a dispatch prerequisite."
        in design
    )
    assert (
        "the tracker SHALL contain a `blocks` prerequisite from `bu-3ifcj` to `bu-fj7lx` "
        "before cleanup dispatch."
    ) in carrier
    assert "`bd dep add bu-3ifcj bu-fj7lx --type blocks`" in tasks
    assert "Before dispatching `bu-3ifcj`" in tasks
    assert (
        "A `discovered-from` relation is not a substitute for that `blocks` prerequisite" in tasks
    )
    assert "synthetic generalized-provider fixture" in tasks
    assert "no compatibility alias, shim, or production registry entry" in tasks


def test_reauth_lifecycle_authority_has_a_live_canonical_archive_target() -> None:
    """Archive must retain the authority split in the canonical ingestion contract."""
    canonical = _read(_INGESTION_SPEC)
    lifecycle = _requirement(
        _read(_DASHBOARD_INGESTION_LIFECYCLE_DELTA), "OAuth reauth lifecycle authority"
    )

    assert _DASHBOARD_INGESTION_LIFECYCLE_DELTA.parent.name == _INGESTION_SPEC.parent.name
    assert not _STALE_LIFECYCLE_DELTA.exists()
    assert "### Requirement: Ingestion-Originated OAuth page_of_origin Contract" in canonical
    assert "## ADDED Requirements" in _read(_DASHBOARD_INGESTION_LIFECYCLE_DELTA)

    assert "#### Scenario: Generic OAuth reauth is Approvals-gated" in lifecycle
    assert "`connector-oauth-scope-surface/spec`" in lifecycle
    assert "#### Scenario: Spotify reauth stays connector-owned" in lifecycle
    assert "`/secrets?focus=u:spotify`" in lifecycle
    assert "`POST /api/connectors/spotify/oauth/start`" in lifecycle
    assert "RFC 0006 Tier 2" in lifecycle
    assert "`public.entity_info` on the owner entity" in lifecycle
    assert "`resolve_owner_entity_info()`" in lifecycle
    assert "SHALL NOT submit the recovery to the Approvals module" in lifecycle
    assert "#### Scenario: Non-OAuth reauth is rejected before approval" in lifecycle
    assert "SHALL NOT pass through the Approvals module" in lifecycle


def test_active_carrier_limits_generic_approval_and_audit_to_generic_oauth() -> None:
    """The active carrier must not turn Spotify or non-OAuth reauth into generic approval flow."""
    proposal = _collapse_whitespace(_read(_CARRIER_PROPOSAL))
    carrier = _collapse_whitespace(_read(_CARRIER_SPEC))
    design = _collapse_whitespace(_read(_CARRIER_DESIGN))

    assert (
        "Only generic OAuth reauth is Approvals-gated and emits the generic reauth audit sequence."
        in proposal
    )
    assert (
        "Spotify continues directly from its content-blind Passport projection to the connector-owned PKCE flow."
        in proposal
    )
    assert "Non-OAuth reauth is rejected before the Approvals module." in proposal

    assert (
        "Only generic OAuth reauth is gated through the Approvals module and emits the generic reauth audit sequence."
        in carrier
    )
    assert (
        "Spotify continues directly from its content-blind Passport projection to the connector-owned PKCE flow."
        in carrier
    )
    assert "Non-OAuth reauth is rejected before the Approvals module." in carrier

    assert "This Approvals-gated state issuance applies only to generic OAuth reauth." in design
    assert (
        "The standard generic OAuth reauth audit sequence applies only to generic OAuth reauth."
        in design
    )
    assert (
        "Spotify's direct Passport-to-connector PKCE recovery is not submitted to Approvals and does not require generic submit or approval-resolution audit records."
        in design
    )
    assert "A non-OAuth target is rejected before any Approvals submission." in design


def test_rfc_0006_tier2_authority_projects_into_spotify_specs() -> None:
    """The higher-layer credential rule must causally bind every Spotify carrier."""
    rfc = _read(_RFC_0006)
    assert "#### Tier 2 — User (entity_info on owner entity)" in rfc
    assert "Identity-bound credentials tied to the owner's personal accounts." in rfc
    assert "Accessed at runtime via `resolve_owner_entity_info(pool, info_type)`" in rfc
    assert (
        "Connectors needing Tier 2 credentials MUST use `resolve_owner_entity_info()`, "
        "never `CredentialStore`."
    ) in rfc

    tier2_sections = {
        "active OAuth carrier": _requirement(
            _read(_CARRIER_SPEC), "Spotify connector authority and Passport projection"
        ),
        "archive-surviving dashboard delta": _requirement(
            _read(_DASHBOARD_INGESTION_LIFECYCLE_DELTA), "OAuth reauth lifecycle authority"
        ),
        "canonical ingestion recovery": _requirement(
            _read(_INGESTION_SPEC), "Connector-owned Spotify recovery"
        ),
        "canonical credential storage": _requirement(
            _read(_CREDENTIALS_SPEC), "Spotify OAuth Token Storage"
        ),
        "canonical Spotify setup": _requirement(
            _read(_SPOTIFY_SETUP_SPEC),
            "Connector-Owned Spotify OAuth 2.0 PKCE Authorization Flow",
        ),
        "canonical Spotify connector": _requirement(
            _read(_SPOTIFY_CONNECTOR_SPEC), "Connector-Owned Production Spotify PKCE"
        ),
        "canonical Passport projection": _requirement(
            _read(_PASSPORT_SPEC), "Connector-owned Passport projections"
        ),
        "canonical Spotify module": _requirement(
            _read(_SPOTIFY_MODULE_SPEC), "Credential Resolution"
        ),
    }

    for artifact, section in tier2_sections.items():
        assert "RFC 0006 Tier 2" in section, artifact
        assert "public.entity_info" in section, artifact
        assert "resolve_owner_entity_info" in section, artifact
        normalized = _collapse_whitespace(section).casefold()
        for forbidden_tier1_claim in (
            "credentialstore is the sole authority for spotify token material",
            "tokens shall be stored in credentialstore",
            "token shall be resolved from credentialstore",
            "token material only through credentialstore",
            "credentialstore entries remain the only authority for spotify token material",
        ):
            assert forbidden_tier1_claim not in normalized, artifact

    credential_storage = tier2_sections["canonical credential storage"]
    assert "`SPOTIFY_CLIENT_ID`" in credential_storage
    assert "Tier 1" in credential_storage
    assert "`CredentialStore`" in credential_storage
    assert "Passport projection" in credential_storage
    assert "SHALL NOT duplicate, persist, or mutate Spotify token material" in credential_storage
