"""Endpoint custody egress fence (bu-s11n0s.1 slice 4, REQ-endpoint-custody-holds-006/-007).

A provider send must not start through a held outgoing account or to a held owner target. The
fence asks only the content-blind ``public.custody_target_held(predicate, object)`` read that
core_293 grants to every role; it never reads the hold table and never sees a credential.

It fails closed, independently of the fail-open-on-read permissions matrix: no pool, a lookup
error, a malformed answer or a target that cannot be named all refuse with
``eligibility_unknown``. A missing binding is unknown, not evidence the target is unheld.

Telegram targets are the ``has-handle`` form ``telegram:<chat id>`` the identity reconciler
writes for a Telegram user id; email targets are ``has-email`` addresses. The digest function
normalizes both, so spelling and case do not matter.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Any, Literal

logger = logging.getLogger(__name__)

CustodyRefusalReason = Literal["endpoint_held", "eligibility_unknown"]

_HELD_SQL = """
SELECT COALESCE(bool_or(public.custody_target_held(t.predicate, t.object)), false)
FROM unnest($1::text[], $2::text[]) AS t(predicate, object)
"""


class CustodyEgressRefused(Exception):
    """A provider send was refused before any traffic because of endpoint custody.

    ``reason`` is ``endpoint_held`` (a target is under an active hold; not retryable until a
    protected release) or ``eligibility_unknown`` (custody could not be read; nothing was sent,
    so a retry after recovery is safe). The message names no target and no credential.
    """

    def __init__(self, reason: CustodyRefusalReason) -> None:
        self.reason: CustodyRefusalReason = reason
        self.retryable = reason == "eligibility_unknown"
        detail = (
            "an endpoint in this send is under an owner custody hold"
            if reason == "endpoint_held"
            else "endpoint custody could not be verified"
        )
        super().__init__(f"Delivery refused before provider start: {detail} ({reason}).")


def telegram_chat_target(chat_id: Any) -> tuple[str, str] | None:
    """The custody target for a Telegram chat id, or ``None`` when it names nothing."""
    text = str(chat_id).strip() if chat_id is not None else ""
    if not text:
        return None
    return ("has-handle", f"telegram:{text}")


def email_targets(addresses: Iterable[str | None]) -> list[tuple[str, str]] | None:
    """The ``has-email`` targets of every address, or ``None`` when one names no mailbox."""
    targets: list[tuple[str, str]] = []
    for raw in addresses:
        if not isinstance(raw, str) or not raw.strip():
            return None
        parsed = [addr for _name, addr in getaddresses([raw])]
        if not parsed or any("@" not in addr for addr in parsed):
            return None
        targets.extend(("has-email", addr) for addr in parsed)
    return targets


async def custody_targets_held(pool: Any, targets: Sequence[tuple[str, str]]) -> bool:
    """Whether any target is actively held. Raises ``eligibility_unknown`` when unreadable."""
    if not targets:
        raise CustodyEgressRefused("eligibility_unknown")
    if pool is None:
        logger.error("Custody egress fence has no database pool; refusing send")
        raise CustodyEgressRefused("eligibility_unknown")
    try:
        held = await pool.fetchval(
            _HELD_SQL,
            [predicate for predicate, _object in targets],
            [obj for _predicate, obj in targets],
        )
    except Exception as exc:
        logger.error("Custody egress lookup failed (%s); refusing send", type(exc).__name__)
        raise CustodyEgressRefused("eligibility_unknown") from None
    if held is True:
        return True
    if held is False:
        return False
    logger.error("Custody egress lookup returned no boolean; refusing send")
    raise CustodyEgressRefused("eligibility_unknown")


async def require_custody_clear(pool: Any, targets: Sequence[tuple[str, str]] | None) -> None:
    """Refuse a send unless every target is provably unheld. ``None`` targets are unknown."""
    if targets is None:
        raise CustodyEgressRefused("eligibility_unknown")
    if await custody_targets_held(pool, targets):
        raise CustodyEgressRefused("endpoint_held")


@dataclass(frozen=True)
class OwnerDefaultTarget:
    """The owner-default delivery the fallback chose, or the reason none is eligible."""

    channel: Literal["telegram", "email"] | None
    recipient: str | None
    refusal: CustodyRefusalReason | None = None


async def choose_owner_default_target(
    pool: Any, *, telegram_chat_id: str | None, owner_email: str | None
) -> OwnerDefaultTarget:
    """Pick the first configured owner-default channel whose target is provably unheld.

    Order is the supported notify surface: Telegram, then email. A channel whose custody
    cannot be read is skipped, never chosen. With none eligible the result carries
    ``eligibility_unknown`` when any lookup failed, otherwise ``endpoint_held``.
    """
    unknown = False
    candidates: list[tuple[Literal["telegram", "email"], str | None, Any]] = [
        ("telegram", telegram_chat_id, telegram_chat_target(telegram_chat_id)),
        ("email", owner_email, (email_targets([owner_email]) or [None])[0]),
    ]
    for channel, recipient, target in candidates:
        if not recipient or not recipient.strip():
            continue
        if target is None:
            unknown = True
            continue
        try:
            held = await custody_targets_held(pool, [target])
        except CustodyEgressRefused:
            unknown = True
            continue
        if not held:
            return OwnerDefaultTarget(channel=channel, recipient=recipient.strip())
    return OwnerDefaultTarget(
        channel=None,
        recipient=None,
        refusal="eligibility_unknown" if unknown else "endpoint_held",
    )


__all__ = [
    "CustodyEgressRefused",
    "OwnerDefaultTarget",
    "choose_owner_default_target",
    "custody_targets_held",
    "email_targets",
    "require_custody_clear",
    "telegram_chat_target",
]
