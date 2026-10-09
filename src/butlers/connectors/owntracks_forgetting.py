"""Connector-owned raw forgetting after registered Chronicler readiness.

These private functions consume a real source read, not model/caller grants.
The native worker must obtain the grant outside this transaction. A digest
binds the frozen set; it is neither authentication nor a fresh policy vote.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from butlers.connectors.mcp_client import CachedMCPClient
from butlers.location_retention import content_digest, utc

DigestHex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FrozenRaw(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    raw_id: UUID
    source_revision: StrictInt = Field(gt=0)
    logical_source_digest: DigestHex
    content_digest: DigestHex
    retention_at: AwareDatetime
    accepted_request_id: UUID
    accepted_payload_digest: DigestHex
    accepted_normalized_digest: DigestHex


def frozen_manifest(
    decision_id: UUID, policy_version: int, cutoff: datetime, rows: Sequence[FrozenRaw]
) -> bytes:
    """Exact immutable metadata binding, with no ready or identity inference."""
    return content_digest(
        {
            "decision_id": str(decision_id),
            "policy_version": policy_version,
            "cutoff": utc(cutoff).isoformat(),
            "rows": [
                row.model_dump(mode="json") for row in sorted(rows, key=lambda row: str(row.raw_id))
            ],
        }
    )


class ReadyGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    grant_id: UUID
    decision_id: UUID
    batch_id: UUID
    policy_version: StrictInt = Field(gt=0)
    cutoff: AwareDatetime
    manifest_digest: DigestHex
    lease_version: StrictInt = Field(gt=0)
    lease_until: AwareDatetime
    rows: tuple[FrozenRaw, ...] = Field(min_length=1, max_length=256)

    def check_manifest(self) -> None:
        identities = [(row.raw_id, row.source_revision) for row in self.rows]
        if len(set(identities)) != len(identities) or len({row.raw_id for row in self.rows}) != len(
            self.rows
        ):
            raise ValueError("duplicate raw identity in ready source")
        if len({row.accepted_request_id for row in self.rows}) != len(self.rows):
            raise ValueError("duplicate accepted source in ready source")
        digest = frozen_manifest(
            self.decision_id, self.policy_version, self.cutoff, self.rows
        ).hex()
        if digest != self.manifest_digest:
            raise ValueError("ready source manifest changed")


class ForgettingRefusedError(RuntimeError):
    """Closed reason: source/role/lease mismatch; no raw exception text."""


class RegisteredRetentionSource:
    """Constructor-owned Switchboard registry selects the fixed Chronicler.

    No model request supplies an endpoint, actor, row set or ready verdict.
    Each cycle checks the real registered source's current eligibility. These
    are trusted daemon/MCP boundaries, not same-UID hostile isolation.
    """

    def __init__(self, switchboard: CachedMCPClient) -> None:
        self._switchboard = switchboard
        self._client: CachedMCPClient | None = None
        self._endpoint: str | None = None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._endpoint = None

    async def forget_pending(self, pool: asyncpg.Pool, *, buffers: Sequence = ()) -> None:
        try:
            registry = await self._switchboard.call_tool("list_butlers", {"routable_only": True})
            if not isinstance(registry, list) or len(registry) > 256:
                raise ValueError
            sources = [
                row
                for row in registry
                if isinstance(row, dict)
                and row.get("name") == "chronicler"
                and row.get("agent_type") == "butler"
                and row.get("eligibility_state") == "active"
            ]
            if len(sources) != 1 or not isinstance(sources[0].get("endpoint_url"), str):
                raise ValueError
            endpoint = sources[0]["endpoint_url"]
            url = urlsplit(endpoint)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                raise ValueError
            if endpoint != self._endpoint:
                await self.aclose()
                self._client = CachedMCPClient(
                    endpoint_url=endpoint, client_name="owntracks-retention"
                )
                self._endpoint = endpoint
            from butlers.connectors.owntracks_copy_retention import (
                FilteredCopyPlan,
                prepare_filtered_copies,
            )

            copies = await self._client.call_tool(
                "chronicler_location_retention_batches", {"phase": "copies"}
            )
            if not isinstance(copies, list) or len(copies) > 8:
                raise ValueError
            for wire in copies:
                await prepare_filtered_copies(pool, FilteredCopyPlan.model_validate(wire), buffers)
            response = await self._client.call_tool("chronicler_location_retention_batches", {})
            if not isinstance(response, list) or len(response) > 8:
                raise ValueError
            grants = [ReadyGrant.model_validate(item) for item in response]
            for grant in grants:
                await forget_ready_batch(pool, grant)
        except Exception:
            # Cached transport errors may contain URLs and upstream strings.
            # They must never enter the connector's health/retention log path.
            raise ForgettingRefusedError("retention_source_unavailable") from None


async def read_batch(pool: asyncpg.Pool, batch_id: UUID) -> dict | None:
    """Separately acquired complete immutable ledger after lost commit ACK.

    A header alone cannot certify all selected dispositions. Both relations
    are append-only and commit together; this read still conveys observation,
    never incoming source authority.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM connectors.owntracks_retention_batches WHERE batch_id=$1", batch_id
        )
        if row is None:
            return None
        members = await conn.fetch(
            "SELECT * FROM connectors.owntracks_retention_batch_rows WHERE batch_id=$1 "
            "ORDER BY raw_id,source_revision",
            batch_id,
        )
    return {**dict(row), "rows": [dict(member) for member in members]}


