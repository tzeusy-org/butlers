"""Tests for the entity_info write-guard added in RFC 0004 Amendment 3 (bu-oluyt.1).

The seam law: entity_info is a credentials-only store.
Non-secret identifiers must go to relationship.entity_facts instead.
"""

from __future__ import annotations

import pytest

from butlers.credential_store import assert_entity_info_secured

pytestmark = pytest.mark.unit


class TestAssertEntityInfoSecured:
    """Unit tests for assert_entity_info_secured().

    The seam law (RFC 0004 Amendment 3): entity_info is a credentials-only
    store. Any type passes when secured=True; with secured=False, only the
    whitelisted technical identifiers pass — every other handle/identifier
    type is rejected so it routes to relationship.entity_facts instead.
    """

    # Allowed: secured=True must pass for ANY type (no whitelist restriction).
    @pytest.mark.parametrize(
        "info_type",
        [
            "google_oauth_refresh",
            "telegram_api_hash",
            "steam_api_key",
            "some_future_secret_type",
            "telegram_api_id",
            "home_assistant_url",
        ],
    )
    def test_secured_true_always_allowed(self, info_type: str) -> None:
        assert_entity_info_secured(info_type, secured=True)

    # Allowed: whitelisted non-secret technical identifiers with secured=False.
    @pytest.mark.parametrize("info_type", ["telegram_api_id", "home_assistant_url"])
    def test_whitelisted_not_secured_allowed(self, info_type: str) -> None:
        assert_entity_info_secured(info_type, secured=False)

    # Rejected: non-secret handle/identifier types with secured=False must be
    # rejected — they belong in entity_facts. Enumerates every reject branch.
    @pytest.mark.parametrize(
        "info_type",
        [
            "telegram_chat_id",
            "telegram",
            "email",
            "phone",
            "linkedin",
            "twitter",
            "whatsapp_jid",
            "some_new_handle_type",  # unknown/unwhitelisted type
        ],
    )
    def test_not_secured_rejected(self, info_type: str) -> None:
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured(info_type, secured=False)

    def test_rejection_message_is_contextual(self) -> None:
        """Rejection error guides to entity_facts, cites the RFC, and names the type."""
        with pytest.raises(ValueError) as exc_info:
            assert_entity_info_secured("telegram_chat_id", secured=False)
        message = str(exc_info.value)
        assert "entity_facts" in message
        assert "RFC 0004 Amendment 3" in message
        assert "telegram_chat_id" in message
