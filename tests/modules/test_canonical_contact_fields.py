"""Tests for the new CanonicalContact sub-models added in spec §4.2.

Covers: ContactAddress, ContactOrganization, ContactDate, ContactUrl,
ContactUsername, ContactPhoto and their integration with CanonicalContact.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactAddress,
    ContactDate,
    ContactOrganization,
    ContactPhoto,
    ContactUrl,
    ContactUsername,
)

pytestmark = pytest.mark.unit


class TestContactAddress:
    def test_all_fields_optional_empty_ok(self):
        addr = ContactAddress()
        assert addr.street is None
        assert addr.city is None
        assert addr.region is None
        assert addr.postal_code is None
        assert addr.country is None
        assert addr.label is None
        assert addr.primary is False

    def test_full_address(self):
        addr = ContactAddress(
            street="123 Main St",
            city="Springfield",
            region="IL",
            postal_code="62701",
            country="US",
            label="home",
            primary=True,
        )
        assert addr.street == "123 Main St"
        assert addr.city == "Springfield"
        assert addr.region == "IL"
        assert addr.postal_code == "62701"
        assert addr.country == "US"
        assert addr.label == "home"
        assert addr.primary is True

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ContactAddress(street="123 Main St", unknown_field="x")  # type: ignore[call-arg]


class TestContactOrganization:
    def test_all_fields_optional(self):
        org = ContactOrganization()
        assert org.company is None
        assert org.title is None
        assert org.department is None

    def test_full_organization(self):
        org = ContactOrganization(
            company="Acme Corp",
            title="Senior Engineer",
            department="Platform",
        )
        assert org.company == "Acme Corp"
        assert org.title == "Senior Engineer"
        assert org.department == "Platform"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ContactOrganization(company="Acme", team="foo")  # type: ignore[call-arg]


class TestContactDate:
    def test_all_fields_optional(self):
        date = ContactDate()
        assert date.year is None
        assert date.month is None
        assert date.day is None
        assert date.label is None

    def test_full_date(self):
        date = ContactDate(year=1990, month=6, day=15, label="birthday")
        assert date.year == 1990
        assert date.month == 6
        assert date.day == 15
        assert date.label == "birthday"

    def test_partial_date_no_year(self):
        """Contacts sometimes have month/day without year (e.g. recurring birthday)."""
        date = ContactDate(month=12, day=25, label="anniversary")
        assert date.year is None
        assert date.month == 12
        assert date.day == 25

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ContactDate(year=2000, era="AD")  # type: ignore[call-arg]


class TestContactUrl:
    def test_value_required(self):
        url = ContactUrl(value="https://example.com")
        assert url.value == "https://example.com"
        assert url.label is None

    def test_with_label(self):
        url = ContactUrl(value="https://github.com/user", label="github")
        assert url.label == "github"

    def test_empty_value_rejected(self):
        with pytest.raises(ValidationError):
            ContactUrl(value="")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ContactUrl(value="https://example.com", rank=1)  # type: ignore[call-arg]


class TestContactUsername:
    def test_value_required(self):
        uname = ContactUsername(value="john_doe")
        assert uname.value == "john_doe"
        assert uname.service is None

    def test_with_service(self):
        uname = ContactUsername(value="@johndoe", service="twitter")
        assert uname.service == "twitter"

    def test_empty_value_rejected(self):
        with pytest.raises(ValidationError):
            ContactUsername(value="")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ContactUsername(value="john", verified=True)  # type: ignore[call-arg]


class TestContactPhoto:
    def test_url_required(self):
        photo = ContactPhoto(url="https://example.com/photo.jpg")
        assert photo.url == "https://example.com/photo.jpg"
        assert photo.primary is False

    def test_primary_flag(self):
        photo = ContactPhoto(url="https://example.com/photo.jpg", primary=True)
        assert photo.primary is True

    def test_empty_url_rejected(self):
        with pytest.raises(ValidationError):
            ContactPhoto(url="")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ContactPhoto(url="https://example.com/p.jpg", width=200)  # type: ignore[call-arg]


class TestCanonicalContactNewFields:
    def test_new_fields_default_to_empty_lists(self):
        contact = CanonicalContact(external_id="people/1")
        assert contact.addresses == []
        assert contact.organizations == []
        assert contact.birthdays == []
        assert contact.anniversaries == []
        assert contact.urls == []
        assert contact.usernames == []
        assert contact.photos == []

    def test_contact_with_all_new_fields(self):
        contact = CanonicalContact(
            external_id="people/42",
            display_name="Jane Smith",
            addresses=[
                ContactAddress(
                    street="456 Oak Ave",
                    city="Portland",
                    region="OR",
                    postal_code="97201",
                    country="US",
                    label="work",
                    primary=True,
                )
            ],
            organizations=[
                ContactOrganization(
                    company="Widgets Inc",
                    title="VP Engineering",
                    department="R&D",
                )
            ],
            birthdays=[ContactDate(year=1985, month=3, day=22, label="birthday")],
            anniversaries=[ContactDate(month=6, day=1, label="anniversary")],
            urls=[ContactUrl(value="https://janesmith.dev", label="homepage")],
            usernames=[ContactUsername(value="janesmith", service="github")],
            photos=[ContactPhoto(url="https://example.com/jane.jpg", primary=True)],
        )
        assert len(contact.addresses) == 1
        assert contact.addresses[0].city == "Portland"
        assert len(contact.organizations) == 1
        assert contact.organizations[0].company == "Widgets Inc"
        assert len(contact.birthdays) == 1
        assert contact.birthdays[0].year == 1985
        assert len(contact.anniversaries) == 1
        assert contact.anniversaries[0].label == "anniversary"
        assert len(contact.urls) == 1
        assert contact.urls[0].value == "https://janesmith.dev"
        assert len(contact.usernames) == 1
        assert contact.usernames[0].service == "github"
        assert len(contact.photos) == 1
        assert contact.photos[0].primary is True

    def test_existing_fields_still_work(self):
        """Regression: no breakage to existing fields."""
        from butlers.modules.contacts.sync import ContactEmail, ContactPhone

        contact = CanonicalContact(
            external_id="people/99",
            display_name="Bob Jones",
            first_name="Bob",
            last_name="Jones",
            emails=[ContactEmail(value="bob@example.com", primary=True)],
            phones=[ContactPhone(value="+15551234567", primary=True)],
            group_memberships=["contactGroups/friends"],
            deleted=False,
        )
        assert contact.display_name == "Bob Jones"
        assert contact.emails[0].value == "bob@example.com"
        assert contact.phones[0].value == "+15551234567"
        assert contact.group_memberships == ["contactGroups/friends"]

    def test_new_fields_exported_from_package(self):
        """Verify all new models are importable from the package __init__."""
        from butlers.modules.contacts import (
            ContactAddress,
            ContactDate,
            ContactOrganization,
            ContactPhoto,
            ContactUrl,
            ContactUsername,
        )

        assert ContactAddress is not None
        assert ContactDate is not None
        assert ContactOrganization is not None
        assert ContactPhoto is not None
        assert ContactUrl is not None
        assert ContactUsername is not None

    def test_canonical_contact_extra_field_rejected(self):
        """extra='forbid' must still block unknown top-level fields."""
        with pytest.raises(ValidationError):
            CanonicalContact(external_id="people/1", unknown_field="bad")  # type: ignore[call-arg]
