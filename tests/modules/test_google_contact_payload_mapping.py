"""Unit tests for Google People API payload mapping in _parse_google_contact.

Covers: _parse_addresses, _parse_organizations, _parse_birthdays_and_events,
_parse_urls, _parse_photos, and their integration with _parse_google_contact.
"""

from __future__ import annotations

import pytest

from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactAddress,
    ContactDate,
    ContactOrganization,
    ContactPhoto,
    ContactUrl,
    _parse_addresses,
    _parse_birthdays_and_events,
    _parse_date_entry,
    _parse_google_contact,
    _parse_organizations,
    _parse_photos,
    _parse_urls,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _parse_addresses
# ---------------------------------------------------------------------------


class TestParseAddresses:
    def test_empty_list_returns_empty(self):
        assert _parse_addresses([]) == []

    def test_non_list_returns_empty(self):
        assert _parse_addresses(None) == []
        assert _parse_addresses("street") == []

    def test_non_dict_items_are_skipped(self):
        result = _parse_addresses(["not-a-dict", 42, None])
        assert result == []

    def test_full_address_fields(self):
        raw = [
            {
                "streetAddress": "123 Main St",
                "city": "Springfield",
                "region": "IL",
                "postalCode": "62701",
                "country": "US",
                "formattedType": "Home",
                "metadata": {"primary": True},
            }
        ]
        result = _parse_addresses(raw)
        assert len(result) == 1
        addr = result[0]
        assert isinstance(addr, ContactAddress)
        assert addr.street == "123 Main St"
        assert addr.city == "Springfield"
        assert addr.region == "IL"
        assert addr.postal_code == "62701"
        assert addr.country == "US"
        assert addr.label == "Home"
        assert addr.primary is True

    def test_all_fields_optional_empty_address_skipped(self):
        raw = [{}]
        result = _parse_addresses(raw)
        assert result == []

    def test_primary_false_when_metadata_absent(self):
        raw = [{"streetAddress": "10 Elm St"}]
        result = _parse_addresses(raw)
        assert result[0].primary is False

    def test_primary_false_when_metadata_not_dict(self):
        raw = [{"streetAddress": "10 Elm St", "metadata": "primary"}]
        result = _parse_addresses(raw)
        assert result[0].primary is False

    def test_multiple_addresses_all_mapped(self):
        raw = [
            {"streetAddress": "1 Home Rd", "formattedType": "Home", "metadata": {"primary": True}},
            {"streetAddress": "2 Work Ave", "formattedType": "Work"},
        ]
        result = _parse_addresses(raw)
        assert len(result) == 2
        assert result[0].primary is True
        assert result[1].primary is False

    def test_whitespace_only_fields_skipped(self):
        # Whitespace-only fields normalize to None; an address with all-None fields is skipped
        raw = [{"streetAddress": "  ", "city": "\t"}]
        result = _parse_addresses(raw)
        assert result == []


# ---------------------------------------------------------------------------
# _parse_organizations
# ---------------------------------------------------------------------------


class TestParseOrganizations:
    def test_empty_list_returns_empty(self):
        assert _parse_organizations([]) == []

    def test_non_list_returns_empty(self):
        assert _parse_organizations(None) == []

    def test_non_dict_items_are_skipped(self):
        result = _parse_organizations(["string", 42])
        assert result == []

    def test_full_organization_fields(self):
        raw = [
            {
                "name": "Acme Corp",
                "title": "Senior Engineer",
                "department": "Platform",
            }
        ]
        result = _parse_organizations(raw)
        assert len(result) == 1
        org = result[0]
        assert isinstance(org, ContactOrganization)
        assert org.company == "Acme Corp"
        assert org.title == "Senior Engineer"
        assert org.department == "Platform"

    def test_partial_fields(self):
        raw = [{"name": "ACME"}]
        result = _parse_organizations(raw)
        assert len(result) == 1
        assert result[0].company == "ACME"
        assert result[0].title is None
        assert result[0].department is None

    def test_all_empty_fields_skipped(self):
        # An org dict with no meaningful fields should be skipped
        raw = [{"name": "  ", "title": "", "department": None}]
        result = _parse_organizations(raw)
        assert result == []

    def test_multiple_organizations(self):
        raw = [
            {"name": "First Co", "title": "CEO"},
            {"name": "Second Co", "title": "Consultant"},
        ]
        result = _parse_organizations(raw)
        assert len(result) == 2
        assert result[0].company == "First Co"
        assert result[1].company == "Second Co"


