"""Calendar source roles and the one owner-time predicate (bu-5ii4gj.1, REQ-module-calendar-034/035).

Synthetic calendar ids only.
"""

from __future__ import annotations

import pytest

from butlers.core.temporal.calendar_source_roles import (
    DEFAULT_ROLE,
    OWNER_TIME_ROLES,
    ROLES,
    asserts_owner_time,
    classify_calendar_source,
    counts_as_owner_time,
    owner_time_roles_sql,
)

pytestmark = pytest.mark.unit

_SELF = {"self": True, "response_status": "accepted"}
_SELF_DECLINED = {"self": True, "response_status": "declined"}
_GUEST = {"email": "guest@example.test"}


def _meta(*attendees: dict, **extra) -> dict:
    return {"attendees": list(attendees), **extra}


@pytest.mark.parametrize(
    ("kwargs", "role"),
    [
        ({"calendar_id": "owner@example.test", "primary": True, "access_role": "owner"}, "owner"),
        (
            {"calendar_id": "en.sg#holiday@group.v.calendar.google.com", "access_role": "reader"},
            "holidays",
        ),
        (
            {
                "calendar_id": "addressbook#contacts@group.v.calendar.google.com",
                "access_role": "reader",
            },
            "birthdays",
        ),
        (
            {"calendar_id": "butlers-cal@group.calendar.google.com", "is_butlers_calendar": True},
            "butler",
        ),
        # A partner's shared calendar the account may only read is asked about, never assumed.
        ({"calendar_id": "partner@example.test", "access_role": "reader"}, "unclassified"),
        ({"calendar_id": "partner@example.test", "access_role": "freeBusyReader"}, "unclassified"),
        # Writable but not primary: still not assumed to be the owner's.
        (
            {"calendar_id": "team@group.calendar.google.com", "access_role": "writer"},
            "unclassified",
        ),
        ({"calendar_id": "second@example.test", "access_role": "owner"}, "unclassified"),
        # Nothing known at all.
        ({"calendar_id": None}, "unclassified"),
    ],
)
def test_classifier_uses_provider_facts_only(kwargs: dict, role: str) -> None:
    assert classify_calendar_source(**kwargs) == (role, "provider_inferred")


def test_classifier_never_reads_a_name() -> None:
    # A display name that says "mine" or "family" must not matter: the function takes no name.
    with pytest.raises(TypeError):
        classify_calendar_source(calendar_id="x@example.test", display_name="My calendar")  # type: ignore[call-arg]


def test_the_role_set_is_closed_and_the_default_is_fail_safe() -> None:
    assert set(ROLES) == {
        "owner",
        "household_member",
        "other_person",
        "team",
        "subscription",
        "holidays",
        "birthdays",
        "butler",
        "unclassified",
    }
    assert DEFAULT_ROLE == "unclassified"
    assert OWNER_TIME_ROLES == frozenset({"owner", "butler"})
    assert owner_time_roles_sql() == "('butler','owner')"


@pytest.mark.parametrize("role", ["owner", "butler"])
def test_owner_time_roles_assert_without_attendees(role: str) -> None:
    assert asserts_owner_time(role, {}) is True
    assert asserts_owner_time(role, _meta(_GUEST)) is True


@pytest.mark.parametrize("role", ["household_member", "other_person", "team", "unclassified"])
def test_other_people_roles_need_the_owner_to_be_invited(role: str) -> None:
    assert asserts_owner_time(role, {}) is False
    assert asserts_owner_time(role, _meta(_GUEST)) is False
    assert asserts_owner_time(role, _meta(_GUEST, _SELF)) is True
    assert asserts_owner_time(role, _meta(_GUEST, {"self": True})) is True  # no answer yet
    assert asserts_owner_time(role, _meta(_GUEST, _SELF_DECLINED)) is False
    # The raw provider spelling counts too.
    assert asserts_owner_time(role, _meta({"self": True, "responseStatus": "declined"})) is False


@pytest.mark.parametrize("role", ["holidays", "birthdays", "subscription"])
def test_holidays_birthdays_and_subscriptions_are_never_owner_time(role: str) -> None:
    assert asserts_owner_time(role, {}) is False
    assert asserts_owner_time(role, _meta(_SELF)) is False


@pytest.mark.parametrize("role", [None, "", "bogus", "OWNER "])
def test_an_absent_or_unknown_role_is_unclassified(role: str | None) -> None:
    assert asserts_owner_time(role, {}) is False
    assert asserts_owner_time(role, _meta(_SELF)) is True


def test_malformed_metadata_does_not_raise() -> None:
    for metadata in (None, "not json", 7, [], {"attendees": "x"}):
        assert asserts_owner_time("unclassified", metadata) is False
        assert asserts_owner_time("owner", metadata) is True


def test_counts_as_owner_time_composes_rsvp_and_transparency() -> None:
    assert counts_as_owner_time("owner", {}) is True
    assert counts_as_owner_time("owner", _meta(_SELF_DECLINED)) is False
    assert counts_as_owner_time("owner", {"transparency": "transparent"}) is False
    assert counts_as_owner_time("unclassified", {}) is False
    assert counts_as_owner_time("unclassified", _meta(_SELF)) is True
    assert counts_as_owner_time("unclassified", _meta(_SELF, transparency="transparent")) is False
