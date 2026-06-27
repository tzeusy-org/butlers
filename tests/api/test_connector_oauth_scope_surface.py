"""Tests for the connector-oauth-scope-surface backend capability.

Spec: openspec/changes/add-connector-oauth-scope-surface/
      specs/connector-oauth-scope-surface/spec.md

Covers:
- Drift classification (all five drift classes)
- Auth status computation (all six auth status values)
- Scope row construction (required/optional/sensitive/extra)
- Per-connector applicability matrix completeness
- Credential masking (no token values in scope/auth response fields)
- Cross-module consistency: registry required ⊆ default OAuth flow scopes (catches drift)
"""

from __future__ import annotations

import pytest

from butlers.api.oauth_scope_registry import (
    APPLICABILITY_MATRIX,
    ScopeDecl,
    ScopeManifest,
    build_scope_rows,
    classify_drift,
    compute_auth_status,
    get_applicability,
    get_scope_manifest,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def simple_manifest() -> ScopeManifest:
    """Minimal manifest for testing: two required, one optional, one sensitive."""
    return ScopeManifest(
        version=1,
        required=[
            ScopeDecl(name="scope-required-a", serif_note="Required scope A"),
            ScopeDecl(name="scope-required-b", serif_note="Required scope B"),
        ],
        optional=[
            ScopeDecl(name="scope-optional-c", serif_note="Optional scope C", category="optional"),
        ],
        sensitive=[
            ScopeDecl(
                name="scope-sensitive-d",
                serif_note="Sensitive scope D",
                category="sensitive",
                approval_reason="Needed for write access",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Drift classification tests (five drift classes)
# ---------------------------------------------------------------------------


class TestClassifyDrift:
    """Spec §Drift taxonomy and per-scope status."""

    def test_ok_when_all_required_granted(self, simple_manifest: ScopeManifest) -> None:
        drift = classify_drift(simple_manifest, ["scope-required-a", "scope-required-b"])
        assert drift == "ok"

    def test_ok_when_required_plus_extra_granted(self, simple_manifest: ScopeManifest) -> None:
        # Granting optional + required → still ok (not extra, because optional is declared)
        drift = classify_drift(
            simple_manifest,
            ["scope-required-a", "scope-required-b", "scope-optional-c"],
        )
        assert drift == "ok"

    def test_extra_when_undeclared_scope_granted(self, simple_manifest: ScopeManifest) -> None:
        drift = classify_drift(
            simple_manifest,
            [
                "scope-required-a",
                "scope-required-b",
                "scope-undeclared-x",  # not in any manifest category
            ],
        )
        assert drift == "extra"

    def test_drift_when_required_scope_missing(self, simple_manifest: ScopeManifest) -> None:
        drift = classify_drift(simple_manifest, ["scope-required-a"])  # missing scope-required-b
        assert drift == "drift"

    def test_drift_when_no_scopes_granted(self, simple_manifest: ScopeManifest) -> None:
        drift = classify_drift(simple_manifest, [])
        assert drift == "drift"

    def test_expired_when_token_rejected(self, simple_manifest: ScopeManifest) -> None:
        # Even if observed_scopes has all required, token_rejected=True → expired
        drift = classify_drift(
            simple_manifest,
            ["scope-required-a", "scope-required-b"],
            token_rejected=True,
        )
        assert drift == "expired"

    def test_ok_when_observed_scopes_is_none(self, simple_manifest: ScopeManifest) -> None:
        # None observed_scopes = never probed; classified as ok (not drift)
        # auth_status will be 'unconfigured' — drift classification itself returns ok.
        drift = classify_drift(simple_manifest, None)
        assert drift == "ok"


# ---------------------------------------------------------------------------
# Auth status computation tests (all six values)
# ---------------------------------------------------------------------------


class TestComputeAuthStatus:
    """Spec §Auth status computation."""

    def test_ok_all_required_granted_version_matches(self, simple_manifest: ScopeManifest) -> None:
        status = compute_auth_status(
            "spotify",
            simple_manifest,
            observed_scopes=["scope-required-a", "scope-required-b", "scope-optional-c"],
            required_scopes_version=1,
        )
        assert status == "ok"

    def test_degraded_when_optional_missing(self, simple_manifest: ScopeManifest) -> None:
        status = compute_auth_status(
            "spotify",
            simple_manifest,
            observed_scopes=["scope-required-a", "scope-required-b"],
            # optional scope-optional-c is missing
            required_scopes_version=1,
        )
        assert status == "degraded"

    def test_expired_when_token_rejected(self, simple_manifest: ScopeManifest) -> None:
        status = compute_auth_status(
            "spotify",
            simple_manifest,
            observed_scopes=["scope-required-a", "scope-required-b"],
            required_scopes_version=1,
            token_rejected=True,
        )
        assert status == "expired"

    def test_rotation_needed_when_scope_drift(self, simple_manifest: ScopeManifest) -> None:
        status = compute_auth_status(
            "spotify",
            simple_manifest,
            observed_scopes=["scope-required-a"],  # scope-required-b missing
            required_scopes_version=1,
        )
        assert status == "rotation-needed"

    def test_rotation_needed_when_version_behind(self, simple_manifest: ScopeManifest) -> None:
        # required_scopes_version is 0 but manifest version is 1
        status = compute_auth_status(
            "spotify",
            simple_manifest,
            observed_scopes=["scope-required-a", "scope-required-b"],
            required_scopes_version=0,
        )
        assert status == "rotation-needed"

    def test_rotation_needed_when_version_is_none(self, simple_manifest: ScopeManifest) -> None:
        status = compute_auth_status(
            "spotify",
            simple_manifest,
            observed_scopes=["scope-required-a", "scope-required-b"],
            required_scopes_version=None,  # never reauthed
        )
        assert status == "rotation-needed"

    def test_unsupported_for_non_oauth_connector(self, simple_manifest: ScopeManifest) -> None:
        # telegram_bot is non-OAuth; manifest argument is ignored
        status = compute_auth_status(
            "telegram_bot",
            simple_manifest,
            observed_scopes=["some-scope"],
            required_scopes_version=1,
        )
        assert status == "unsupported"

    def test_unsupported_when_no_manifest(self) -> None:
        # Pass None manifest for an unrecognized connector type
        status = compute_auth_status(
            "unknown_connector",
            None,
            observed_scopes=None,
            required_scopes_version=None,
        )
        assert status == "unsupported"

    def test_unconfigured_when_observed_scopes_is_none(
        self, simple_manifest: ScopeManifest
    ) -> None:
        # OAuth connector that has never completed a token introspection
        status = compute_auth_status(
            "spotify",
            simple_manifest,
            observed_scopes=None,
            required_scopes_version=None,
        )
        assert status == "unconfigured"


# ---------------------------------------------------------------------------
# Scope row construction tests
# ---------------------------------------------------------------------------


class TestBuildScopeRows:
    """Spec §Dashboard API response shape — scopes block."""

    def test_all_ok_when_fully_granted(self, simple_manifest: ScopeManifest) -> None:
        rows = build_scope_rows(
            simple_manifest,
            ["scope-required-a", "scope-required-b", "scope-optional-c", "scope-sensitive-d"],
        )
        by_name = {r.name: r for r in rows}
        assert by_name["scope-required-a"].status == "ok"
        assert by_name["scope-required-b"].status == "ok"
        assert by_name["scope-optional-c"].status == "ok"
        assert by_name["scope-sensitive-d"].status == "ok"
        assert by_name["scope-sensitive-d"].sensitive_granted is True

    def test_missing_when_not_in_observed(self, simple_manifest: ScopeManifest) -> None:
        rows = build_scope_rows(simple_manifest, ["scope-required-a"])
        by_name = {r.name: r for r in rows}
        assert by_name["scope-required-b"].status == "missing"
        assert by_name["scope-optional-c"].status == "missing"
        assert by_name["scope-sensitive-d"].status == "missing"

    def test_extra_scopes_appended_last(self, simple_manifest: ScopeManifest) -> None:
        rows = build_scope_rows(
            simple_manifest,
            ["scope-required-a", "scope-required-b", "scope-undeclared-x"],
        )
        # Extra scope should appear and be classified as extra
        extra = [r for r in rows if r.status == "extra"]
        assert len(extra) == 1
        assert extra[0].name == "scope-undeclared-x"
        assert extra[0].category == "extra"
        assert "harmless" in extra[0].serif_note

    def test_ordering_required_optional_sensitive_extra(
        self, simple_manifest: ScopeManifest
    ) -> None:
        rows = build_scope_rows(
            simple_manifest,
            ["scope-required-a", "scope-required-b", "scope-optional-c", "scope-undeclared-x"],
        )
        categories = [r.category for r in rows]
        # required entries come first, then optional, then extra (sensitive missing in observed)
        req_indices = [i for i, c in enumerate(categories) if c == "required"]
        opt_indices = [i for i, c in enumerate(categories) if c == "optional"]
        extra_indices = [i for i, c in enumerate(categories) if c == "extra"]
        assert max(req_indices) < min(opt_indices)
        assert max(opt_indices) < min(extra_indices)

    def test_none_observed_returns_all_missing(self, simple_manifest: ScopeManifest) -> None:
        rows = build_scope_rows(simple_manifest, None)
        assert all(r.status == "missing" for r in rows)
        assert len(rows) == len(simple_manifest.all_decls())

    def test_sensitive_granted_orthogonal_flag(self, simple_manifest: ScopeManifest) -> None:
        # sensitive scope granted → status=ok AND sensitive_granted=True
        rows = build_scope_rows(
            simple_manifest, ["scope-required-a", "scope-required-b", "scope-sensitive-d"]
        )
        by_name = {r.name: r for r in rows}
        sensitive_row = by_name["scope-sensitive-d"]
        assert sensitive_row.status == "ok"
        assert sensitive_row.sensitive_granted is True

    def test_sensitive_not_granted_flag_false(self, simple_manifest: ScopeManifest) -> None:
        rows = build_scope_rows(simple_manifest, ["scope-required-a", "scope-required-b"])
        by_name = {r.name: r for r in rows}
        assert by_name["scope-sensitive-d"].sensitive_granted is False


# ---------------------------------------------------------------------------
# Applicability matrix completeness test
# ---------------------------------------------------------------------------


class TestApplicabilityMatrix:
    """Spec §Per-connector applicability matrix — matrix completeness test."""

    def test_known_oauth_connectors_are_supported(self) -> None:
        oauth_types = ["spotify", "gmail", "google_calendar", "google_drive", "google_health"]
        for ct in oauth_types:
            entry = get_applicability(ct)
            assert entry.oauth_supported is True, f"{ct} should be OAuth-supported"

    def test_known_non_oauth_connectors_are_unsupported(self) -> None:
        non_oauth_types = [
            "telegram_bot",
            "telegram_user_client",
            "owntracks",
            "home_assistant",
            "whatsapp",
            "steam",
        ]
        for ct in non_oauth_types:
            entry = get_applicability(ct)
            assert entry.oauth_supported is False, f"{ct} should not be OAuth-supported"
            assert entry.alt_surface_kind is not None, f"{ct} should have an alt_surface_kind"

    def test_non_oauth_connectors_have_remediation_paths(self) -> None:
        for ct, entry in APPLICABILITY_MATRIX.items():
            if not entry.oauth_supported:
                assert entry.alt_surface_remediation_path, (
                    f"{ct} is non-OAuth but has no remediation path"
                )

    def test_unrecognized_connector_type_falls_back_to_unsupported(self) -> None:
        entry = get_applicability("future_connector_not_in_matrix")
        assert entry.oauth_supported is False
        assert entry.alt_surface_kind is not None

    def test_all_matrix_entries_have_required_fields(self) -> None:
        for ct, entry in APPLICABILITY_MATRIX.items():
            assert entry.connector_type == ct or entry.connector_type in (
                "unknown",
                ct,
            ), f"{ct} matrix entry has mismatched connector_type"
            assert entry.credential_model, f"{ct} has no credential_model"


# ---------------------------------------------------------------------------
# Scope manifest registry tests
# ---------------------------------------------------------------------------


class TestScopeManifestRegistry:
    """Spec §Manifest registry exposure."""

    def test_oauth_connectors_have_manifests(self) -> None:
        oauth_types = ["spotify", "gmail", "google_calendar", "google_drive", "google_health"]
        for ct in oauth_types:
            manifest = get_scope_manifest(ct)
            assert manifest is not None, f"{ct} should have a scope manifest"
            assert manifest.version >= 1
            assert len(manifest.required) > 0

    def test_non_oauth_connectors_have_no_manifest(self) -> None:
        non_oauth_types = ["telegram_bot", "owntracks", "home_assistant"]
        for ct in non_oauth_types:
            manifest = get_scope_manifest(ct)
            assert manifest is None, f"{ct} should not have a scope manifest"

    def test_unrecognized_connector_type_returns_none(self) -> None:
        assert get_scope_manifest("completely_unknown_connector") is None

    def test_spotify_manifest_has_required_scopes(self) -> None:
        manifest = get_scope_manifest("spotify")
        assert manifest is not None
        required_names = manifest.required_names()
        assert "user-read-recently-played" in required_names
        assert "user-read-playback-state" in required_names

    def test_gmail_manifest_has_required_scopes(self) -> None:
        manifest = get_scope_manifest("gmail")
        assert manifest is not None
        required_names = manifest.required_names()
        assert "https://www.googleapis.com/auth/gmail.readonly" in required_names

    def test_sensitive_scopes_have_approval_reason(self) -> None:
        manifest = get_scope_manifest("spotify")
        assert manifest is not None
        for decl in manifest.sensitive:
            assert decl.approval_reason, (
                f"Sensitive scope {decl.name} on spotify has no approval_reason"
            )


# ---------------------------------------------------------------------------
# Credential masking test
# ---------------------------------------------------------------------------


class TestCredentialMasking:
    """Spec §Test obligations — credential-masking test.

    Verifies that the auth/scopes blocks do not contain values that look
    like access tokens, refresh tokens, or client secrets.
    """

    import re

    _TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._\-]{40,}$")

    def _find_token_like_values(self, obj: object, parent_key: str = "") -> list[str]:
        """Recursively search for token-like string values in a nested structure."""
        findings: list[str] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                findings.extend(self._find_token_like_values(v, k))
        elif isinstance(obj, list):
            for item in obj:
                findings.extend(self._find_token_like_values(item, parent_key))
        elif isinstance(obj, str):
            if self._TOKEN_PATTERN.match(obj) and any(
                kw in parent_key.lower()
                for kw in ("token", "secret", "key", "password", "credential")
            ):
                findings.append(f"key={parent_key!r} value_len={len(obj)}")
        return findings

    def test_scope_rows_contain_no_credential_values(self, simple_manifest: ScopeManifest) -> None:
        rows = build_scope_rows(
            simple_manifest,
            ["scope-required-a", "scope-required-b"],
        )
        for row in rows:
            row_dict = {
                "name": row.name,
                "serif_note": row.serif_note,
                "granted_at": row.granted_at,
                "required_since": row.required_since,
            }
            findings = self._find_token_like_values(row_dict)
            assert not findings, f"Token-like value found in scope row: {findings}"


# ---------------------------------------------------------------------------
# Cross-module consistency: registry required ⊆ default OAuth flow scopes
# ---------------------------------------------------------------------------


class TestRegistryOAuthFlowConsistency:
    """Guard against future drift between oauth_scope_registry and oauth.py.

    Invariant: for every OAuth provider in the scope registry, the manifest's
    required scope set must be a subset of (or equal to) the scopes that the
    default OAuth flow requests when no butler.toml override is present.

    Violation means a freshly-authorized connector would immediately show
    scope drift — the surface would report required scopes as missing even
    though the connector was just authorized via the standard flow.

    Coverage: all connectors listed in _CONNECTOR_OAUTH_CONFIG below. Connectors
    absent from that map (e.g. discord, discord_user) have no oauth.py provider
    config yet and are skipped rather than failed. Add an entry to
    _CONNECTOR_OAUTH_CONFIG once an oauth.py provider config is implemented.
    """

    # Maps connector_type → (oauth_provider, extra_scope_sets_or_None).
    # extra_scope_sets: additional named scope sets beyond the provider default that
    # the connector requires the user to authorize via ?scope_set=... at authorize time.
    # None means the connector uses only the default scope composition.
    #
    # Connectors absent from this map have no oauth.py provider config and are skipped.
    _CONNECTOR_OAUTH_CONFIG: dict[str, tuple[str, list[str] | None]] = {
        "spotify": ("spotify", None),
        "gmail": ("google", None),
        "google_calendar": ("google", None),
        "google_drive": ("google", None),
        # google_health requires the RESTRICTED 'health' scope set — users must
        # explicitly authorize via ?scope_set=health because Google classifies these
        # scopes as RESTRICTED and requires a one-time privacy/security review.
        # See oauth.py comment: "RESTRICTED scopes — require Google privacy/security review".
        "google_health": ("google", ["health"]),
        # discord and discord_user have no provider config in oauth.py yet — skipped.
    }

    def _authorized_oauth_scopes(self, connector_type: str) -> frozenset[str]:
        """Return the full scope set a user receives when authorizing *connector_type*.

        Uses an empty roster directory so no butler.toml overrides are applied.
        For connectors that require extra scope sets (e.g. google_health → health),
        the extra sets are included so the check reflects the actual authorize-time scopes.
        """
        import tempfile
        from pathlib import Path

        from butlers.api.routers.oauth import (
            _PROVIDER_REGISTRY,
            _compose_provider_default_scopes,
            _compose_provider_scopes_from_sets,
        )

        cfg = self._CONNECTOR_OAUTH_CONFIG.get(connector_type)
        if cfg is None:
            return frozenset()

        oauth_provider, extra_sets = cfg
        if oauth_provider not in _PROVIDER_REGISTRY:
            return frozenset()

        provider_cfg = _PROVIDER_REGISTRY[oauth_provider]

        with tempfile.TemporaryDirectory() as tmp:
            default_str = _compose_provider_default_scopes(
                provider_cfg, oauth_provider, roster_dir=Path(tmp)
            )

        scopes: dict[str, None] = dict.fromkeys(default_str.split())

        if extra_sets:
            # Union the extra (connector-specific) scope sets into the default.
            extra_str = _compose_provider_scopes_from_sets(provider_cfg, extra_sets)
            for s in extra_str.split():
                scopes.setdefault(s, None)

        return frozenset(scopes)

    def test_registry_required_subset_of_authorized_oauth_scopes(self) -> None:
        """For each OAuth connector in the registry, required ⊆ scopes-at-authorize-time.

        This catches the class of bug where oauth_scope_registry.py declares
        scopes as 'required' that the OAuth flow never requests for that connector
        — causing every freshly-authorized connector to immediately show drift.

        "scopes-at-authorize-time" means: the default OAuth flow scopes unioned
        with any connector-specific scope sets (e.g. google_health adds 'health').

        Connectors absent from _CONNECTOR_OAUTH_CONFIG (e.g. discord, which has no
        provider config yet in oauth.py) are skipped rather than failed, since the
        flow for those providers is not implemented.
        """
        from butlers.api.oauth_scope_registry import _OAUTH_MANIFESTS

        skipped: list[str] = []
        failed: list[str] = []

        for connector_type, manifest in _OAUTH_MANIFESTS.items():
            authorized_scopes = self._authorized_oauth_scopes(connector_type)

            if not authorized_scopes:
                # No oauth.py provider config for this connector — skip.
                skipped.append(connector_type)
                continue

            required = manifest.required_names()
            missing_from_authorized = required - authorized_scopes

            if missing_from_authorized:
                failed.append(
                    f"{connector_type}: required scopes not in authorize-time OAuth scopes: "
                    f"{sorted(missing_from_authorized)}"
                )

        assert not failed, (
            "Registry required scopes are NOT a subset of the scopes the OAuth flow requests.\n"
            "A freshly-authorized connector would immediately show drift.\n"
            "Fix: either expand the scope sets in oauth.py, add an extra_sets entry to\n"
            "_CONNECTOR_OAUTH_CONFIG in this test, or move the scope to 'optional' "
            "in the manifest.\n\n"
            + "\n".join(failed)
            + (f"\n\n(Skipped — no oauth.py provider config: {skipped})" if skipped else "")
        )

    def test_spotify_required_exactly_equals_default_oauth_scopes(self) -> None:
        """Spotify registry required set equals the default OAuth flow scopes exactly.

        This is a tighter invariant than the subset check — for Spotify we know
        the exact required set and can assert equality, not just subset.  If this
        fails it means either:
          (a) oauth.py requests extra scopes the registry doesn't declare, or
          (b) the registry declares required scopes the flow doesn't request.
        Both are bugs: (a) means unexpected grants, (b) means false drift reports.
        """
        from butlers.api.oauth_scope_registry import get_scope_manifest

        manifest = get_scope_manifest("spotify")
        assert manifest is not None

        authorized_scopes = self._authorized_oauth_scopes("spotify")
        assert authorized_scopes, "Spotify must have a registered OAuth provider config"

        required = manifest.required_names()
        assert required == authorized_scopes, (
            f"Spotify required scopes in registry do not match default OAuth flow scopes.\n"
            f"Registry required : {sorted(required)}\n"
            f"Default OAuth flow: {sorted(authorized_scopes)}\n"
            f"In required but not flow: {sorted(required - authorized_scopes)}\n"
            f"In flow but not required: {sorted(authorized_scopes - required)}"
        )

    def test_google_drive_required_matches_module_write_scope(self) -> None:
        """google_drive manifest must declare the FULL drive scope the module needs.

        The google_drive module writes files and hard-fails startup unless the
        granted scopes include the full ``drive`` scope (modules/google_drive
        validates ``_DRIVE_SCOPE`` and bails otherwise). Declaring the read-only
        ``drive.readonly`` scope as the required scope would make the scope-surface
        dashboard report the WRONG required grant (read-only) when read/write is
        actually needed. This test ties the manifest to the module's real
        requirement so the two cannot drift apart (regression: bu-yvayp).
        """
        from butlers.api.oauth_scope_registry import get_scope_manifest
        from butlers.modules.google_drive import _DRIVE_SCOPE

        manifest = get_scope_manifest("google_drive")
        assert manifest is not None

        required = manifest.required_names()
        assert _DRIVE_SCOPE in required, (
            "google_drive manifest must require the full drive scope the module writes with.\n"
            f"Module requires    : {_DRIVE_SCOPE}\n"
            f"Manifest required  : {sorted(required)}"
        )
        # The read-only scope must not stand in for the full scope as required —
        # full drive supersedes it and is what the OAuth grant must cover.
        assert "https://www.googleapis.com/auth/drive.readonly" not in required, (
            "drive.readonly must not be the declared required scope — the module "
            "writes files and needs full drive; full drive supersedes read-only."
        )
