"""Custody fence for Google-sent calendar mail (bu-s11n0s.94, REQ-endpoint-custody-holds-007).

A calendar write with ``sendUpdates`` ``all`` or ``externalOnly`` makes Google email the event's
guests from the owner's account: an egress that never passes Messenger. Before such a write
starts, every address Google could mail (the organizer and the guests) must be provably unheld.
A held address refuses the whole write with ``endpoint_held``; custody that cannot be read, or an
address that names no mailbox, refuses with ``eligibility_unknown``. Nothing is downgraded to
``none`` silently: the caller asked for the mail, so the write is refused rather than retargeted.

A write that sends no mail (``none``, or the flag omitted, Google's silent default) is not
fenced: it reaches no endpoint.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from butlers.core.custody_egress import email_targets, require_custody_clear

#: ``sendUpdates`` values under which Google emails guests.
MAILING_SEND_UPDATES = frozenset({"all", "externalOnly"})


def sends_mail(send_updates: str | None) -> bool:
    """Whether a write with this ``sendUpdates`` value makes Google email anyone."""
    return isinstance(send_updates, str) and send_updates.strip() in MAILING_SEND_UPDATES


def google_resource_recipients(resource: Any) -> list[str | None]:
    """The organizer and guest addresses a raw Google event resource names."""
    if not isinstance(resource, dict):
        return []
    recipients: list[str | None] = []
    organizer = resource.get("organizer")
    if isinstance(organizer, dict) and organizer.get("email"):
        recipients.append(organizer["email"])
    for attendee in resource.get("attendees") or []:
        recipients.append(attendee.get("email") if isinstance(attendee, dict) else attendee)
    return recipients


async def require_calendar_mail_custody_clear(
    pool: Any, send_updates: str | None, recipients: Iterable[str | None]
) -> None:
    """Refuse a mail-sending calendar write unless every recipient is provably unheld.

    Raises :class:`~butlers.core.custody_egress.CustodyEgressRefused` before provider start.
    A write that sends no mail, or names no recipient, passes untouched.
    """
    if not sends_mail(send_updates):
        return
    addresses = list(dict.fromkeys(recipients))
    if not addresses:
        return
    await require_custody_clear(pool, email_targets(addresses))
