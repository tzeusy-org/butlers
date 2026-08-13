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
_INGESTION_SPEC = (
    _REPO_ROOT / "openspec" / "specs" / "dashboard-ingestion-dispatch-console" / "spec.md"
)
_DASHBOARD_API_SPEC = _REPO_ROOT / "openspec" / "specs" / "dashboard-api" / "spec.md"
_PASSPORT_SPEC = _REPO_ROOT / "openspec" / "specs" / "butler-secrets" / "spec.md"
_SPOTIFY_SETUP_SPEC = _REPO_ROOT / "openspec" / "specs" / "dashboard-spotify-setup" / "spec.md"
_SPOTIFY_CONNECTOR_SPEC = _REPO_ROOT / "openspec" / "specs" / "connector-spotify" / "spec.md"
_CREDENTIALS_SPEC = _REPO_ROOT / "openspec" / "specs" / "core-credentials" / "spec.md"


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
    assert "CredentialStore is the sole authority for Spotify token material." in carrier
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
        "The Spotify connector PKCE flow and its CredentialStore entries are the only Spotify token authority."
        in credentials
    )


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
