"""Owner-declared retention horizon for raw OwnTracks location evidence.

Raw OwnTracks fixes are promised a declared horizon (bu-s11n0s.7): 30 days by
default, which the owner may shorten to 1..30 days. This module owns the
canonical typed policy record (``chronicler.location_retention_policy``,
migration ``chronicler_028``) and the declared semantics later expiry work must
honour.

This slice declares and versions the horizon only. Raw expiry is not yet
enforced: deletion must wait for genuine per-row projection coverage, so
:data:`ENFORCEMENT_STATUS` stays ``"not_enforced"`` until that machinery lands
and the status must never claim raw evidence was forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

DEFAULT_RETENTION_DAYS = 30
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 30

#: How a raw fix's age is measured. Mirrors the OwnTracks point adapter's
#: clock-skew rule (``CLOCK_SKEW_THRESHOLD_HOURS``), so a device timestamp far
#: in the future cannot extend retention.
RAW_AGE_BASIS = (
    "device timestamp, or server receipt time when the device timestamp is more "
    "than 4 hours after receipt; a fix is expired only when strictly older than "
    "the horizon"
)

#: Spatial precision the projected summaries keep once their raw fixes expire.
PRECISION_AFTER_FORGETTING = "approximately 150 m grid cells"

#: Raw expiry is declared but not yet enforced (no deletion path exists yet).
ENFORCEMENT_STATUS = "not_enforced"

#: Sources whose raw evidence the horizon governs.
COVERED_SOURCES = (
    "owntracks.points",
    "owntracks.place_cluster",
    "owntracks.ssid_presence",
)


class PolicyVersionConflict(Exception):
    """The caller's ``expected_version`` no longer names the stored policy."""

    def __init__(self, current: LocationRetentionPolicy) -> None:
        super().__init__("location retention policy version changed")
        self.current = current


class PolicyUnavailable(Exception):
    """The canonical policy row is missing; callers must fail closed."""


@dataclass(frozen=True)
class LocationRetentionPolicy:
    days: int
    version: int
    updated_at: datetime
    updated_by: str


_SELECT = "SELECT days, version, updated_at, updated_by FROM location_retention_policy WHERE id = 1"


def _row_to_policy(row: asyncpg.Record) -> LocationRetentionPolicy:
    return LocationRetentionPolicy(
        days=row["days"],
        version=row["version"],
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


async def get_location_retention_policy(pool: asyncpg.Pool) -> LocationRetentionPolicy:
    """Return the canonical policy, or raise :class:`PolicyUnavailable`."""
    row = await pool.fetchrow(_SELECT)
    if row is None:
        raise PolicyUnavailable("location retention policy row is missing")
    return _row_to_policy(row)


async def set_location_retention_policy(
    pool: asyncpg.Pool,
    *,
    days: int,
    expected_version: int,
    actor: str,
) -> LocationRetentionPolicy:
    """Compare-and-set the horizon; increments ``version`` on success.

    Raises :class:`PolicyVersionConflict` when ``expected_version`` is stale and
    :class:`PolicyUnavailable` when the row is missing. ``days`` outside
    ``MIN..MAX`` raises ``ValueError`` before any write.
    """
    if (
        isinstance(days, bool)
        or not isinstance(days, int)
        or not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS
    ):
        raise ValueError(f"days must be an integer in {MIN_RETENTION_DAYS}..{MAX_RETENTION_DAYS}")
    async with pool.acquire() as conn, conn.transaction():
        current = await conn.fetchrow(_SELECT + " FOR UPDATE")
        if current is None:
            raise PolicyUnavailable("location retention policy row is missing")
        if current["version"] != expected_version:
            raise PolicyVersionConflict(_row_to_policy(current))
        row = await conn.fetchrow(
            """
            UPDATE location_retention_policy
            SET days = $1, version = version + 1, updated_at = now(), updated_by = $2
            WHERE id = 1
            RETURNING days, version, updated_at, updated_by
            """,
            days,
            actor,
        )
    return _row_to_policy(row)
