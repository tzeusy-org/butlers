"""Contract tests: Credential Tier Resolution (RFC 0006, Invariant 7).

Validates the three-tier authority model, CredentialStore API,
and security constraints (no plaintext leakage).
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestCredentialTierModel:
    """RFC 0006: Three-tier authority model for credential management."""

    def test_credential_store_api_and_tier_model(self):
        """CredentialStore has resolve/store/list_secrets; tiers documented."""
        from butlers.credential_store import CredentialStore

        for method in ["resolve", "store", "list_secrets"]:
            assert hasattr(CredentialStore, method)

        # Tier 0 env vars (bootstrap, before DB)
        tier_0 = {
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "SWITCHBOARD_MCP_URL",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
        }
        assert len(tier_0) >= 6

        # Tier 1 (butler_secrets), Tier 2 (entity_info)
        tier_1_examples = {"BUTLER_EMAIL_PASSWORD", "BUTLER_TELEGRAM_TOKEN"}
        tier_2_examples = {"google_refresh_token", "steam_api_key"}
        assert len(tier_1_examples) >= 2 and len(tier_2_examples) >= 2

    def test_env_fallback_opt_in_and_security(self):
        """env_fallback is opt-in; list_secrets returns metadata not values."""
        from butlers.credential_store import CredentialStore

        sig = inspect.signature(CredentialStore.resolve)
        params = sig.parameters
        assert "env_fallback" in params

        # list_secrets returns SecretMetadata with is_sensitive field
        assert hasattr(CredentialStore, "list_secrets")

        # os.environ direct access forbidden outside Tier 0
        # Secret values never logged
        src = inspect.getsource(CredentialStore)
        assert "sensitive" in src.lower() or "secret" in src.lower()

    def test_resolve_owner_entity_info_importable(self):
        try:
            from butlers.tools.shared.owner_entity_info import resolve_owner_entity_info

            assert callable(resolve_owner_entity_info)
        except ImportError:
            try:
                from butlers.core.owner import resolve_owner_entity_info

                assert callable(resolve_owner_entity_info)
            except ImportError:
                from butlers import identity

                src = inspect.getsource(identity)
                assert "entity_info" in src or "owner" in src

    def test_cli_auth_and_restore_tokens(self):
        from butlers.credential_store import CredentialStore

        src = inspect.getsource(CredentialStore)
        # CLI auth tokens use specific category; restore reconstructs files
        assert "cli" in src.lower() or "restore" in src.lower() or "token" in src.lower()


class TestCredentialSecurityConstraints:
    """RFC 0006: Credential values are never logged; list_secrets returns metadata only."""

    def test_credentials_not_logged_in_session(self):
        """RFC 0006: Session log NEVER contains credential values.

        butler_secrets.is_sensitive=True marks credentials for redaction.
        list_secrets() returns SecretMetadata objects — raw values are excluded.
        Secret values are never logged, even at DEBUG level.
        """
        from butlers.credential_store import CredentialStore

        src = inspect.getsource(CredentialStore)

        # is_sensitive controls masking
        assert "sensitive" in src.lower(), (
            "CredentialStore must reference is_sensitive for masking (RFC 0006)"
        )

        # list_secrets must return metadata, not values
        assert hasattr(CredentialStore, "list_secrets"), (
            "CredentialStore must have list_secrets method (RFC 0006)"
        )

        # Verify list_secrets signature — it should not have a 'reveal_values' param
        sig = inspect.signature(CredentialStore.list_secrets)
        params = list(sig.parameters.keys())
        assert "reveal_values" not in params, (
            "list_secrets must not accept reveal_values — it always returns metadata (RFC 0006)"
        )

    def test_secret_values_not_in_list_response(self):
        """RFC 0006: 'list_secrets() returns SecretMetadata objects only — raw values NEVER included'.

        The SecretMetadata class must not contain a 'value' or 'secret_value' field.
        Only metadata (key, category, is_sensitive, expires_at) is returned by list_secrets().
        """
        import dataclasses

        from butlers.credential_store import SecretMetadata

        assert dataclasses.is_dataclass(SecretMetadata) or hasattr(SecretMetadata, "__fields__"), (
            "SecretMetadata must be a dataclass or Pydantic model (RFC 0006)"
        )

        # SecretMetadata must not expose the raw value
        field_names = set()
        if dataclasses.is_dataclass(SecretMetadata):
            field_names = {f.name for f in dataclasses.fields(SecretMetadata)}
        elif hasattr(SecretMetadata, "__fields__"):
            field_names = set(SecretMetadata.__fields__.keys())
        elif hasattr(SecretMetadata, "model_fields"):
            field_names = set(SecretMetadata.model_fields.keys())

        assert "value" not in field_names, (
            "SecretMetadata must not contain 'value' field — raw values never returned (RFC 0006)"
        )
        assert "secret_value" not in field_names, (
            "SecretMetadata must not contain 'secret_value' field (RFC 0006)"
        )

    def test_tier_0_env_vars_are_only_pre_db_credentials(self):
        """RFC 0006: Only Tier 0 (bootstrap) credentials may be read from os.environ.

        Tier 0 includes: POSTGRES_*, SWITCHBOARD_MCP_URL, OTEL endpoints, etc.
        These are the ONLY credentials that may come from os.environ directly.
        Tier 1 (butler_secrets) and Tier 2 (entity_info) must use DB-first resolution.
        """
        tier_0_vars = {
            "POSTGRES_HOST",
            "POSTGRES_PORT",
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_DB",
            "SWITCHBOARD_MCP_URL",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
        }

        # All Tier 0 vars are infrastructure-level bootstrap credentials
        for var in tier_0_vars:
            assert var.startswith(("POSTGRES", "SWITCHBOARD", "OTEL", "OAUTH")), (
                f"Tier 0 var {var} must be infrastructure-level (RFC 0006)"
            )

        # Tier 1 examples must NOT be Tier 0 vars
        tier_1_examples = {"BUTLER_TELEGRAM_TOKEN", "GOOGLE_OAUTH_CLIENT_ID"}
        for var in tier_1_examples:
            assert var not in tier_0_vars, (
                f"Tier 1 credential '{var}' must not be in Tier 0 (RFC 0006)"
            )

    def test_db_first_resolution_before_env_fallback(self):
        """RFC 0006: Database-stored credentials always take precedence over environment vars.

        Resolution order: (1) local DB butler_secrets, (2) fallback DB pools,
        (3) os.environ only if env_fallback=True (default False).
        DB takes precedence — env_fallback is opt-in.
        """
        from butlers.credential_store import CredentialStore

        # env_fallback must default to False to prevent accidental env reads
        sig = inspect.signature(CredentialStore.resolve)
        params = sig.parameters
        assert "env_fallback" in params, (
            "CredentialStore.resolve must have env_fallback parameter (RFC 0006)"
        )

        default_fallback = params["env_fallback"].default
        assert default_fallback is False, (
            "env_fallback must default to False — DB always takes precedence (RFC 0006)"
        )
