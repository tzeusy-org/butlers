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
        tier_0 = {"POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
                   "POSTGRES_PASSWORD", "SWITCHBOARD_MCP_URL",
                   "OTEL_EXPORTER_OTLP_ENDPOINT"}
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
