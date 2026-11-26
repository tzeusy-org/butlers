"""Reconcile a specialist's domain time claims into its own calendar rows (bu-5ii4gj.13 S2).

``CalendarModule.reconcile_domain_claims`` is called in-process by the producing butler's own job,
inside that butler's daemon. For each active ``block`` or ``reminder`` claim it keeps exactly one
``lane="butler"`` row in the butler's own ``calendar_events`` (on its ``internal_reminders``
source), keyed ``origin_ref = "claim:" + claim_key``. A changed ``record_version`` updates the same
row in place; an unchanged claim writes nothing. When the caller's read of its records was
``complete``, a ``claim:`` row whose claim is absent, settled or cancelled is retracted: set to
``cancelled``, never deleted, so the existing outbound push removes the provider copy and the
history stays. A returning claim revives the same row.

No model is involved, no cross-schema write happens, no MCP tool is added, and nothing is written
to the owner's primary calendar: the outbound push targets the butler's own calendar.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from butlers.core.temporal.domain_claims import (
    CLAIM_ORIGIN_PREFIX,
    ClaimError,
    DomainClaim,
    parse_claim,
)

from .constants import SOURCE_KIND_INTERNAL_REMINDERS

logger = logging.getLogger(__name__)


@dataclass
class ClaimReconcileResult:
    """What one reconcile did, by claim key."""

    created: list[str]
    updated: list[str]
    revived: list[str]
    retracted: list[str]
    unchanged: list[str]
    invalid: list[str]
    #: ``False`` when retraction was skipped (a partial read, or an invalid claim).
    retraction_ran: bool

    @property
    def writes(self) -> int:
        return len(self.created) + len(self.updated) + len(self.revived) + len(self.retracted)


def _claim_metadata(claim: DomainClaim) -> dict[str, Any]:
    return {
        "claim": {
            "claim_key": claim.claim_key,
            "record_version": claim.record_version,
            "kind": claim.kind,
            "presence": claim.presence,
            "priority": claim.priority,
            "owner_action": claim.owner_action,
            "reminder_offsets": list(claim.reminder_offsets),
            "door": claim.door,
        }
    }


def _existing_version(metadata: Any) -> tuple[str | None, str | None]:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            return None, None
    claim = metadata.get("claim") if isinstance(metadata, Mapping) else None
    if not isinstance(claim, Mapping):
        return None, None
    return claim.get("record_version"), claim.get("presence")


class DomainClaimsMixin:
    """``reconcile_domain_claims`` for :class:`CalendarModule`."""

    async def reconcile_domain_claims(
        self,
        source_butler: str,
        claims: Iterable[Mapping[str, Any]],
        complete: bool,
    ) -> ClaimReconcileResult:
        """Make the butler's ``claim:`` rows match ``claims``; see the module docstring.

        ``claims`` is the producer's whole set for its window. Raises :class:`ClaimError` when
        ``source_butler`` is not this module's own butler or a claim key repeats. A malformed
        claim is skipped and logged, and it also withholds retraction (its record is unknown).
        """
        own = self._resolve_effective_butler_name()  # type: ignore[attr-defined]
        if source_butler != own:
            raise ClaimError(f"{source_butler!r} cannot reconcile claims for butler {own!r}")

        parsed: dict[str, DomainClaim] = {}
        invalid: list[str] = []
        for raw in claims:
            try:
                claim = parse_claim(raw, source_butler=source_butler)
            except ClaimError as exc:
                key = raw.get("claim_key") if isinstance(raw, Mapping) else None
                invalid.append(str(key))
                logger.warning("calendar claim skipped (%s): %s", key, exc)
                continue
            if claim.claim_key in parsed:
                raise ClaimError(f"claim_key repeated in one reconcile: {claim.claim_key!r}")
            parsed[claim.claim_key] = claim

        retract = complete and not invalid
        result = ClaimReconcileResult([], [], [], [], [], invalid, retract)

        pool = getattr(self._db, "pool", None) if self._db is not None else None  # type: ignore[attr-defined]
        if pool is None:
            raise RuntimeError("Database pool is not available")
        if not await self._projection_tables_available():  # type: ignore[attr-defined]
            logger.warning("calendar claims not reconciled: projection tables are absent")
            result.retraction_ran = False
            return result
        source_id = await self._resolve_action_source_id(  # type: ignore[attr-defined]
            source_kind=SOURCE_KIND_INTERNAL_REMINDERS, lane="butler"
        )
        if source_id is None:
            raise RuntimeError("Could not resolve internal_reminders calendar source")

        async with pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, origin_ref, title, timezone, starts_at, ends_at, all_day, status,
                       metadata
                FROM calendar_events
                -- quarantine-exempt: the butler's own claim rows, never a provider invitation
                WHERE source_id = $1 AND origin_ref LIKE $2
                FOR UPDATE
                """,
                source_id,
                CLAIM_ORIGIN_PREFIX + "%",
            )
            existing = {r["origin_ref"][len(CLAIM_ORIGIN_PREFIX) :]: r for r in rows}

            for key, claim in parsed.items():
                if not claim.wants_row:
                    continue
                span = claim.span
                row = existing.get(key)
                metadata = json.dumps(_claim_metadata(claim))
                if row is None:
                    await conn.execute(
                        """
                        INSERT INTO calendar_events (
                            source_id, origin_ref, title, timezone, starts_at, ends_at, all_day,
                            status, visibility, source_butler, metadata
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, 'confirmed', 'default', $8,
                                ($9::text)::jsonb)
                        """,
                        source_id,
                        CLAIM_ORIGIN_PREFIX + key,
                        claim.label,
                        span.timezone,
                        span.start,
                        span.end,
                        span.all_day,
                        own,
                        metadata,
                    )
                    result.created.append(key)
                    continue
                version, presence = _existing_version(row["metadata"])
                revived = row["status"] == "cancelled"
                if (
                    not revived
                    and version == claim.record_version
                    and presence == claim.presence
                    and row["title"] == claim.label
                    and row["timezone"] == span.timezone
                    and row["starts_at"] == span.start
                    and row["ends_at"] == span.end
                    and row["all_day"] == span.all_day
                ):
                    result.unchanged.append(key)
                    continue
                # ``||`` keeps everything else on the row: the provider mirror id and any
                # owner override written by the butler-event tools.
                await conn.execute(
                    """
                    UPDATE calendar_events
                    SET title = $2, timezone = $3, starts_at = $4, ends_at = $5, all_day = $6,
                        status = 'confirmed',
                        metadata = COALESCE(metadata, '{}'::jsonb) || ($7::text)::jsonb,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    row["id"],
                    claim.label,
                    span.timezone,
                    span.start,
                    span.end,
                    span.all_day,
                    metadata,
                )
                (result.revived if revived else result.updated).append(key)

            if retract:
                wanted = {k for k, c in parsed.items() if c.wants_row}
                gone = [
                    r["id"]
                    for k, r in existing.items()
                    if k not in wanted and r["status"] != "cancelled"
                ]
                if gone:
                    await conn.execute(
                        "UPDATE calendar_events SET status = 'cancelled', updated_at = now() "
                        "WHERE id = ANY($1::uuid[])",
                        gone,
                    )
                result.retracted.extend(
                    k for k, r in existing.items() if k not in wanted and r["status"] != "cancelled"
                )
        return result
