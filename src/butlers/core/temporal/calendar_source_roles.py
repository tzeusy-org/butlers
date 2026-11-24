"""Calendar source roles and the one predicate that says whose time an event is.

Calendar discovery registers every calendar the account can see. A partner's shared calendar, a
team calendar, holidays and birthdays all land beside the owner's own, so a reader that does not ask
which calendar an event came from treats someone else's appointment as the owner's (bu-5ii4gj.1,
REQ-module-calendar-034/035).

Roles come from provider facts only (the primary flag, the calendar id shape, the resolved Butlers
calendar), never from a title or display name, and never assume ownership: a calendar the account
can write but that is not primary is ``unclassified``. Pure functions, no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from butlers.core.temporal.calendar_provenance import (
    _metadata_object,
    counts_toward_owner_load,
    is_owner_attending,
    is_quarantined_invitation,
)

ROLES: tuple[str, ...] = (
    "owner",
    "household_member",
    "other_person",
    "team",
    "subscription",
    "holidays",
    "birthdays",
    "butler",
    "unclassified",
)

#: The role a source gets when nothing says otherwise. It fails toward "not the owner's time".
DEFAULT_ROLE = "unclassified"

#: ``role_basis`` values: inferred from provider facts, or set by the owner (sticky).
BASIS_PROVIDER_INFERRED = "provider_inferred"
BASIS_OWNER_CONFIRMED = "owner_confirmed"
ROLE_BASES: tuple[str, ...] = (BASIS_PROVIDER_INFERRED, BASIS_OWNER_CONFIRMED)

#: Roles whose events are the owner's own time without needing an invitation. ``butler`` is the
#: dedicated Butlers calendar and the butler-lane internal sources: a hold the butler placed blocks
#: a free slot, and a generated event stays excluded by ``metadata.butler_generated``.
OWNER_TIME_ROLES: frozenset[str] = frozenset({"owner", "butler"})

#: Roles whose events are someone else's, or ambient: owner time only through an invitation.
_INVITATION_ROLES: frozenset[str] = frozenset(
    {"household_member", "other_person", "team", "unclassified"}
)

#: The provider alias for the account's own primary calendar.
PRIMARY_ALIAS_ID = "primary"
HOLIDAY_CALENDAR_SUFFIX = "#holiday@group.v.calendar.google.com"
BIRTHDAY_CALENDAR_ID = "addressbook#contacts@group.v.calendar.google.com"


def classify_calendar_source(
    *,
    calendar_id: str | None,
    primary: bool = False,
    access_role: str | None = None,
    is_butlers_calendar: bool = False,
) -> tuple[str, str]:
    """The ``(time_role, role_basis)`` a calendarList entry's provider facts support.

    ``access_role`` is accepted so the inputs read as provider facts, but it never promotes a
    calendar: a writable non-primary calendar is ``unclassified``, and so is a read-only one.
    """
    del access_role
    if primary:
        return "owner", BASIS_PROVIDER_INFERRED
    if is_butlers_calendar:
        return "butler", BASIS_PROVIDER_INFERRED
    if isinstance(calendar_id, str):
        if calendar_id.endswith(HOLIDAY_CALENDAR_SUFFIX):
            return "holidays", BASIS_PROVIDER_INFERRED
        if calendar_id == BIRTHDAY_CALENDAR_ID:
            return "birthdays", BASIS_PROVIDER_INFERRED
    return DEFAULT_ROLE, BASIS_PROVIDER_INFERRED


def inferred_role_or_none(*, calendar_id: str | None, butler_specific: bool = False) -> str | None:
    """The role a sync or mutation path can infer for a calendar it names, else ``None``.

    These paths know a calendar id and whether it is the butler-specific calendar, not the
    calendarList facts. The provider's ``primary`` alias id is the owner's primary calendar. An
    uninferable calendar returns ``None`` so the stored role (discovery's, or the owner's) is kept
    rather than overwritten with the default.
    """
    role, _basis = classify_calendar_source(
        calendar_id=calendar_id,
        primary=calendar_id == PRIMARY_ALIAS_ID,
        is_butlers_calendar=butler_specific,
    )
    return None if role == DEFAULT_ROLE else role


def owner_time_roles_sql() -> str:
    """The owner-time roles as a SQL ``IN`` list literal (constants, no user input)."""
    return "(" + ",".join(f"'{role}'" for role in sorted(OWNER_TIME_ROLES)) + ")"


def normalize_role(time_role: Any) -> str:
    """A known role, or ``unclassified`` for an absent or unrecognised value."""
    return time_role if isinstance(time_role, str) and time_role in ROLES else DEFAULT_ROLE


def _has_self_attendee(metadata: Any) -> bool:
    metadata_object = _metadata_object(metadata)
    if metadata_object is None:
        return False
    attendees = metadata_object.get("attendees")
    if not isinstance(attendees, list):
        return False
    return any(isinstance(a, Mapping) and a.get("self") is True for a in attendees)


def asserts_owner_time(time_role: Any, metadata: Any) -> bool:
    """Whether an event on a source with this role is the owner's own time.

    ``owner`` and ``butler`` assert it. ``household_member``, ``other_person``, ``team`` and
    ``unclassified`` assert it only through a ``self`` attendee who has not declined.
    ``holidays``, ``birthdays`` and ``subscription`` never do. An absent or unknown role is
    ``unclassified``. This is the role gate only: ``counts_as_owner_time`` adds the owner's own
    RSVP and transparency rule.
    """
    if is_quarantined_invitation(metadata):
        # An unanswered invitation from an untrusted organizer holds no time, even on the owner's
        # own calendar, where invitations land (REQ-module-calendar-041).
        return False
    role = normalize_role(time_role)
    if role in OWNER_TIME_ROLES:
        return True
    if role in _INVITATION_ROLES:
        return _has_self_attendee(metadata) and is_owner_attending(metadata)
    return False


def counts_as_owner_time(time_role: Any, metadata: Any) -> bool:
    """Busy time the owner will attend: the role gate, then decline and free exclusions."""
    return asserts_owner_time(time_role, metadata) and counts_toward_owner_load(metadata)
