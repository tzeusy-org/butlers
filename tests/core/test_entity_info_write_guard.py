"""Tests for the entity_info write-guard added in RFC 0004 Amendment 3 (bu-oluyt.1).

The seam law: entity_info is a credentials-only store.
Non-secret identifiers must go to relationship.entity_facts instead.
"""

from __future__ import annotations

import pytest

from butlers.credential_store import assert_entity_info_secured

pytestmark = pytest.mark.unit


class TestAssertEntityInfoSecured:
    """Unit tests for assert_entity_info_secured()."""

    # -----------------------------------------------------------------------
    # Allowed: secured=True (any type)
    # -----------------------------------------------------------------------

    def test_secured_google_oauth_allowed(self) -> None:
        """Standard secured credential — must pass silently."""
        assert_entity_info_secured("google_oauth_refresh", secured=True)

    def test_secured_telegram_api_hash_allowed(self) -> None:
        assert_entity_info_secured("telegram_api_hash", secured=True)

    def test_secured_steam_api_key_allowed(self) -> None:
        assert_entity_info_secured("steam_api_key", secured=True)

    def test_secured_arbitrary_type_allowed(self) -> None:
        """Any type with secured=True must be allowed — no whitelist restriction."""
        assert_entity_info_secured("some_future_secret_type", secured=True)

    # -----------------------------------------------------------------------
    # Allowed: whitelisted non-secret technical identifiers
    # -----------------------------------------------------------------------

    def test_telegram_api_id_not_secured_allowed(self) -> None:
        """telegram_api_id is a technical credential component; allowed with secured=False."""
        assert_entity_info_secured("telegram_api_id", secured=False)

    def test_telegram_api_id_secured_also_allowed(self) -> None:
        """telegram_api_id with secured=True is also fine (stricter, always passes)."""
        assert_entity_info_secured("telegram_api_id", secured=True)

    def test_home_assistant_url_not_secured_allowed(self) -> None:
        """home_assistant_url is a service URL config entry; no predicate home in entity_facts."""
        assert_entity_info_secured("home_assistant_url", secured=False)

    def test_home_assistant_url_secured_also_allowed(self) -> None:
        """home_assistant_url with secured=True is also fine (stricter, always passes)."""
        assert_entity_info_secured("home_assistant_url", secured=True)

    # -----------------------------------------------------------------------
    # Rejected: non-secret types that belong in entity_facts
    # -----------------------------------------------------------------------

    def test_telegram_chat_id_not_secured_rejected(self) -> None:
        """telegram_chat_id is a routing handle; it must go to entity_facts (has-handle)."""
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured("telegram_chat_id", secured=False)

    def test_telegram_not_secured_rejected(self) -> None:
        """Plain 'telegram' handle type must be rejected."""
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured("telegram", secured=False)

    def test_email_not_secured_rejected(self) -> None:
        """Email addresses are entity_facts (has-email) — not credentials."""
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured("email", secured=False)

    def test_phone_not_secured_rejected(self) -> None:
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured("phone", secured=False)

    def test_linkedin_not_secured_rejected(self) -> None:
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured("linkedin", secured=False)

    def test_twitter_not_secured_rejected(self) -> None:
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured("twitter", secured=False)

    def test_whatsapp_jid_not_secured_rejected(self) -> None:
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured("whatsapp_jid", secured=False)

    def test_unknown_type_not_secured_rejected(self) -> None:
        """Any unknown/unwhitelisted type with secured=False must be rejected."""
        with pytest.raises(ValueError, match="entity_info write rejected"):
            assert_entity_info_secured("some_new_handle_type", secured=False)

    # -----------------------------------------------------------------------
    # Error message content checks
    # -----------------------------------------------------------------------

    def test_error_message_mentions_entity_facts(self) -> None:
        """Error must guide the caller to entity_facts (has-handle predicate)."""
        with pytest.raises(ValueError, match="entity_facts"):
            assert_entity_info_secured("telegram_chat_id", secured=False)

    def test_error_message_mentions_rfc(self) -> None:
        """Error must cite RFC 0004 Amendment 3 for traceability."""
        with pytest.raises(ValueError, match="RFC 0004 Amendment 3"):
            assert_entity_info_secured("telegram_chat_id", secured=False)

    def test_error_message_includes_type_name(self) -> None:
        """Error message must include the rejected type for diagnosability."""
        with pytest.raises(ValueError, match="telegram_chat_id"):
            assert_entity_info_secured("telegram_chat_id", secured=False)
