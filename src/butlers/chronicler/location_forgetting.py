"""Forgotten-row receipts and location evidence tombstones (bu-s11n0s.7 S3).

When raw OwnTracks fixes expire, each Chronicler location point event the expiry removes leaves
a tombstone (``chronicler.location_evidence_tombstones``, chronicler_035) and each run leaves a
receipt (``chronicler.location_forgetting_receipts``). A tombstone holds only opaque
identities: the former point event id, its ``source_ref``, the receipt and the episodes it
was evidence for. No coordinates, title, network name or payload.

:func:`record_forgetting` is the only writer. It is dark: raw expiry, a later slice, calls it
in the same transaction as the deletions and summary coarsening it records. Nothing here
deletes.

:func:`owntracks_point_forgotten` is the resurrection floor: the OwnTracks point projection
asks it before writing a point event, so a replayed webhook, reset cursor or wider horizon
never restores a forgotten point.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.spatial_precision import SPATIAL_PRECISION_M, SPATIAL_SCHEME_REVISION

__all__ = [
    "OWNTRACKS_SOURCE_REF_PREFIX",
    "ForgettingReceipt",
    "ForgottenPointEvent",
    "owntracks_point_forgotten",
    "record_forgetting",
]

OWNTRACKS_SOURCE_REF_PREFIX = "connectors.owntracks_points:"

_INSERT_TOMBSTONE = """
    INSERT INTO location_evidence_tombstones (point_event_id, source_ref, receipt_id, episode_ids)
    VALUES ($1, $2, $3, $4::uuid[])
    ON CONFLICT DO NOTHING
    RETURNING point_event_id
"""

_INSERT_RECEIPT = """
    INSERT INTO location_forgetting_receipts (
        id, policy_version, cutoff, spatial_scheme, spatial_precision_m,
        point_events_forgotten, episodes_coarsened
    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
"""


@dataclass(frozen=True)
class ForgottenPointEvent:
    """One location point event a forgetting run removes."""

    point_event_id: UUID
    source_ref: str
    episode_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.point_event_id, UUID):
            raise ValueError("point_event_id must be a UUID")
        if not (
            isinstance(self.source_ref, str)
            and self.source_ref.startswith(OWNTRACKS_SOURCE_REF_PREFIX)
            and len(self.source_ref) > len(OWNTRACKS_SOURCE_REF_PREFIX)
        ):
            raise ValueError("source_ref must name an OwnTracks raw source")
        if not all(isinstance(episode, UUID) for episode in self.episode_ids):
            raise ValueError("episode_ids must be UUIDs")


@dataclass(frozen=True)
class ForgettingReceipt:
    """What a forgetting run committed."""

    id: UUID
    point_events_forgotten: int


def _positive_int(value: Any, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


async def record_forgetting(
    conn: Any,
    *,
    policy_version: int,
    cutoff: datetime,
    episodes_coarsened: int,
    forgotten: Sequence[ForgottenPointEvent],
    spatial_scheme: str = SPATIAL_SCHEME_REVISION,
    spatial_precision_m: int = SPATIAL_PRECISION_M,
) -> ForgettingReceipt:
    """Write the run's tombstones, then its receipt, in one transaction.

    ``point_events_forgotten`` counts only tombstones this call added: a source already
    tombstoned is skipped, so re-recording the same run never doubles the count.
    """
    _positive_int(policy_version, "policy_version", minimum=1)
    _positive_int(episodes_coarsened, "episodes_coarsened", minimum=0)
    _positive_int(spatial_precision_m, "spatial_precision_m", minimum=1)
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
        raise ValueError("cutoff must be a timezone-aware datetime")

    receipt_id = uuid4()
    added = 0
    async with conn.transaction():
        for event in forgotten:
            inserted = await conn.fetchval(
                _INSERT_TOMBSTONE,
                event.point_event_id,
                event.source_ref,
                receipt_id,
                list(event.episode_ids),
            )
            if inserted is not None:
                added += 1
        await conn.execute(
            _INSERT_RECEIPT,
            receipt_id,
            policy_version,
            cutoff,
            spatial_scheme,
            spatial_precision_m,
            added,
            episodes_coarsened,
        )
    return ForgettingReceipt(id=receipt_id, point_events_forgotten=added)


async def owntracks_point_forgotten(conn: Any, source_ref: str) -> bool:
    """True only when a tombstone records ``source_ref`` as forgotten."""
    found = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM location_evidence_tombstones WHERE source_ref = $1)",
        source_ref,
    )
    return found is True
