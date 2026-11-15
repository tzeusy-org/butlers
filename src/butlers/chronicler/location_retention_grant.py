"""Prepared retention grants for raw OwnTracks fixes (bu-s11n0s.7, slice 4a).

Raw expiry has two committed stages. Chronicler first prepares a grant: the exact raw rows it
has finished projecting, frozen under the current policy (tables ``chronicler_036``). A later
slice lets the connector delete exactly those rows under its own role. This module only
prepares. It is dark: nothing calls :func:`prepare_retention_grant`, nothing is deleted and
``location_retention.ENFORCEMENT_STATUS`` stays ``not_enforced``.

A raw row is granted only when all of these hold, checked in one statement:

* every covered adapter (:data:`~butlers.chronicler.projection_coverage.COVERED_ADAPTERS`) holds
  a coverage record for it at that adapter's **current** config revision and for the row's
  **current** content digest. A record under another revision (including the ``legacy``
  records chronicler_034 left) or for other content does not count;
* no adapter holds it in an open carry (``location_projection_open_members``);
* its immutable ``retention_at`` is strictly older than the cutoff (``now() - days``, the
  transaction's clock), and its arrival has settled (``recorded_at`` at least
  :data:`~butlers.chronicler.projection_coverage.DISCOVERY_SETTLE` old);
* no earlier grant names it (preparation is irreversible, so a row is granted at most once).

The caller passes the adapters' current config revisions, computed by constructing the
adapters from their configuration (each adapter's ``coverage_revision``). Preparation holds the
policy row (``FOR SHARE``) and every adapter's projection lock in source-name order for the
whole transaction, so no projection run, coverage write or policy change interleaves with it.

Each run writes its items, then its grant row, in that transaction: the grant row is the run's
receipt, written even when it grants nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_retention import PolicyUnavailable
from butlers.chronicler.projection_coverage import (
    COVERED_ADAPTERS,
    DISCOVERY_SETTLE,
    is_digest,
    raw_row_digest_sql,
)
from butlers.chronicler.storage import owntracks_projection_lock_key

__all__ = ["GRANT_LIMIT", "PreparedGrant", "prepare_retention_grant"]

#: Rows one preparation run grants at most; the rest wait for the next run.
GRANT_LIMIT = 500

_LOCK_POLICY = "SELECT days, version FROM location_retention_policy WHERE id = 1 FOR SHARE"
_LOCK_ADAPTER = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"
_CUTOFF = "SELECT now() - make_interval(days => $1)"


def _eligible_sql() -> str:
    return f"""
WITH current_revision AS (
    SELECT * FROM unnest($1::text[], $2::text[]) AS r(adapter_name, config_revision)
)
SELECT p.id, p.idempotency_key, p.retention_at, d.content_digest
FROM connectors.owntracks_points AS p
CROSS JOIN LATERAL (SELECT {raw_row_digest_sql("p")} AS content_digest) AS d
WHERE p.retention_at < $3
  AND p.recorded_at <= now() - $4::interval
  AND NOT EXISTS (
      SELECT 1 FROM location_retention_grant_items AS g WHERE g.raw_source_id = p.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM location_projection_open_members AS m WHERE m.raw_source_id = p.id
  )
  AND NOT EXISTS (
      SELECT 1 FROM current_revision AS r
      WHERE NOT EXISTS (
          SELECT 1 FROM location_projection_coverage AS c
          WHERE c.adapter_name = r.adapter_name
            AND c.raw_source_id = p.id
            AND c.config_revision = r.config_revision
            AND c.content_digest = d.content_digest
      )
  )
ORDER BY p.retention_at, p.id
LIMIT $5
"""


_INSERT_ITEM = (
    "INSERT INTO location_retention_grant_items "
    "(grant_id, raw_source_id, logical_source_key, retention_at, content_digest) "
    "VALUES ($1, $2, $3, $4, $5)"
)
_INSERT_GRANT = (
    "INSERT INTO location_retention_grants (id, policy_version, retention_days, cutoff, "
    "config_revisions, item_count, capped) VALUES ($1, $2, $3, $4, ($5::text)::jsonb, $6, $7)"
)


@dataclass(frozen=True)
class PreparedGrant:
    """What one preparation run committed."""

    id: UUID
    policy_version: int
    retention_days: int
    cutoff: datetime
    item_count: int
    capped: bool


def _checked_revisions(config_revisions: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(config_revisions, Mapping) or set(config_revisions) != set(COVERED_ADAPTERS):
        raise ValueError(f"config_revisions must name exactly {', '.join(COVERED_ADAPTERS)}")
    if not all(is_digest(value) for value in config_revisions.values()):
        raise ValueError("config_revisions must be current config_revision() digests")
    return {adapter: config_revisions[adapter] for adapter in sorted(COVERED_ADAPTERS)}


async def prepare_retention_grant(
    conn: Any,
    *,
    config_revisions: Mapping[str, str],
    limit: int = GRANT_LIMIT,
    settle: timedelta = DISCOVERY_SETTLE,
) -> PreparedGrant:
    """Grant the expired raw rows every covered adapter has finished with, at most ``limit``.

    ``conn`` is a Chronicler connection (not a pool): the run is one transaction. Raises
    ``ValueError`` before touching the database for incomplete or malformed revisions or a bad
    limit, and :class:`PolicyUnavailable` (writing nothing) when the policy row is missing.
    """
    revisions = _checked_revisions(config_revisions)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    async with conn.transaction():
        policy = await conn.fetchrow(_LOCK_POLICY)
        if policy is None:
            raise PolicyUnavailable("location retention policy row is missing")
        for adapter in revisions:  # source-name order
            await conn.execute(_LOCK_ADAPTER, owntracks_projection_lock_key(adapter))
        cutoff = await conn.fetchval(_CUTOFF, policy["days"])
        rows = await conn.fetch(
            _eligible_sql(),
            list(revisions),
            list(revisions.values()),
            cutoff,
            settle,
            limit + 1,
        )
        capped = len(rows) > limit
        granted = rows[:limit]
        grant_id = uuid4()
        if granted:
            await conn.executemany(
                _INSERT_ITEM,
                [
                    (
                        grant_id,
                        row["id"],
                        row["idempotency_key"],
                        row["retention_at"],
                        row["content_digest"],
                    )
                    for row in granted
                ],
            )
        await conn.execute(
            _INSERT_GRANT,
            grant_id,
            policy["version"],
            policy["days"],
            cutoff,
            json.dumps(revisions, sort_keys=True),
            len(granted),
            capped,
        )
    return PreparedGrant(
        id=grant_id,
        policy_version=policy["version"],
        retention_days=policy["days"],
        cutoff=cutoff,
        item_count=len(granted),
        capped=capped,
    )
