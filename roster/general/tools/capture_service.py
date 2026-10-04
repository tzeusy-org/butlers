"""Inactive internal General capture admission, local transaction and verify seam.

RFC0037; REQ-general-capture-001/-002. General is the sole supported target.
The later authenticated ingress owns source verification; no tool/route/worker is
registered by this unit. Unknown lineages remain held, with no retry timer/resend.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

from butlers.core.capture import (
    CaptureReceipt,
    CaptureUnavailable,
    ServiceEpoch,
    VerifiedAuthority,
    canonical_intake,
    load_epoch,
    project_receipt,
)
from butlers.tools.general.items import item_create_versioned
from butlers.tools.general.source_authority import (
    GeneralSourceUnavailable,
    lock_ordinary_item,
    lock_ordinary_parent,
    read_source_version,
    source_digest,
)

TARGETS = {"note": "notes", "fact": "facts", "preference": "preferences"}
SUPPORTED_OWNERS = frozenset({"general"})


class CaptureService:
    """General-role pool plus externally managed nonsecret epoch manifest."""

    def __init__(self, pool: asyncpg.Pool, epoch_path: Path) -> None:
        self.pool = pool
        self.epoch_path = epoch_path

    async def _control(self, conn: asyncpg.Connection, *, dispatch: bool) -> ServiceEpoch:
        epoch = load_epoch(self.epoch_path)
        row = await conn.fetchrow(
            "SELECT * FROM public.capture_service_control WHERE singleton FOR SHARE"
        )
        if (
            await conn.fetchval("SELECT current_user") != "butler_general_rw"
            or row is None
            or row["admitted_epoch"] != epoch.generation
            or row["recovery_required"]
            or not row["dispatch_enabled" if dispatch else "admission_enabled"]
        ):
            raise CaptureUnavailable("recovery_required")
        return epoch

    async def admit(
        self,
        authority: VerifiedAuthority,
        text: str,
        *,
        references: list[dict[str, str]] | None = None,
        mutation_key: str | None = None,
    ) -> CaptureReceipt:
        """Commit held before returning; never invokes classification or a target."""
        canonical, digest = canonical_intake(text, references)
        if (
            not isinstance(authority, VerifiedAuthority)
            or not isinstance(authority.principal_id, uuid.UUID)
            or not isinstance(authority.source_occurrence, uuid.UUID)
            or not isinstance(authority.source_occurred_at, datetime)
            or authority.payload_digest != digest
            or authority.source_occurred_at.tzinfo is None
            or mutation_key is not None
            and (
                not isinstance(mutation_key, str)
                or not mutation_key.strip()
                or len(mutation_key.encode("utf-8")) > 128
            )
        ):
            raise CaptureUnavailable("invalid_authority")
        async with self.pool.acquire() as conn, conn.transaction():
            epoch = await self._control(conn, dispatch=False)
            if (
                authority.service_epoch != epoch.generation
                or authority.source_occurred_at < epoch.not_before
            ):
                raise CaptureUnavailable("recovery_required")
            row = await conn.fetchrow(
                """INSERT INTO public.captures
                    (id, principal_id, source_occurrence, source_occurred_at, service_epoch,
                     mutation_key, canonical_intake, payload_digest)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                    ON CONFLICT (principal_id,source_occurrence,mutation_key) DO NOTHING
                    RETURNING *""",
                uuid.uuid4(),
                authority.principal_id,
                authority.source_occurrence,
                authority.source_occurred_at,
                epoch.generation,
                mutation_key,
                canonical,
                digest,
            )
            if row is None:
                row = await conn.fetchrow(
                    """SELECT * FROM public.captures
                       WHERE principal_id=$1 AND source_occurrence=$2 AND mutation_key=$3
                       FOR SHARE""",
                    authority.principal_id,
                    authority.source_occurrence,
                    mutation_key,
                )
                if row is None or row["payload_digest"] != digest:
                    raise CaptureUnavailable("mutation_conflict")
            receipt = project_receipt(row)
        return receipt

    async def claim(self, capture_id: uuid.UUID, *, owner: str, kind: str) -> CaptureReceipt:
        """Durable fixed next-stage claim, before any effect; unsupported owner refuses."""
        async with self.pool.acquire() as conn, conn.transaction():
            epoch = await self._control(conn, dispatch=True)
            row = await conn.fetchrow(
                "SELECT * FROM public.captures WHERE id=$1 FOR UPDATE", capture_id
            )
            if row is None:
                raise CaptureUnavailable()
            if row["disposition"] != "held":
                return project_receipt(row)
            if row["service_epoch"] != epoch.generation:
                raise CaptureUnavailable("recovery_required")
            if row["operation_id"] is not None:
                return project_receipt(row)  # never recast an existing lineage
            if owner not in SUPPORTED_OWNERS:
                row = await conn.fetchrow(
                    """UPDATE public.captures SET disposition='refused',
                       category='ownership_refused', updated_at=now() WHERE id=$1 RETURNING *""",
                    capture_id,
                )
                return project_receipt(row)
            if kind not in TARGETS:
                row = await conn.fetchrow(
                    """UPDATE public.captures SET category='classification_failed',
                       updated_at=now() WHERE id=$1 RETURNING *""",
                    capture_id,
                )
                return project_receipt(row)
            operation_id = uuid.uuid4()
            await conn.execute(
                """INSERT INTO public.capture_operations
                   (id,capture_id,service_epoch,target_owner,kind,payload_digest)
                   VALUES ($1,$2,$3,'general',$4,$5)""",
                operation_id,
                capture_id,
                epoch.generation,
                kind,
                row["payload_digest"],
            )
            row = await conn.fetchrow(
                """UPDATE public.captures SET operation_id=$2,claim_version=claim_version+1,
                   updated_at=now() WHERE id=$1 RETURNING *""",
                capture_id,
                operation_id,
            )
            return project_receipt(row)

    async def process_one(self, capture_id: uuid.UUID, *, owner: str, kind: str) -> CaptureReceipt:
        """One step; persist a possible commit's uncertainty before propagating."""
        try:
            return await self._process_one(capture_id, owner=owner, kind=kind)
        except (ConnectionError, OSError, asyncpg.PostgresConnectionError, asyncio.CancelledError):
            # All local effects and the routed receipt share one transaction. A
            # committed receipt wins; otherwise keep the same lineage in doubt.
            async def retain_unknown():
                operation_id = await self.pool.fetchval(
                    "SELECT operation_id FROM public.captures WHERE id=$1", capture_id
                )
                if operation_id is not None:
                    await self.mark_unknown(operation_id)

            try:
                await asyncio.shield(retain_unknown())
            except (ConnectionError, OSError, asyncpg.PostgresConnectionError):
                # Database unavailable: the original durable claim remains. A
                # restarted local transaction must lock/read that exact operation,
                # never create a fresh lineage or attempt a remote resend.
                pass
            raise

    async def _process_one(self, capture_id: uuid.UUID, *, owner: str, kind: str) -> CaptureReceipt:
        """One internal step, no loop/classifier. Local target and receipt commit together."""
        claimed = await self.claim(capture_id, owner=owner, kind=kind)
        if claimed.disposition != "held" or claimed.operation_id is None:
            return claimed
        async with self.pool.acquire() as conn, conn.transaction():
            epoch = await self._control(conn, dispatch=True)
            operation = await conn.fetchrow(
                "SELECT * FROM public.capture_operations WHERE id=$1", claimed.operation_id
            )
            if operation["kind"] != kind or owner != "general":
                raise CaptureUnavailable("mutation_conflict")
            # Parent before capture/operation; generic item writers never lock a
            # capture. Resolve only declared fixed vocabulary, never seed names.
            collection_id = await conn.fetchval(
                """SELECT c.id FROM collections AS c
                   JOIN collection_vocabulary AS v ON v.collection_id=c.id
                   WHERE c.name=$1""",
                TARGETS[kind],
            )
            parent = await lock_ordinary_parent(conn, collection_id) if collection_id else None
            row = await conn.fetchrow(
                "SELECT * FROM public.captures WHERE id=$1 FOR UPDATE", capture_id
            )
            operation = await conn.fetchrow(
                "SELECT * FROM public.capture_operations WHERE id=$1 FOR UPDATE",
                claimed.operation_id,
            )
            if row["disposition"] != "held":
                return project_receipt(row)
            if row["service_epoch"] != epoch.generation:
                raise CaptureUnavailable("recovery_required")
            if operation["stage"] == "in_doubt":
                return project_receipt(row)
            if parent is None:
                row = await conn.fetchrow(
                    """UPDATE public.captures SET category='source_unavailable',updated_at=now()
                       WHERE id=$1 RETURNING *""",
                    capture_id,
                )
                return project_receipt(row)
            intake = json.loads(row["canonical_intake"])
            if intake["references"]:
                # Admission can retain normalized handles, but no source-owning
                # attachment eligibility adapter exists in this internal unit.
                # Never relabel retained references as current read authority.
                row = await conn.fetchrow(
                    "UPDATE public.captures SET category='source_unavailable',updated_at=now() "
                    "WHERE id=$1 RETURNING *",
                    capture_id,
                )
                return project_receipt(row)
            await conn.execute(
                "UPDATE public.capture_operations SET stage='in_doubt',updated_at=now() "
                "WHERE id=$1",
                operation["id"],
            )
            try:
                item_id, version = await item_create_versioned(
                    conn,
                    TARGETS[kind],
                    {"text": intake["text"], "references": intake["references"]},
                )
            except GeneralSourceUnavailable:
                # Do not catch/continue inside an aborted transaction. The nested
                # item primitive savepoint has rolled back; outer rollback is safest.
                raise CaptureUnavailable("source_unavailable") from None
            target = {
                "owner": "general",
                "operation_id": str(operation["id"]),
                "item_id": str(item_id),
                "version": version.version,
                "generation": version.eligibility_generation,
                "digest": version.digest,
            }
            await conn.execute(
                """UPDATE public.capture_operations SET stage='routed',receipt=$2,
                   updated_at=now() WHERE id=$1""",
                operation["id"],
                target,
            )
            row = await conn.fetchrow(
                """UPDATE public.captures SET disposition='routed',category='routed',receipt=$2,
                   updated_at=now() WHERE id=$1 RETURNING *""",
                capture_id,
                target,
            )
            result = project_receipt(row)
        return result

    async def mark_unknown(self, operation_id: uuid.UUID) -> None:
        """Persist uncertainty without reopening lineage, rebasing epoch or resending."""
        async with self.pool.acquire() as conn, conn.transaction():
            capture_id = await conn.fetchval(
                "SELECT capture_id FROM public.capture_operations WHERE id=$1", operation_id
            )
            if capture_id is None:
                raise CaptureUnavailable()
            capture = await conn.fetchrow(
                "SELECT * FROM public.captures WHERE id=$1 FOR UPDATE", capture_id
            )
            operation = await conn.fetchrow(
                "SELECT * FROM public.capture_operations WHERE id=$1 FOR UPDATE", operation_id
            )
            if operation["stage"] == "routed" or capture["disposition"] != "held":
                return
            await conn.execute(
                "UPDATE public.capture_operations SET stage='in_doubt',updated_at=now() "
                "WHERE id=$1",
                operation_id,
            )
            await conn.execute(
                """UPDATE public.captures SET category='target_outcome_unknown',updated_at=now()
                   WHERE id=$1""",
                capture_id,
            )

    async def verify(self, operation_id: uuid.UUID) -> dict[str, Any] | None:
        """Exact read-only proof; absent/unknown/stale/private uniformly returns None.

        A stored version's current flag is insufficient: lock the live parent/item
        and recompute its projection digest before returning any source content.
        No caller receipt, item locator or peer schema is accepted.
        """
        async with self.pool.acquire() as conn, conn.transaction():
            operation = await conn.fetchrow(
                "SELECT * FROM public.capture_operations WHERE id=$1", operation_id
            )
            if operation is None or operation["stage"] != "routed":
                return None
            receipt = operation["receipt"]
            if (
                not isinstance(receipt, dict)
                or set(receipt)
                != {"owner", "operation_id", "item_id", "version", "generation", "digest"}
                or receipt["owner"] != "general"
                or receipt["operation_id"] != str(operation_id)
            ):
                return None
            try:
                item_id = uuid.UUID(receipt["item_id"])
                locked = await lock_ordinary_item(conn, item_id)
                if locked is None:
                    return None
                parent, item = locked
                version = await read_source_version(conn, item_id, receipt["version"])
                projection = {
                    "collection_id": str(item["collection_id"]),
                    "data": item["data"],
                    "tags": item["tags"],
                }
                if (
                    version is None
                    or not version.current
                    or version.digest != receipt["digest"]
                    or parent["eligibility_generation"] != receipt["generation"]
                    or source_digest(item_id, version.operation, projection) != receipt["digest"]
                ):
                    return None
                capture = await conn.fetchrow(
                    "SELECT * FROM public.captures WHERE id=$1 FOR SHARE", operation["capture_id"]
                )
                if capture["receipt"] != receipt or capture["operation_id"] != operation_id:
                    return None
                return {**receipt, "content": version.content}
            except (ValueError, TypeError, GeneralSourceUnavailable):
                return None