# ---------------------------------------------------------------------------
# _parse_date_entry
# ---------------------------------------------------------------------------


class TestParseDateEntry:
    def test_full_date(self):
        item = {"date": {"year": 1990, "month": 6, "day": 15}}
        result = _parse_date_entry(item, label="birthday")
        assert result is not None
        assert result.year == 1990
        assert result.month == 6
        assert result.day == 15
        assert result.label == "birthday"

    def test_partial_date_no_year(self):
        item = {"date": {"month": 3, "day": 14}}
        result = _parse_date_entry(item, label="birthday")
        assert result is not None
        assert result.year is None
        assert result.month == 3
        assert result.day == 14

    def test_missing_date_key_returns_none(self):
        result = _parse_date_entry({}, label="birthday")
        assert result is None

    def test_date_not_dict_returns_none(self):
        result = _parse_date_entry({"date": "1990-06-15"}, label="birthday")
        assert result is None

    def test_zero_fields_normalized_to_none(self):
        # Google uses 0 to indicate a missing date component
        item = {"date": {"year": 0, "month": 1, "day": 1}}
        result = _parse_date_entry(item, label="test")
        assert result is not None
        assert result.year is None
        assert result.month == 1
        assert result.day == 1

    def test_all_null_fields_returns_none(self):
        item = {"date": {}}
        result = _parse_date_entry(item, label="birthday")
        assert result is None

    def test_non_int_year_ignored(self):
        item = {"date": {"year": "1990", "month": 6, "day": 15}}
        result = _parse_date_entry(item, label="birthday")
        # string year is not int, so year=None; month and day are None too (also string check fails)
        # Actually month and day are ints here so result should not be None
        assert result is not None
        assert result.year is None
        assert result.month == 6
        assert result.day == 15


# ---------------------------------------------------------------------------
# _parse_birthdays_and_events
# ---------------------------------------------------------------------------


class TestParseBirthdaysAndEvents:
    def test_empty_inputs(self):
        bdays, annivs = _parse_birthdays_and_events([], [])
        assert bdays == []
        assert annivs == []

    def test_none_inputs(self):
        bdays, annivs = _parse_birthdays_and_events(None, None)
        assert bdays == []
        assert annivs == []

    def test_birthday_label_always_birthday(self):
        raw_bdays = [{"date": {"year": 1985, "month": 4, "day": 10}}]
        bdays, _ = _parse_birthdays_and_events(raw_bdays, [])
        assert len(bdays) == 1
        assert isinstance(bdays[0], ContactDate)
        assert bdays[0].label == "birthday"
        assert bdays[0].year == 1985

    def test_anniversary_uses_formattedType(self):
        raw_events = [
            {"date": {"year": 2010, "month": 8, "day": 20}, "formattedType": "Anniversary"}
        ]
        _, annivs = _parse_birthdays_and_events([], raw_events)
        assert len(annivs) == 1
        assert annivs[0].label == "Anniversary"
        assert annivs[0].year == 2010

    def test_event_with_other_type(self):
        raw_events = [{"date": {"year": 2000, "month": 1, "day": 1}, "formattedType": "Other"}]
        _, annivs = _parse_birthdays_and_events([], raw_events)
        assert len(annivs) == 1
        assert annivs[0].label == "Other"

    def test_event_missing_date_skipped(self):
        raw_events = [{"formattedType": "Anniversary"}]
        _, annivs = _parse_birthdays_and_events([], raw_events)
        assert annivs == []

    def test_birthday_missing_date_skipped(self):
        raw_bdays = [{"text": "June 15"}]
        bdays, _ = _parse_birthdays_and_events(raw_bdays, [])
        assert bdays == []

    def test_non_dict_items_skipped(self):
        bdays, annivs = _parse_birthdays_and_events(["not-a-dict"], ["also-not"])
        assert bdays == []
        assert annivs == []

    def test_multiple_birthdays(self):
        raw_bdays = [
            {"date": {"year": 1985, "month": 4, "day": 10}},
            {"date": {"month": 12, "day": 25}},
        ]
        bdays, _ = _parse_birthdays_and_events(raw_bdays, [])
        assert len(bdays) == 2
        assert all(b.label == "birthday" for b in bdays)


# ---------------------------------------------------------------------------
# _parse_urls
# ---------------------------------------------------------------------------


