"""Specification consistency checks for Spotify OAuth authority (bu-27dxl.7.6.3).

These checks deliberately inspect the normative artifacts rather than the
currently transitional implementation.  The active OAuth scope-surface change
and the canonical dashboard/credential contracts must agree before its two
serialized implementation beads can safely remove the generic OAuth Spotify
exemplar and add the Passport projection.
"""

from __future__ import annotations

import ast
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
_DASHBOARD_RELATIONSHIP_SPEC = (
    _REPO_ROOT / "openspec" / "specs" / "dashboard-relationship" / "spec.md"
)
_PASSPORT_SPEC = _REPO_ROOT / "openspec" / "specs" / "butler-secrets" / "spec.md"
_SPOTIFY_SETUP_SPEC = _REPO_ROOT / "openspec" / "specs" / "dashboard-spotify-setup" / "spec.md"
_SPOTIFY_CONNECTOR_SPEC = _REPO_ROOT / "openspec" / "specs" / "connector-spotify" / "spec.md"
_CREDENTIALS_SPEC = _REPO_ROOT / "openspec" / "specs" / "core-credentials" / "spec.md"
_SPOTIFY_MODULE_SPEC = _REPO_ROOT / "openspec" / "specs" / "module-spotify" / "spec.md"
_ENTITY_IDENTITY_SPEC = _REPO_ROOT / "openspec" / "specs" / "entity-identity" / "spec.md"
_USER_SECRET_TYPES = _REPO_ROOT / "frontend" / "src" / "lib" / "user-secret-templates.ts"
_PASSPORT_PAGES = (
    _REPO_ROOT / "frontend" / "src" / "components" / "secrets" / "passport" / "pages.tsx"
)
_SECRETS_ROUTER = _REPO_ROOT / "src" / "butlers" / "api" / "routers" / "secrets_v2.py"
_RELATIONSHIP_ROUTER = _REPO_ROOT / "roster" / "relationship" / "api" / "router.py"
_CONNECTOR_BASE_DELTA = (
    _REPO_ROOT
    / "openspec"
    / "changes"
    / "add-connector-oauth-scope-surface"
    / "specs"
    / "connector-base-spec"
    / "spec.md"
)
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


def _scenario(requirement: str, title: str) -> str:
    marker = f"#### Scenario: {title}"
    start = requirement.index(marker)
    end = requirement.find("\n#### Scenario:", start + len(marker))
    return requirement[start:] if end == -1 else requirement[start:end]


