"""Chronicler collects holder receipts and makes grants ready (bu-s11n0s.7 S4c-3).

A prepared grant may become ``ready`` (and so be offered to the connector for deletion) only
once every holder of a copy derived from its raw rows has forgotten that copy. The one holder
is Switchboard's ``message_inbox`` (:data:`~.location_retention_grant.REQUIRED_HOLDERS`).

:func:`collect_holder_receipts` takes the prepared grants with rows and no state yet, least
recently asked first, and for each:

* if Switchboard's ``complete`` receipt is already recorded (a run stopped before ``ready``),
  appends ``ready`` without asking again;
* otherwise asks the injected :data:`Forgetter` with the grant id and its rows' logical keys.
  No transaction is held across the call. The reply must be exactly the count-only receipt
  (``outcome``, ``items``, ``forgotten``, ``already_forgotten``, ``absent``, ``blocked``),
  with ``items`` the grant's item count, the four counts adding up to it and ``complete``
  exactly when nothing is blocked. Anything else is :class:`HolderReplyMalformed` and records
  nothing.
* records the receipt; ``complete`` then appends ``ready``. ``blocked`` waits for a later run.

The time of each ask is kept in ``location_retention_holder_asks`` (chronicler_040), written
before the ask, so "least recently asked" holds even for a failed ask. A failed ask (refusal,
outage, malformed reply) does not stop the other grants; once they are done, the first failure
is raised, so the run fails and its failure streak shows it.

In production the forgetter is :func:`relay_forgetter`: the ``location_retention.forget_copies``
named relay, which Switchboard answers itself. The retention job takes the forgetter as an
argument; its hourly schedule supplies this one only while the owner has raw expiry on
(bu-s11n0s.75).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

from butlers.chronicler.location_retention_grant import (
    REQUIRED_HOLDERS,
    append_grant_state,
    record_holder_receipt,
)

#: Asks Switchboard to forget its copies of one grant's rows; returns its count-only receipt.
Forgetter = Callable[[UUID, tuple[str, ...]], Awaitable[Any]]

(HOLDER,) = REQUIRED_HOLDERS
#: Grants asked per run.
DEFAULT_LIMIT = 5
_COUNTS = ("items", "forgotten", "already_forgotten", "absent", "blocked")

_PENDING = """
SELECT g.id, g.item_count,
       EXISTS (
           SELECT 1 FROM location_retention_holder_receipts r
           WHERE r.grant_id = g.id AND r.holder = $1 AND r.outcome = 'complete'
       ) AS complete
FROM location_retention_grants g
WHERE g.item_count > 0
  AND NOT EXISTS (SELECT 1 FROM location_retention_grant_states s WHERE s.grant_id = g.id)
ORDER BY (
    SELECT a.asked_at FROM location_retention_holder_asks a
    WHERE a.grant_id = g.id AND a.holder = $1
) NULLS FIRST, g.prepared_at, g.id
LIMIT $2
"""
# Written before the ask, so a failed ask (which records no receipt) still sends the grant to
# the back of the queue and cannot hold the front of every run.
_ASKED = """
INSERT INTO location_retention_holder_asks (grant_id, holder, asked_at)
VALUES ($1, $2, clock_timestamp())
ON CONFLICT (grant_id, holder) DO UPDATE SET asked_at = EXCLUDED.asked_at
"""
_KEYS = """
SELECT logical_source_key FROM location_retention_grant_items
WHERE grant_id = $1 ORDER BY raw_source_id
"""


class HoldersUnavailable(Exception):
    """Switchboard could not be asked, or refused; nothing was recorded."""


class HolderReplyMalformed(ValueError):
    """Switchboard's reply is not this grant's count-only receipt; nothing was recorded."""


def _checked_reply(reply: Any, item_count: int) -> tuple[str, dict[str, int]]:
    if not isinstance(reply, Mapping) or set(reply) != {"outcome", *_COUNTS}:
        raise HolderReplyMalformed("not a count-only receipt")
    counts = {name: reply[name] for name in _COUNTS}
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in counts.values()
    ):
        raise HolderReplyMalformed("counts must be non-negative integers")
    outcome = reply["outcome"]
    if outcome not in ("complete", "blocked"):
        raise HolderReplyMalformed("unknown outcome")
    if counts["items"] != item_count or counts["items"] != sum(
        counts[name] for name in _COUNTS[1:]
    ):
        raise HolderReplyMalformed("counts do not account for the grant's items")
    if (outcome == "complete") != (counts["blocked"] == 0):
        raise HolderReplyMalformed("outcome disagrees with the blocked count")
    return outcome, counts


async def collect_holder_receipts(
    conn: Any, forget: Forgetter, *, limit: int = DEFAULT_LIMIT
) -> dict[str, int]:
    """Ask for, record and act on holder receipts; returns ``{"ready": n, "blocked": n}``."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    made_ready = blocked = 0
    first_failure: Exception | None = None
    for grant in await conn.fetch(_PENDING, HOLDER, limit):
        grant_id = grant["id"]
        if not grant["complete"]:
            keys = tuple(row["logical_source_key"] for row in await conn.fetch(_KEYS, grant_id))
            await conn.execute(_ASKED, grant_id, HOLDER)
            try:
                outcome, counts = _checked_reply(await forget(grant_id, keys), grant["item_count"])
            except Exception as exc:
                # One grant's failed ask records nothing and does not stop the others.
                first_failure = first_failure or exc
                continue
            await record_holder_receipt(conn, grant_id, HOLDER, outcome=outcome, receipt=counts)
            if outcome == "blocked":
                blocked += 1
                continue
        await append_grant_state(conn, grant_id, "ready")
        made_ready += 1
    if first_failure is not None:
        raise first_failure
    return {"ready": made_ready, "blocked": blocked}


def relay_forgetter(switchboard_url: Callable[[], str | None]) -> Forgetter:
    """Ask through Switchboard's ``location_retention.forget_copies`` relay as Chronicler.

    ``switchboard_url`` is resolved on each call. Any refusal or transport failure is
    :class:`HoldersUnavailable`.
    """
    from butlers.core.service_plane.client import RelayUnavailable, call_relay
    from butlers.core.service_plane.registry import LOCATION_RETENTION_FORGET_COPIES
    from butlers.core.service_plane.relays import RelayRefused

    async def forget(grant_id: UUID, keys: tuple[str, ...]) -> Any:
        url = switchboard_url()
        if not url:
            raise HoldersUnavailable("switchboard_url_unconfigured")
        try:
            return await call_relay(
                LOCATION_RETENTION_FORGET_COPIES,
                {"grant_id": str(grant_id), "logical_source_keys": list(keys)},
                daemon="chronicler",
                switchboard_url=url,
            )
        except (RelayRefused, RelayUnavailable, OSError) as exc:
            # OSError: no projected relay token in this workload.
            raise HoldersUnavailable(type(exc).__name__) from None

    return forget