class TestParseUrls:
    def test_empty_returns_empty(self):
        assert _parse_urls([]) == []

    def test_non_list_returns_empty(self):
        assert _parse_urls(None) == []

    def test_non_dict_items_skipped(self):
        result = _parse_urls(["http://example.com"])
        assert result == []

    def test_full_url_entry(self):
        raw = [{"value": "https://example.com", "formattedType": "Homepage"}]
        result = _parse_urls(raw)
        assert len(result) == 1
        url = result[0]
        assert isinstance(url, ContactUrl)
        assert url.value == "https://example.com"
        assert url.label == "Homepage"

    def test_url_without_type(self):
        raw = [{"value": "https://blog.example.com"}]
        result = _parse_urls(raw)
        assert len(result) == 1
        assert result[0].label is None

    def test_missing_value_skipped(self):
        raw = [{"formattedType": "Homepage"}]
        result = _parse_urls(raw)
        assert result == []

    def test_empty_value_skipped(self):
        raw = [{"value": "  "}]
        result = _parse_urls(raw)
        assert result == []

    def test_multiple_urls(self):
        raw = [
            {"value": "https://site1.com", "formattedType": "Work"},
            {"value": "https://site2.com", "formattedType": "Personal"},
        ]
        result = _parse_urls(raw)
        assert len(result) == 2
        assert result[0].value == "https://site1.com"
        assert result[1].label == "Personal"


# ---------------------------------------------------------------------------
# _parse_photos
# ---------------------------------------------------------------------------


class TestParsePhotos:
    def test_empty_returns_empty(self):
        assert _parse_photos([]) == []

    def test_non_list_returns_empty(self):
        assert _parse_photos(None) == []

    def test_non_dict_items_skipped(self):
        result = _parse_photos(["https://photo.example.com/1.jpg"])
        assert result == []

    def test_full_photo_with_primary(self):
        raw = [
            {
                "url": "https://photo.example.com/1.jpg",
                "metadata": {"primary": True},
            }
        ]
        result = _parse_photos(raw)
        assert len(result) == 1
        photo = result[0]
        assert isinstance(photo, ContactPhoto)
        assert photo.url == "https://photo.example.com/1.jpg"
        assert photo.primary is True

    def test_photo_without_primary_flag(self):
        raw = [{"url": "https://photo.example.com/2.jpg"}]
        result = _parse_photos(raw)
        assert len(result) == 1
        assert result[0].primary is False

    def test_missing_url_skipped(self):
        raw = [{"metadata": {"primary": True}}]
        result = _parse_photos(raw)
        assert result == []

    def test_empty_url_skipped(self):
        raw = [{"url": "  "}]
        result = _parse_photos(raw)
        assert result == []

    def test_multiple_photos(self):
        raw = [
            {"url": "https://photo1.com/img.jpg", "metadata": {"primary": True}},
            {"url": "https://photo2.com/img.jpg"},
        ]
        result = _parse_photos(raw)
        assert len(result) == 2
        assert result[0].primary is True
        assert result[1].primary is False


# ---------------------------------------------------------------------------
# _parse_google_contact integration — new fields
# ---------------------------------------------------------------------------