def _receipt_matches(committed: dict, grant: ReadyGrant) -> bool:
    members = committed["rows"]
    wanted = {
        (row.raw_id, row.source_revision, bytes.fromhex(row.logical_source_digest))
        for row in grant.rows
    }
    observed = {
        (row["raw_id"], row["source_revision"], row["logical_source_digest"]) for row in members
    }
    deleted = sum(row["disposition"] == "deleted" for row in members)
    already = sum(row["disposition"] == "already_forgotten" for row in members)
    return (
        committed["batch_id"] == grant.batch_id
        and committed["decision_id"] == grant.decision_id
        and committed["grant_id"] == grant.grant_id
        and committed["policy_version"] == grant.policy_version
        and committed["cutoff"] == grant.cutoff
        and committed["manifest_digest"] == bytes.fromhex(grant.manifest_digest)
        and observed == wanted
        and len(members) == len(wanted)
        and all(row["batch_id"] == grant.batch_id for row in members)
        and deleted + already == len(members)
        and committed["deleted_count"] == deleted
        and committed["already_forgotten_count"] == already
    )


def _same_raw(row: asyncpg.Record, expected: FrozenRaw) -> bool:
    return (
        row["source_revision"] == expected.source_revision
        and row["logical_source_digest"] == bytes.fromhex(expected.logical_source_digest)
        and row["content_digest"] == bytes.fromhex(expected.content_digest)
        and row["retention_at"] == expected.retention_at
        and row["accepted_request_id"] == expected.accepted_request_id
        and row["accepted_payload_digest"] == bytes.fromhex(expected.accepted_payload_digest)
        and row["accepted_normalized_digest"] == bytes.fromhex(expected.accepted_normalized_digest)
    )


