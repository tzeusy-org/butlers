"""One deterministic trust verdict per invitation organizer (REQ-module-calendar-039).

The function is pure and takes only server-verified provider fields plus two owner-owned lookups
passed in as values. It deliberately has no parameter for a title, description, location or any
other free text: third-party text can never move an invitation into a more trusted verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

VERDICT_VERSION: Final = 1
SWEEP_BATCH: Final = 200  # events stamped per sync pass

Verdict = Literal["owner", "known_entity", "known_domain", "first_contact", "blocked", "unknown"]
IdentityClass = Literal["known_entity", "known_domain", "none"]
LureBadge = Literal[
    "hidden_guest_list_large",
    "organizer_domain_mismatch_conference",
    "external_link_present",
    "from_gmail",
    "mass_invite",
]

# Verdicts whose needsAction invitations are quarantined (REQ-module-calendar-041).
QUARANTINE_VERDICTS: Final = frozenset({"first_contact", "blocked", "unknown"})

HIDDEN_GUEST_LIST_LARGE_MIN: Final = 10
MASS_INVITE_MIN: Final = 25

# Mainstream conferencing hosts are not a mismatch signal on their own.
_COMMON_CONFERENCE_HOSTS: Final = frozenset(
    {"meet.google.com", "zoom.us", "teams.microsoft.com", "webex.com", "whereby.com"}
)


@dataclass(frozen=True, slots=True)
class OrganizerVerdict:
    verdict: Verdict
    lure_badges: tuple[LureBadge, ...]


def _address(value: str | None) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


def _domain(address: str) -> str:
    return address.rpartition("@")[2]


def _host_is_common(host: str) -> bool:
    return any(host == known or host.endswith("." + known) for known in _COMMON_CONFERENCE_HOSTS)


def lure_badges(
    *,
    organizer_email: str | None,
    attendee_count: int,
    guests_hidden: bool,
    event_type: str | None,
    conference_host: str | None,
    external_link_count: int,
) -> tuple[LureBadge, ...]:
    """Closed lure badges from server-verified fields only."""
    badges: list[LureBadge] = []
    if guests_hidden and attendee_count >= HIDDEN_GUEST_LIST_LARGE_MIN:
        badges.append("hidden_guest_list_large")
    host = _address(conference_host)
    domain = _domain(_address(organizer_email))
    if host and domain and not _host_is_common(host) and not host.endswith(domain):
        badges.append("organizer_domain_mismatch_conference")
    if external_link_count > 0:
        badges.append("external_link_present")
    if event_type == "fromGmail":
        badges.append("from_gmail")
    if attendee_count >= MASS_INVITE_MIN:
        badges.append("mass_invite")
    return tuple(badges)


def organizer_verdict(
    *,
    organizer_email: str | None,
    own_email: str | None,
    identity_class: IdentityClass | None,
    policy_blocked: bool | None,
    attendee_count: int,
    guests_hidden: bool,
    event_type: str | None,
    conference_host: str | None,
    external_link_count: int,
) -> OrganizerVerdict:
    """Return the organizer's verdict and lure badges.

    ``identity_class`` and ``policy_blocked`` are the results of the owner-owned lookups; ``None``
    means the lookup could not be read, which yields ``unknown`` rather than any ``known_*`` value.
    """
    badges = lure_badges(
        organizer_email=organizer_email,
        attendee_count=attendee_count,
        guests_hidden=guests_hidden,
        event_type=event_type,
        conference_host=conference_host,
        external_link_count=external_link_count,
    )
    organizer = _address(organizer_email)
    if not organizer:
        return OrganizerVerdict("unknown", badges)
    own = _address(own_email)
    if own and organizer == own:
        return OrganizerVerdict("owner", badges)
    if identity_class is None or policy_blocked is None:
        return OrganizerVerdict("unknown", badges)
    if policy_blocked:
        return OrganizerVerdict("blocked", badges)
    if identity_class == "known_entity":
        return OrganizerVerdict("known_entity", badges)
    if identity_class == "known_domain":
        return OrganizerVerdict("known_domain", badges)
    return OrganizerVerdict("first_contact", badges)