def _router_seams(path: Path) -> set[tuple[str, str, str]]:
    """Return method, path, and handler triples from literal router decorators."""
    seams: set[tuple[str, str, str]] = set()
    tree = ast.parse(_read(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            function = decorator.func
            if (
                not isinstance(function, ast.Attribute)
                or not isinstance(function.value, ast.Name)
                or function.value.id != "router"
                or function.attr not in {"get", "post", "patch", "delete"}
                or not isinstance(decorator.args[0], ast.Constant)
                or not isinstance(decorator.args[0].value, str)
            ):
                continue
            seams.add((function.attr.upper(), decorator.args[0].value, node.name))
    return seams


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


def test_spotify_tier2_tokens_are_connector_managed_passport_exceptions() -> None:
    """The shared read helper must not imply a generic editable or Tier 1 authority."""
    entity_registry = _requirement(
        _read(_ENTITY_IDENTITY_SPEC), "Entity info type registry (frontend ↔ backend coupling)"
    )

    assert "user-provisioned module credential dependencies" in entity_registry
    assert "connector-managed Tier 2 exception" in entity_registry
    assert "every `info_type` that a module resolves MUST be present" not in entity_registry
    dropdown_source = _read(_USER_SECRET_TYPES)
    dropdown = dropdown_source.split("export const ENTITY_INFO_TYPES = [", 1)[1].split(
        "] as const", 1
    )[0]
    for info_type in (
        "spotify_oauth_access",
        "spotify_oauth_refresh",
        "spotify_oauth_expires_at",
    ):
        assert f"`{info_type}`" in entity_registry
        assert f'"{info_type}"' not in dropdown
    assert "`PassportAddPanel`" in entity_registry
    assert "MUST NOT be present in the frontend `ENTITY_INFO_TYPES` dropdown" in entity_registry
    assert "dashboard entity detail page" not in entity_registry
    assert "navigates to the entity detail page" not in entity_registry
    assert "navigates to `/secrets`" in entity_registry
    assert "`useCreateUserSecret`" in entity_registry

    passport_pages = _read(_PASSPORT_PAGES)
    assert "export function PassportAddPanel" in passport_pages
    assert "const userMutation = useCreateUserSecret();" in passport_pages
    assert "{ENTITY_INFO_TYPES.map((t) => (" in passport_pages

    spotify_sections = {
        "active OAuth carrier": _requirement(
            _read(_CARRIER_SPEC), "Spotify connector authority and Passport projection"
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

    for artifact, section in spotify_sections.items():
        assert "connector-managed Tier 2" in section, artifact
        assert "PassportAddPanel" in section, artifact
        assert "CredentialStore" in section, artifact
        assert "Relationship entity-info" in section, artifact


def test_spotify_missing_credentials_use_tier2_names_without_legacy_key_authority() -> None:
    """Startup recovery must name the real Tier 2 types, not retired env-style keys."""
    credential_resolution = _requirement(_read(_SPOTIFY_MODULE_SPEC), "Credential Resolution")
    missing = _scenario(credential_resolution, "Missing credentials at startup")

    assert "`spotify_oauth_access`" in missing
    assert "`spotify_oauth_refresh`" in missing
    assert "`resolve_owner_entity_info()`" in missing
    assert "`SPOTIFY_ACCESS_TOKEN`" not in missing
    assert "`SPOTIFY_REFRESH_TOKEN`" not in missing
    assert "active non-archive key authority" in credential_resolution
    assert "MUST NOT" in _collapse_whitespace(credential_resolution)


def test_generic_secrets_api_requires_a_server_side_spotify_fence_at_every_seam() -> None:
    """UI omission is not an authority fence; every generic server seam must reject Spotify."""
    dashboard_api = _requirement(
        _read(_DASHBOARD_API_SPEC), "Spotify exclusion from generic Secrets authority"
    )
    passport = _requirement(_read(_PASSPORT_SPEC), "Connector-owned Passport projections")
    carrier = _requirement(
        _read(_CARRIER_SPEC), "Spotify connector authority and Passport projection"
    )
    tasks = _collapse_whitespace(_read(_CARRIER_TASKS))

    expected_generic_routes = (
        "GET /api/secrets/inventory",
        "GET /api/secrets/user/{provider}",
        "POST /api/secrets/user/{provider}/rotate",
        "POST /api/secrets/user/{provider}/disconnect",
        "POST /api/secrets/user/{provider}/probe",
        "POST /api/secrets/user/{provider}/reauthorize",
    )
    for route in expected_generic_routes:
        assert f"`{route}`" in dashboard_api
        assert f"`{route}`" in carrier

    for section in (dashboard_api, passport, carrier):
        assert "server-side" in section
        assert "before any `public.entity_info` lookup or mutation" in section
        assert "Spotify connector endpoints" in section

    assert "`src/butlers/api/routers/secrets_v2.py`" in carrier
    assert "`PassportAddPanel`" in carrier
    assert "bu-fj7lx" in tasks
    assert "secrets_v2.py" in tasks
    assert "inventory, detail/read, rotate, disconnect, probe, and reauthorize" in tasks

    # Trace the normative fence to the real generic raw-editor and API seams.
    router = _read(_SECRETS_ROUTER)
    for route_fragment in (
        '"/inventory"',
        '"/user/{provider}"',
        '"/user/{provider}/rotate"',
        '"/user/{provider}/disconnect"',
        '"/user/{provider}/probe"',
        '"/user/{provider}/reauthorize"',
    ):
        assert route_fragment in router


def test_generic_rejection_is_indistinguishable_and_direction_stays_on_projection() -> None:
    """A generic 404 cannot simultaneously disclose Spotify-specific recovery guidance."""
    dashboard_api = _requirement(
        _read(_DASHBOARD_API_SPEC), "Spotify exclusion from generic Secrets authority"
    )
    generic_rejection = _scenario(dashboard_api, "Generic Secrets routes cannot address Spotify")

    normalized_rejection = _collapse_whitespace(generic_rejection)
    assert "indistinguishable from a genuinely absent generic credential" in normalized_rejection
    assert "SHALL NOT direct the caller" in normalized_rejection
    assert "Passport projection or connector card" in _collapse_whitespace(dashboard_api)
    assert "direct interactive lifecycle work" not in generic_rejection


def test_generic_relationship_entity_info_surfaces_are_fenced_and_allocated() -> None:
    """The downstream handoff must fence every real generic Relationship authority seam."""
    dashboard_api = _requirement(
        _read(_DASHBOARD_API_SPEC),
        "Spotify exclusion from generic Relationship entity-info authority",
    )
    dashboard_relationship = _requirement(
        _read(_DASHBOARD_RELATIONSHIP_SPEC),
        "Connector-managed Spotify Tier 2 exclusion from generic entity-info authority",
    )
    passport = _requirement(_read(_PASSPORT_SPEC), "Connector-owned Passport projections")
    carrier = _requirement(
        _read(_CARRIER_SPEC), "Spotify connector authority and Passport projection"
    )
    tasks = _collapse_whitespace(_read(_CARRIER_TASKS))

    generic_relationship_seams = (
        ("GET", "/owner/entity-info", "get_owner_entity_info"),
        ("GET", "/entities/{entity_id}", "get_entity"),
        ("POST", "/entities/{entity_id}/info", "create_entity_info"),
        ("PATCH", "/entities/{entity_id}/info/{info_id}", "patch_entity_info"),
        ("DELETE", "/entities/{entity_id}/info/{info_id}", "delete_entity_info"),
        ("GET", "/entities/{entity_id}/secrets/{info_id}", "reveal_entity_secret"),
        ("GET", "/entities/{entity_id}/linked-contacts", "list_entity_linked_contacts"),
    )
    actual_seams = _router_seams(_RELATIONSHIP_ROUTER)
    for method, route, handler in generic_relationship_seams:
        qualified_route = f"{method} /api/relationship{route}"
        for artifact, section in (
            ("dashboard-api", dashboard_api),
            ("dashboard-relationship", dashboard_relationship),
            ("active carrier", carrier),
        ):
            assert f"`{qualified_route}`" in section, artifact
        assert qualified_route in tasks
        assert (method, route, handler) in actual_seams

    for artifact, section in (
        ("dashboard-api", dashboard_api),
        ("dashboard-relationship", dashboard_relationship),
        ("active carrier", carrier),
        ("Passport", passport),
    ):
        normalized_section = _collapse_whitespace(section)
        assert "stable non-disclosing HTTP 404" in normalized_section, artifact
        assert "metadata-only type discriminator" in normalized_section, artifact
        assert "before selecting or revealing `value`" in normalized_section, artifact
        assert "connector-owned Spotify OAuth lifecycle" in normalized_section, artifact
        assert "sole authority" in normalized_section, artifact
        assert "callback is the sole initial token-creation writer" in normalized_section, artifact
        assert "connector refresh is the only permitted subsequent update" in normalized_section, (
            artifact
        )
        assert "connector disconnect is the only permitted delete" in normalized_section, artifact

    assert "`roster/relationship/api/router.py`" in carrier
    assert "`bu-fj7lx`" in tasks
    assert "generic Relationship entity-info" in tasks

    contact_detail = _requirement(_read(_DASHBOARD_RELATIONSHIP_SPEC), "Contact detail API")
    reveal = _requirement(_read(_DASHBOARD_RELATIONSHIP_SPEC), "Secured contact info reveal API")
    assert "eligible generic `public.entity_info` rows" in contact_detail
    assert "Connector-managed Spotify Tier 2 exclusion" in contact_detail
    assert (
        "the `entity_info` array MUST contain the entity's contact-channel entries"
        not in contact_detail
    )
    assert "eligible generic secured `public.entity_info` entry" in reveal
    assert "Connector-managed Spotify Tier 2 exclusion" in reveal
    assert "returns the unmasked value of a secured `public.entity_info` entry" not in reveal


def test_spotify_lifecycle_authority_rejects_callback_only_writer_language() -> None:
    """Initial creation, refresh updates, and disconnect deletion have distinct writers."""
    authority_artifacts = {
        "active carrier": _read(_CARRIER_SPEC),
        "carrier design": _read(_CARRIER_DESIGN),
        "carrier tasks": _read(_CARRIER_TASKS),
        "dashboard-api": _read(_DASHBOARD_API_SPEC),
        "dashboard-relationship": _read(_DASHBOARD_RELATIONSHIP_SPEC),
        "Passport": _read(_PASSPORT_SPEC),
        "credential storage": _read(_CREDENTIALS_SPEC),
        "entity identity": _read(_ENTITY_IDENTITY_SPEC),
        "Spotify connector": _read(_SPOTIFY_CONNECTOR_SPEC),
        "Spotify setup": _read(_SPOTIFY_SETUP_SPEC),
        "ingestion recovery": _read(_INGESTION_SPEC),
    }
    forbidden = (
        "callback remains the sole writer",
        "connector flow as the sole writer",
        "lifecycle is the sole writer",
        "only writer of those rows",
        "only writer of the tier 2 owner",
        "only the connector flow writes",
    )
    for artifact, document in authority_artifacts.items():
        normalized = _collapse_whitespace(document).casefold()
        for stale_claim in forbidden:
            assert stale_claim not in normalized, (artifact, stale_claim)

    lifecycle_sections = {
        "active carrier": _requirement(
            authority_artifacts["active carrier"],
            "Spotify connector authority and Passport projection",
        ),
        "dashboard-api": _requirement(
            authority_artifacts["dashboard-api"],
            "Spotify exclusion from generic Relationship entity-info authority",
        ),
        "dashboard-relationship": _requirement(
            authority_artifacts["dashboard-relationship"],
            "Connector-managed Spotify Tier 2 exclusion from generic entity-info authority",
        ),
        "Passport": _requirement(
            authority_artifacts["Passport"], "Connector-owned Passport projections"
        ),
        "credential storage": _requirement(
            authority_artifacts["credential storage"], "Spotify OAuth Token Storage"
        ),
        "entity identity": _requirement(
            authority_artifacts["entity identity"],
            "Entity info type registry (frontend ↔ backend coupling)",
        ),
        "Spotify connector": _requirement(
            authority_artifacts["Spotify connector"],
            "Connector-Owned Production Spotify PKCE",
        ),
        "Spotify setup": _requirement(
            authority_artifacts["Spotify setup"],
            "Connector-Owned Spotify OAuth 2.0 PKCE Authorization Flow",
        ),
        "ingestion recovery": _requirement(
            authority_artifacts["ingestion recovery"], "Connector-owned Spotify recovery"
        ),
    }
    lifecycle_claims = (
        "connector-owned Spotify OAuth lifecycle",
        "sole authority",
        "callback is the sole initial token-creation writer",
        "connector refresh is the only permitted subsequent update",
        "connector disconnect is the only permitted delete",
    )
    for artifact, section in lifecycle_sections.items():
        normalized = _collapse_whitespace(section)
        for claim in lifecycle_claims:
            assert claim in normalized, (artifact, claim)


def test_scope_status_converges_to_the_canonical_recovery_state_before_ui_resolution() -> None:
    """Carrier detail statuses must map into the canonical typed recovery resolver."""
    carrier = _requirement(_read(_CARRIER_SPEC), "Auth status computation")
    connector_base = _requirement(
        _read(_CONNECTOR_BASE_DELTA), "ConnectorDetail Pydantic auth and scopes blocks"
    )
    ingestion = _requirement(
        _read(_INGESTION_SPEC), "Ingestion-Originated OAuth page_of_origin Contract"
    )
    lifecycle = _requirement(
        _read(_DASHBOARD_INGESTION_LIFECYCLE_DELTA), "OAuth reauth lifecycle authority"
    )

    for section in (carrier, connector_base, ingestion, lifecycle):
        normalized = _collapse_whitespace(section)
        assert "`expired | rotation-needed` → `needs_reauth`" in normalized
        assert "typed recovery resolver" in normalized

    assert "Only the normalized `needs_reauth`" in ingestion
    assert "generic Google OAuth" in ingestion
    assert "`unsupported`" in ingestion
    assert "no recovery link and no network request" in ingestion
    assert "Spotify" in lifecycle
    assert "Approvals module" in lifecycle