async def forget_ready_batch(pool: asyncpg.Pool, grant: ReadyGrant) -> dict:
    """No network under locks; tombstones, DELETE and receipt commit together.

    Caller placement is native-only and source verification remains mandatory.
    SQL actions use the existing connector_writer; installation does not widen
    role membership. This function never chooses a new raw set or cutoff.
    """
    grant.check_manifest()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "connector_writer":
                raise ForgettingRefusedError("writer_identity_mismatch")
            await conn.execute("SET LOCAL lock_timeout='2s'")
            await conn.execute("SET LOCAL statement_timeout='5s'")
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", "owntracks:retention:source"
            )
            existing = await conn.fetchrow(
                "SELECT * FROM connectors.owntracks_retention_batches WHERE batch_id=$1",
                grant.batch_id,
            )
            if existing is not None:
                if (
                    existing["decision_id"] != grant.decision_id
                    or existing["grant_id"] != grant.grant_id
                    or existing["policy_version"] != grant.policy_version
                    or existing["cutoff"] != grant.cutoff
                    or existing["manifest_digest"] != bytes.fromhex(grant.manifest_digest)
                ):
                    raise ForgettingRefusedError("receipt_identity_mismatch")
            else:
                rows = await conn.fetch(
                    """SELECT * FROM connectors.owntracks_points WHERE id=ANY($1::uuid[])
                       ORDER BY id FOR UPDATE""",
                    [row.raw_id for row in grant.rows],
                )
                actual = {row["id"]: row for row in rows}
                deleting: list[UUID] = []
                dispositions = []
                for expected in sorted(grant.rows, key=lambda row: str(row.raw_id)):
                    source = actual.get(expected.raw_id)
                    if source is not None:
                        if (
                            not _same_raw(source, expected)
                            or source["retention_at"] >= grant.cutoff
                        ):
                            raise ForgettingRefusedError("source_revision_mismatch")
                        from butlers.connectors.owntracks_input_copies import require_inputs_ended

                        try:
                            await require_inputs_ended(
                                conn,
                                source["source_input_generation"],
                                source["logical_source_digest"],
                                source["content_digest"],
                            )
                        except ValueError:
                            raise ForgettingRefusedError(
                                "source_input_cohort_unavailable"
                            ) from None
                        deleting.append(expected.raw_id)
                        await conn.execute(
                            """INSERT INTO connectors.owntracks_retention_tombstones
                               (logical_source_digest,raw_id,source_revision,retention_at,
                                decision_id,batch_id) VALUES($1,$2,$3,$4,$5,$6)""",
                            bytes.fromhex(expected.logical_source_digest),
                            expected.raw_id,
                            expected.source_revision,
                            expected.retention_at,
                            grant.decision_id,
                            grant.batch_id,
                        )
                        dispositions.append((expected, "deleted"))
                    else:
                        tombstone = await conn.fetchrow(
                            """SELECT * FROM connectors.owntracks_retention_tombstones
                               WHERE logical_source_digest=$1""",
                            bytes.fromhex(expected.logical_source_digest),
                        )
                        if (
                            tombstone is None
                            or tombstone["raw_id"] != expected.raw_id
                            or tombstone["source_revision"] != expected.source_revision
                            or tombstone["retention_at"] != expected.retention_at
                            or tombstone["decision_id"] != grant.decision_id
                        ):
                            raise ForgettingRefusedError("missing_committed_tombstone")
                        dispositions.append((expected, "already_forgotten"))
                wall_clock: datetime = await conn.fetchval("SELECT clock_timestamp()")
                if (
                    not wall_clock < grant.lease_until
                    or (grant.lease_until - wall_clock).total_seconds() > 120
                ):
                    raise ForgettingRefusedError("claim_lease_expired")
                removed = await conn.fetch(
                    """DELETE FROM connectors.owntracks_points WHERE id=ANY($1::uuid[])
                       AND clock_timestamp() < $2 RETURNING id""",
                    deleting,
                    grant.lease_until,
                )
                if {row["id"] for row in removed} != set(deleting):
                    raise ForgettingRefusedError("claim_lease_expired")
                await conn.execute(
                    """INSERT INTO connectors.owntracks_retention_batches
                       (batch_id,decision_id,grant_id,manifest_digest,policy_version,cutoff,
                        deleted_count,already_forgotten_count)
                       VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
                    grant.batch_id,
                    grant.decision_id,
                    grant.grant_id,
                    bytes.fromhex(grant.manifest_digest),
                    grant.policy_version,
                    grant.cutoff,
                    len(deleting),
                    len(dispositions) - len(deleting),
                )
                for expected, disposition in dispositions:
                    await conn.execute(
                        """INSERT INTO connectors.owntracks_retention_batch_rows
                           (batch_id,raw_id,source_revision,logical_source_digest,disposition)
                           VALUES($1,$2,$3,$4,$5)""",
                        grant.batch_id,
                        expected.raw_id,
                        expected.source_revision,
                        bytes.fromhex(expected.logical_source_digest),
                        disposition,
                    )
    committed = await read_batch(pool, grant.batch_id)
    if committed is None:
        raise ForgettingRefusedError("committed_receipt_unknown")
    if not _receipt_matches(committed, grant):
        raise ForgettingRefusedError("committed_receipt_mismatch")
    return committed