class TestParseGoogleContactNewFields:
    """Integration tests ensuring new fields are wired into _parse_google_contact."""

    def _base_payload(self) -> dict:
        return {
            "resourceName": "people/c12345",
            "etag": "etag1",
            "metadata": {},
        }

    def test_addresses_populated(self):
        payload = self._base_payload()
        payload["addresses"] = [
            {
                "streetAddress": "10 Downing St",
                "city": "London",
                "country": "UK",
                "formattedType": "Home",
                "metadata": {"primary": True},
            }
        ]
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert isinstance(contact, CanonicalContact)
        assert len(contact.addresses) == 1
        addr = contact.addresses[0]
        assert addr.street == "10 Downing St"
        assert addr.city == "London"
        assert addr.country == "UK"
        assert addr.label == "Home"
        assert addr.primary is True

    def test_organizations_populated(self):
        payload = self._base_payload()
        payload["organizations"] = [
            {"name": "Google LLC", "title": "Staff SWE", "department": "Core"}
        ]
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert len(contact.organizations) == 1
        org = contact.organizations[0]
        assert org.company == "Google LLC"
        assert org.title == "Staff SWE"
        assert org.department == "Core"

    def test_birthdays_populated(self):
        payload = self._base_payload()
        payload["birthdays"] = [{"date": {"year": 1990, "month": 3, "day": 21}}]
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert len(contact.birthdays) == 1
        assert contact.birthdays[0].label == "birthday"
        assert contact.birthdays[0].year == 1990
        assert contact.birthdays[0].month == 3
        assert contact.birthdays[0].day == 21

    def test_events_become_anniversaries(self):
        payload = self._base_payload()
        payload["events"] = [
            {"date": {"year": 2015, "month": 6, "day": 1}, "formattedType": "Anniversary"}
        ]
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert len(contact.anniversaries) == 1
        assert contact.anniversaries[0].label == "Anniversary"
        assert contact.anniversaries[0].year == 2015

    def test_urls_populated(self):
        payload = self._base_payload()
        payload["urls"] = [{"value": "https://johndoe.dev", "formattedType": "Homepage"}]
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert len(contact.urls) == 1
        assert contact.urls[0].value == "https://johndoe.dev"
        assert contact.urls[0].label == "Homepage"

    def test_photos_populated(self):
        payload = self._base_payload()
        payload["photos"] = [
            {"url": "https://lh3.googleusercontent.com/photo.jpg", "metadata": {"primary": True}}
        ]
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert len(contact.photos) == 1
        assert contact.photos[0].url == "https://lh3.googleusercontent.com/photo.jpg"
        assert contact.photos[0].primary is True

    def test_biographies_not_in_canonical_model_no_crash(self):
        """biographies[] has no canonical model; they land in raw only."""
        payload = self._base_payload()
        payload["biographies"] = [{"value": "A famous explorer.", "contentType": "TEXT_PLAIN"}]
        contact = _parse_google_contact(payload)
        assert contact is not None
        # biographies are not in canonical fields — only in raw
        assert "biographies" in contact.raw
        assert not hasattr(contact, "biographies")

    def test_user_defined_not_in_canonical_model_no_crash(self):
        """userDefined[] has no canonical model; they land in raw only."""
        payload = self._base_payload()
        payload["userDefined"] = [{"key": "custom_field", "value": "some_value"}]
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert "userDefined" in contact.raw
        assert not hasattr(contact, "user_defined")

    def test_missing_all_new_fields_defaults_to_empty_lists(self):
        payload = self._base_payload()
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert contact.addresses == []
        assert contact.organizations == []
        assert contact.birthdays == []
        assert contact.anniversaries == []
        assert contact.urls == []
        assert contact.photos == []

    def test_full_rich_contact_all_fields_populated(self):
        """Integration test with a realistic full Google People API payload."""
        payload = {
            "resourceName": "people/c99999",
            "etag": "abc123",
            "metadata": {},
            "names": [
                {
                    "displayName": "Jane Doe",
                    "givenName": "Jane",
                    "familyName": "Doe",
                    "metadata": {"primary": True},
                }
            ],
            "emailAddresses": [
                {"value": "jane@example.com", "type": "work", "metadata": {"primary": True}}
            ],
            "phoneNumbers": [{"value": "+1-555-1234", "type": "mobile"}],
            "addresses": [
                {
                    "streetAddress": "456 Oak Ave",
                    "city": "Portland",
                    "region": "OR",
                    "postalCode": "97201",
                    "country": "US",
                    "formattedType": "Work",
                    "metadata": {"primary": True},
                }
            ],
            "organizations": [{"name": "Acme Corp", "title": "CTO"}],
            "birthdays": [{"date": {"year": 1982, "month": 11, "day": 30}}],
            "events": [
                {"date": {"year": 2012, "month": 5, "day": 18}, "formattedType": "Anniversary"}
            ],
            "urls": [{"value": "https://janedoe.io", "formattedType": "Homepage"}],
            "photos": [
                {
                    "url": "https://lh3.googleusercontent.com/jane.jpg",
                    "metadata": {"primary": True},
                }
            ],
            "biographies": [{"value": "Writer and engineer.", "contentType": "TEXT_PLAIN"}],
            "userDefined": [{"key": "fav_color", "value": "blue"}],
        }
        contact = _parse_google_contact(payload)
        assert contact is not None
        assert contact.display_name == "Jane Doe"
        assert contact.first_name == "Jane"
        assert contact.last_name == "Doe"
        assert len(contact.emails) == 1
        assert len(contact.phones) == 1
        assert len(contact.addresses) == 1
        assert contact.addresses[0].city == "Portland"
        assert len(contact.organizations) == 1
        assert contact.organizations[0].company == "Acme Corp"
        assert len(contact.birthdays) == 1
        assert contact.birthdays[0].year == 1982
        assert len(contact.anniversaries) == 1
        assert contact.anniversaries[0].label == "Anniversary"
        assert len(contact.urls) == 1
        assert contact.urls[0].value == "https://janedoe.io"
        assert len(contact.photos) == 1
        assert contact.photos[0].primary is True
        assert contact.raw == payload
