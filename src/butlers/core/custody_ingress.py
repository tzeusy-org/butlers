"""Endpoint custody at ingress (bu-s11n0s.1 slice 3a, REQ-endpoint-custody-holds-005).

A sender that resolves to the owner through a held endpoint, or through one whose custody cannot
be read, keeps its canonical attribution (the owner entity) but does not carry current owner
authority. Callers that would grant owner treatment to a directly resolved owner ask
:func:`owner_source_custody_reason` first; ``endpoint_held`` means "treat as a report".

The check asks only the content-blind ``public.custody_target_held`` read (core_293), over the
same typed owner fact candidates the owner-channel definer uses, and fails closed: no pool, a
lookup error, or a channel whose owner facts cannot be named all yield ``endpoint_held``.

This is a pre-cutover security fix in Switchboard's frozen classify-and-route path (RFC 0038
"Freeze" admits security fixes). At cutover owner origin comes from owner-channel authorization,
which ``resolve_owner_triple`` already refuses for a held endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from butlers.core.custody_egress import (
    CustodyEgressRefused,
    custody_targets_held,
    phone_targets,
)

logger = logging.getLogger(__name__)

ENDPOINT_HELD: Literal["endpoint_held"] = "endpoint_held"


def owner_source_targets(channel_type: str, channel_value: str) -> list[tuple[str, str]] | None:
    """Custody targets for an owner-looking sender, or ``None`` when none can be named."""
    from butlers.identity import owner_channel_authorization_candidates

    targets: list[tuple[str, str]] = []
    for candidate in owner_channel_authorization_candidates(channel_type, channel_value):
        predicate, _, value = candidate.partition(":")
        if predicate == "phone-digits":
            phones = phone_targets(value)
            if phones is None:
                return None
            targets.extend(phones)
        elif predicate in ("has-email", "has-handle") and value:
            targets.append((predicate, value))
        else:
            return None
    return list(dict.fromkeys(targets)) or None


async def owner_source_custody_reason(
    pool: Any, channel_type: str | None, channel_value: str | None
) -> Literal["endpoint_held"] | None:
    """``endpoint_held`` unless the owner-looking source endpoint is provably unheld."""
    targets = (
        owner_source_targets(channel_type, str(channel_value))
        if channel_type and channel_value
        else None
    )
    if targets is None:
        return ENDPOINT_HELD
    try:
        held = await custody_targets_held(pool, targets)
    except CustodyEgressRefused:
        logger.warning("custody_ingress: owner source custody unreadable; demoting to report")
        return ENDPOINT_HELD
    return ENDPOINT_HELD if held else None


__all__ = ["ENDPOINT_HELD", "owner_source_custody_reason", "owner_source_targets"]
