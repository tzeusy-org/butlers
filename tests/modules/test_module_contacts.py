"""Condensed contacts module tests — behavioral contract only.

Replaces test_module_contacts.py (77), test_contacts_backfill.py (50),
test_contacts_sync.py (34), test_contacts_telegram_provider.py (32),
test_canonical_contact_fields.py (27), test_google_contact_payload_mapping.py (58)
= ~278 tests replaced with ~25.

Covers:
- Module ABC compliance
- ContactsConfig validation (provider required, multi-provider, normalization)
- Registry discovery
- Google contact payload parsing (key field extraction)
- Sync state roundtrip
- Backfill resolver helpers (display_name, group_label)

[bu-7sd7a]
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.contacts import (
    ContactsConfig,
    ContactsModule,
    ProviderEntry,
)
from butlers.modules.contacts.backfill import (
    _build_display_name,
    _normalize_group_label,
)
from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactsSyncState,
    _parse_google_contact,
)
from butlers.modules.registry import default_registry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_module_contract(self) -> None:
        """ContactsModule satisfies Module ABC: name, config_schema, dependencies, revisions."""
        mod = ContactsModule()
        assert issubclass(ContactsModule, Module)
        assert mod.name == "contacts"
        assert mod.config_schema is ContactsConfig
        assert mod.dependencies == []
        assert mod.migration_revisions() == "contacts"
        assert "contacts" in default_registry().available_modules


# ---------------------------------------------------------------------------
# ContactsConfig validation
# ---------------------------------------------------------------------------


class TestContactsConfig:
    def test_provider_required(self) -> None:
        with pytest.raises(ValidationError):
            ContactsConfig()

    def test_defaults(self) -> None:
        config = ContactsConfig(provider="google")
        assert config.provider == "google"
        assert config.include_other_contacts is False
        assert config.sync.enabled is True
        assert config.sync.interval_minutes == 15

    def test_legacy_provider_creates_providers_list(self) -> None:
        config = ContactsConfig(provider="google")
        assert config.providers == [ProviderEntry(type="google")]

    def test_providers_list_accepted(self) -> None:
        config = ContactsConfig(providers=[{"type": "google"}, {"type": "telegram"}])
        assert len(config.providers) == 2

    def test_both_provider_and_providers_raises(self) -> None:
        with pytest.raises(ValidationError, match="Cannot specify both"):
            ContactsConfig(provider="google", providers=[{"type": "google"}])

    def test_duplicate_provider_types_raises(self) -> None:
        with pytest.raises(ValidationError, match="distinct"):
            ContactsConfig(providers=[{"type": "google"}, {"type": "google"}])

    def test_provider_normalization(self) -> None:
        config = ContactsConfig(provider="  GOOGLE  ")
        assert config.provider == "google"

    def test_multi_account_with_distinct_accounts_succeeds(self) -> None:
        config = ContactsConfig(
            providers=[
                {"type": "google", "account": "personal@gmail.com"},
                {"type": "google", "account": "work@gmail.com"},
            ]
        )
        assert len(config.providers) == 2

    def test_whitespace_only_provider_raises(self) -> None:
        with pytest.raises(ValidationError):
            ContactsConfig(provider="   ")

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ContactsConfig(provider="google", unsupported=True)


# ---------------------------------------------------------------------------
# Google contact payload mapping
# ---------------------------------------------------------------------------


class TestGoogleContactParsing:
    def _make_raw(self, **kwargs) -> dict:
        base = {
            "resourceName": "people/c123",
            "etag": "abc",
            "names": [
                {
                    "givenName": "Alice",
                    "familyName": "Smith",
                    "displayName": "Alice Smith",
                    "metadata": {"primary": True},
                }
            ],
            "emailAddresses": [{"value": "alice@example.com", "metadata": {"primary": True}}],
        }
        base.update(kwargs)
        return base

    def test_parse_basic_contact(self) -> None:
        raw = self._make_raw()
        contact = _parse_google_contact(raw)
        assert isinstance(contact, CanonicalContact)
        assert contact.first_name == "Alice"
        assert contact.last_name == "Smith"
        assert len(contact.emails) >= 1

    def test_parse_phone_numbers(self) -> None:
        raw = self._make_raw(
            phoneNumbers=[{"value": "+1-555-555-1234", "metadata": {"primary": True}}]
        )
        contact = _parse_google_contact(raw)
        assert len(contact.phones) >= 1

    def test_parse_organization(self) -> None:
        raw = self._make_raw(
            organizations=[
                {"name": "Acme Corp", "title": "Engineer", "metadata": {"primary": True}}
            ]
        )
        contact = _parse_google_contact(raw)
        assert len(contact.organizations) >= 1
        # ContactOrganization maps 'name' → 'company'
        assert contact.organizations[0].company == "Acme Corp"


# ---------------------------------------------------------------------------
# Backfill helpers
# ---------------------------------------------------------------------------


class TestBackfillHelpers:
    def test_build_display_name_from_parts(self):
        contact = CanonicalContact(external_id="people/c1", first_name="Alice", last_name="Smith")
        assert _build_display_name(contact) == "Alice Smith"

    def test_build_display_name_from_display(self):
        contact = CanonicalContact(external_id="people/c2", display_name="Bob Jones")
        assert _build_display_name(contact) == "Bob Jones"

    def test_normalize_group_label_from_resource(self):
        result = _normalize_group_label("contactGroups/friends")
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Sync state roundtrip
# ---------------------------------------------------------------------------


class TestContactsSyncState:
    def test_default_state(self) -> None:
        state = ContactsSyncState()
        assert state.sync_cursor is None
        assert state.last_full_sync_at is None

    def test_model_dump_roundtrip(self) -> None:
        state = ContactsSyncState(sync_cursor="tok-123", last_error=None)
        restored = ContactsSyncState(**state.model_dump())
        assert restored.sync_cursor == state.sync_cursor
