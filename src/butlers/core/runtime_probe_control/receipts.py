"""Switchboard-only durable replay receipts for runtime-probe capabilities.

REQ-database-security-008: after a capability verifies, Switchboard inserts and
commits one receipt *before* catalog lookup, runtime launch, or verification
persistence.  The unique ``(audience, nonce_digest)`` constraint is what makes
two concurrent uses of one capability produce exactly one probe, and what makes
a replay still fail after a Switchboard restart.

A receipt carries only the fixed audience, the SHA-256 digest of the nonce, the
key id, the capability expiry, and the receipt timestamp.  The raw nonce and
the signature are never stored, so the table cannot be replayed back into a
valid capability.

Only ``butler_switchboard_rw`` holds ``SELECT``/``INSERT``/``DELETE`` here, and
``core_201`` forces a row-security policy on top so even the table owner --- the
shared migration/dashboard login --- is fenced out of the rows.  See that
migration for the boundary this repository assumes.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from butlers.core.runtime_probe_control.keys import CONTROL_AUDIENCE

RECEIPTS_TABLE: Final = "public.runtime_probe_control_receipts"

#: A receipt is retained through ``exp`` plus the accepted clock skew, so a
#: cleanup can never reopen a replay window that is still live.
RECEIPT_RETENTION_SKEW: Final = timedelta(seconds=5)


def nonce_digest(nonce: bytes) -> bytes:
    """The stored SHA-256 digest of a capability's 256-bit nonce."""
    if len(nonce) != 32:
        raise ValueError("runtime-probe control nonce must be 32 bytes")
    return hashlib.sha256(nonce).digest()


class RuntimeProbeControlReceipts:
    """Narrow persistence surface for the replay receipt.

    Every method runs outside an explicit transaction so the insert commits on
    its own, which is the ordering the requirement asks for: the receipt is
    durable before any lookup, launch, or persistence can observe it.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def claim(self, *, nonce: bytes, kid: str, expires_at: datetime) -> bool:
        """Insert and commit one receipt; ``False`` means the nonce was replayed.

        Under concurrency the loser blocks on the winner's insert and then
        observes the conflict, so exactly one caller sees ``True``.
        """
        claimed = await self._pool.fetchval(
            f"""
            INSERT INTO {RECEIPTS_TABLE} (audience, nonce_digest, kid, capability_exp)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (audience, nonce_digest) DO NOTHING
            RETURNING true
            """,
            CONTROL_AUDIENCE,
            nonce_digest(nonce),
            kid,
            expires_at,
        )
        return bool(claimed)

    async def is_consumed(self, *, nonce: bytes) -> bool:
        """Whether this nonce already has a receipt (replay inspection only)."""
        found = await self._pool.fetchval(
            f"SELECT true FROM {RECEIPTS_TABLE} WHERE audience = $1 AND nonce_digest = $2",
            CONTROL_AUDIENCE,
            nonce_digest(nonce),
        )
        return bool(found)

    async def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete only receipts whose retention bound has already elapsed.

        The database enforces the same bound with a BEFORE DELETE trigger, so a
        cleanup that got this predicate wrong would fail loudly rather than
        quietly reopen a replay window.
        """
        cutoff = (now or datetime.now(UTC)) - RECEIPT_RETENTION_SKEW
        status = await self._pool.execute(
            f"DELETE FROM {RECEIPTS_TABLE} WHERE capability_exp < $1",
            cutoff,
        )
        return int(str(status).rsplit(" ", 1)[-1])
