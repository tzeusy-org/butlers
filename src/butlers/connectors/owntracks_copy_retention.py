"""OwnTracks-owned filtered/replay copy lineage before any raw READY grant.

The fixed connector constructor owns this producer. Frozen locators/digests
select its stored copy cohort, never a role, source identity or deletion vote.
Legacy rows without native birth remain unknown; this module never certifies
an inbox/remote runtime/browser on the connector's behalf.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from butlers.connectors.owntracks_forgetting import DigestHex, FrozenRaw, frozen_manifest
from butlers.location_retention import content_digest, logical_digest


class FilteredCopyPlan(BaseModel):
    """Full immutable owning plan read over the existing registered source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # No lease/grant/ready or caller actor enters this copy-preparation wire.
    decision_id: UUID
    policy_version: StrictInt = Field(gt=0)
    cutoff: AwareDatetime
    manifest_digest: DigestHex
    rows: tuple[FrozenRaw, ...] = Field(min_length=1, max_length=256)

    def check_manifest(self) -> None:
        if (
            len({row.raw_id for row in self.rows}) != len(self.rows)
            or len({row.accepted_request_id for row in self.rows}) != len(self.rows)
            or frozen_manifest(self.decision_id, self.policy_version, self.cutoff, self.rows).hex()
            != self.manifest_digest
        ):
            raise ValueError("native copy plan differs")


def filtered_location_binding(endpoint: str, row: tuple[Any, ...]) -> tuple[bytes, bytes] | None:
    """Validate the ACTUAL configured OwnTracks buffer's canonical location.

    This helper describes a constructor-owned row; its return is not a public
    source capability. Transition/waypoint rows retain their existing behavior.
    A malformed location must refuse, not be reclassified as independent.
    """
    if len(row) != 11 or row[1] != "owntracks" or row[2] != endpoint:
        raise ValueError("native filtered producer differs")
    envelope = row[9]
    if not isinstance(envelope, dict):
        raise ValueError("native filtered body differs")
    source, payload = envelope.get("source"), envelope.get("payload")
    if not isinstance(source, dict) or not isinstance(payload, dict):
        raise ValueError("native filtered source differs")
    if source.get("provider") != "owntracks" or source.get("endpoint_identity") != endpoint:
        raise ValueError("native filtered source differs")
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        raise ValueError("native filtered input differs")
    if raw.get("_type") != "location":
        if raw.get("_type") not in {"transition", "waypoints"}:
            raise ValueError("native filtered input kind differs")
        return None
    from butlers.connectors.owntracks import extract_tst

    stamp = extract_tst(raw)  # Preserve the actual native finite-number codec.
    event = envelope.get("event")
    if (
        stamp is None
        or row[3] != f"{raw['tst']}:location"
        or not isinstance(event, dict)
        or event.get("external_event_id") != row[3]
        or row[4] != "owntracks"
    ):
        raise ValueError("native filtered input binding differs")
    return logical_digest(f"owntracks:{endpoint}:{stamp}:location"), content_digest(raw)


def filtered_row_digest(row: tuple[Any, ...]) -> bytes:
    """Every emitted stored field except database-assigned identity/time.

    Endpoint/sender/reason/status/error copies are included, not merely raw
    JSON. The owning writer freezes the actual native buffer values.
    """
    if len(row) != 11:
        raise ValueError("native filtered row differs")
    return content_digest({"filtered_row": list(row[1:])})


class NativeFilteredCopyBuffer:
    """Private buffer adapter installed only by the fixed OwnTracks constructor.

    The ordinary buffer owns preview sanitization/formatting. This adapter
    commits its actual emitted location row and immutable copy birth on one
    existing connector transaction, before any later preparation can observe
    that copy. Other provider buffers never enter this producer.
    """

    def __init__(self, endpoint: str) -> None:
        from butlers.connectors.filtered_event_buffer import FilteredEventBuffer

        self.buffer = FilteredEventBuffer("owntracks", endpoint)
        self.endpoint = endpoint
        self.closed_sources: set[bytes] = set()
        self._flush_lock = asyncio.Lock()

    def __len__(self) -> int:
        return len(self.buffer)

    def record(self, **fields: Any) -> None:
        self.buffer.record(**fields)
        row = self.buffer._rows[-1]
        try:
            binding = filtered_location_binding(self.endpoint, row)
        except Exception:
            self.buffer._rows.pop()  # Invalid append never becomes a native copy.
            raise
        if binding is not None and binding[0] in self.closed_sources:
            # The owning source preparation records these floors only after
            # its actual committed readback. A caller field cannot add one.
            self.buffer._rows.pop()

    async def flush(self, pool: Any) -> None:
        # Flushes share one snapshot. Records may append while I/O yields;
        # preparation may remove selected rows. Never delete by shifted prefix.
        async with self._flush_lock:
            await self._flush_snapshot(pool)

    def _discard_snapshot(self, rows: list[tuple[Any, ...]]) -> None:
        identities = {id(row) for row in rows}
        self.buffer._rows[:] = [row for row in self.buffer._rows if id(row) not in identities]

    async def _flush_snapshot(self, pool: Any) -> None:
        import logging
        from uuid import uuid4

        import asyncpg

        from butlers.connectors.filtered_event_buffer import _INSERT_SQL

        if not isinstance(pool, asyncpg.Pool):
            # Explicit unconfigured adapters/doubles retain ordinary flush;
            # they cannot produce a native birth, floor or source receipt.
            await self.buffer.flush(pool)
            return
        rows = list(self.buffer._rows)
        births: list[tuple[Any, ...]] = []
        if not rows:
            return
        try:
            await pool.execute(
                "SELECT connectors.connectors_filtered_events_ensure_partition(now())"
            )
            async with pool.acquire() as conn:
                async with conn.transaction():
                    if await conn.fetchval("SELECT current_user") != "connector_writer":
                        raise ValueError("native filtered writer differs")
                    await conn.execute("SET LOCAL lock_timeout='2s'")
                    await conn.execute("SET LOCAL statement_timeout='5s'")
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                        "owntracks:retention:source",
                    )
                    for row in rows:
                        binding = filtered_location_binding(self.endpoint, row)
                        if binding is not None:
                            logical, raw_digest = binding
                            closed = await conn.fetchval(
                                "SELECT EXISTS(SELECT 1 FROM "
                                "connectors.owntracks_filtered_copy_floors "
                                "WHERE logical_source_digest=$1) OR EXISTS("
                                "SELECT 1 FROM connectors.owntracks_retention_tombstones "
                                "WHERE logical_source_digest=$1)",
                                logical,
                            )
                            if closed:
                                continue  # Never refill an already closed native source.
                        emitted = await conn.fetchrow(
                            _INSERT_SQL + " RETURNING id,received_at", *row
                        )
                        if emitted is None or emitted["received_at"] != row[0]:
                            raise ValueError("native filtered emitted row differs")
                        if binding is not None:
                            generation = uuid4()
                            births.append(
                                (
                                    generation,
                                    emitted["id"],
                                    emitted["received_at"],
                                    logical,
                                    raw_digest,
                                    filtered_row_digest(row),
                                )
                            )
                            await conn.execute(
                                "INSERT INTO connectors.owntracks_filtered_copy_births "
                                "(copy_generation,filtered_id,filtered_received_at,logical_source_digest,"
                                "raw_digest,row_digest,producer_contract) "
                                "VALUES($1,$2,$3,$4,$5,$6,1)",
                                generation,
                                emitted["id"],
                                emitted["received_at"],
                                logical,
                                raw_digest,
                                filtered_row_digest(row),
                            )
        except Exception:
            # Native driver exceptions can contain copied JSON/error fields.
            logging.getLogger(__name__).warning("native_filtered_copy_flush_unavailable")
            # Ordinary visibility failure drops these buffered operational
            # rows, but never generates a terminal receipt or deletion vote.
            self._discard_snapshot(rows)
            return
        # An ACK does not prove the committed native birth. Resolve the exact
        # producer-created identities from a different acquisition; never
        # manufacture replacement births after a lost ACK.
        try:
            async with asyncio.timeout(5):
                async with pool.acquire() as observed:
                    for birth in births:
                        stored = await observed.fetchrow(
                            "SELECT copy_generation,filtered_id,filtered_received_at,"
                            "logical_source_digest,raw_digest,row_digest FROM "
                            "connectors.owntracks_filtered_copy_births WHERE copy_generation=$1",
                            birth[0],
                        )
                        if stored is None or tuple(stored.values()) != birth:
                            raise ValueError("committed native filtered birth is unknown")
        except Exception:
            # Unresolved ACK cannot be replayed as a replacement generation.
            # Keep durable uncertainty and prevent a duplicate buffer emission.
            self._discard_snapshot(rows)
            raise ValueError("committed native filtered birth is unknown") from None
        self._discard_snapshot(rows)
        try:
            from butlers.fleet_events import publish_fleet_event

            await publish_fleet_event(pool, "ingestion", {})
        except Exception:
            logging.getLogger(__name__).warning("native_filtered_copy_notification_unavailable")


_FILTERED_COLUMNS = (
    "received_at,connector_type,endpoint_identity,external_message_id,source_channel,"
    "sender_identity,subject_or_preview,filter_reason,status,full_payload,error_detail"
)


def reduced_filtered_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Fixed stored-copy reduction, with no original device/body/display echo."""
    if len(row) != 11:
        raise ValueError("native filtered row differs")
    return (
        row[0],
        "owntracks",
        "retention",
        "retention",
        "owntracks",
        "retention",
        "[Location evidence expired]",
        "retention_expired",
        "filtered",
        {},
        None,
    )


async def read_filtered_receipt(pool: Any, plan: FilteredCopyPlan) -> dict[str, Any] | None:
    """Independent committed read of the COMPLETE native stored-copy receipt."""
    async with pool.acquire() as conn:
        header = await conn.fetchrow(
            "SELECT * FROM connectors.owntracks_filtered_copy_batches WHERE decision_id=$1",
            plan.decision_id,
        )
        if header is None:
            return None
        members = await conn.fetch(
            "SELECT m.*,b.logical_source_digest,b.raw_digest FROM "
            "connectors.owntracks_filtered_copy_members m JOIN "
            "connectors.owntracks_filtered_copy_births b USING(copy_generation) "
            "WHERE m.receipt_id=$1 ORDER BY m.copy_generation",
            header["receipt_id"],
        )
        floors = await conn.fetch(
            "SELECT * FROM connectors.owntracks_filtered_copy_floors "
            "WHERE decision_id=$1 ORDER BY raw_id",
            plan.decision_id,
        )
    wanted = {
        (
            r.raw_id,
            r.source_revision,
            bytes.fromhex(r.logical_source_digest),
            bytes.fromhex(r.content_digest),
        )
        for r in plan.rows
    }
    observed = {
        (r["raw_id"], r["source_revision"], r["logical_source_digest"], r["raw_digest"])
        for r in floors
    }
    if (
        header["manifest_digest"] != bytes.fromhex(plan.manifest_digest)
        or header["policy_version"] != plan.policy_version
        or header["cutoff"] != plan.cutoff
        or len(members) != header["expected_count"]
        or len({m["copy_generation"] for m in members}) != len(members)
        or observed != wanted
        or len(floors) != len(wanted)
        or any(f["manifest_digest"] != header["manifest_digest"] for f in floors)
        or any(
            (m["logical_source_digest"], m["raw_digest"]) not in {(w[2], w[3]) for w in wanted}
            for m in members
        )
    ):
        raise ValueError("committed native filtered receipt differs")
    # Member digests attest only the original producer's write. Verify the
    # current actual stored body from the independent acquisition as well;
    # a receipt alongside a refilled/changed row is not terminal.
    async with pool.acquire() as conn:
        for member in members:
            birth = await conn.fetchrow(
                "SELECT * FROM connectors.owntracks_filtered_copy_births WHERE copy_generation=$1",
                member["copy_generation"],
            )
            stored = await conn.fetchrow(
                "SELECT " + _FILTERED_COLUMNS + " FROM connectors.filtered_events "
                "WHERE id=$1 AND received_at=$2",
                birth["filtered_id"],
                birth["filtered_received_at"],
            )
            if (
                birth["row_digest"] != member["original_digest"]
                or stored is None
                or filtered_row_digest(tuple(stored.values())) != member["reduced_digest"]
                or tuple(stored.values()) != reduced_filtered_row(tuple(stored.values()))
            ):
                raise ValueError("committed native filtered body differs")
    return {**dict(header), "members": [dict(m) for m in members]}


async def prepare_filtered_copies(
    pool: Any,
    plan: FilteredCopyPlan,
    buffers: Sequence[NativeFilteredCopyBuffer] = (),
) -> dict[str, Any]:
    """Actual owning stored-copy reduction BEFORE any point/raw READY.

    This private native worker receives the complete plan only from its fixed
    registered Chronicler. It validates actual owning raw rows and the full
    matching filtered cohort; an old/missing birth or changed body refuses.
    A stored-copy receipt never attests an active webhook, another runtime or
    managed browser. Those independent holders remain mandatory.
    """
    from uuid import uuid4

    import asyncpg

    from butlers.connectors.owntracks_forgetting import _same_raw

    if not isinstance(pool, asyncpg.Pool):
        raise ValueError("native filtered pool differs")
    plan.check_manifest()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "connector_writer":
                raise ValueError("native filtered writer differs")
            await conn.execute("SET LOCAL lock_timeout='2s'")
            await conn.execute("SET LOCAL statement_timeout='5s'")
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                "owntracks:retention:source",
            )
            prior = await conn.fetchrow(
                "SELECT * FROM connectors.owntracks_filtered_copy_batches WHERE decision_id=$1",
                plan.decision_id,
            )
            if prior is None:
                raw = await conn.fetch(
                    "SELECT * FROM connectors.owntracks_points WHERE id=ANY($1::uuid[]) "
                    "ORDER BY id FOR UPDATE",
                    [r.raw_id for r in plan.rows],
                )
                actual = {r["id"]: r for r in raw}
                if len(actual) != len(plan.rows):
                    raise ValueError("native filtered raw source unavailable")
                captured = []
                for expected in sorted(plan.rows, key=lambda r: str(r.raw_id)):
                    source = actual[expected.raw_id]
                    if not _same_raw(source, expected) or source["retention_at"] >= plan.cutoff:
                        raise ValueError("native filtered raw source differs")
                    payload = source["raw_payload"]
                    endpoint = source["endpoint_identity"]
                    from butlers.connectors.owntracks import extract_tst

                    if not isinstance(payload, dict) or (stamp := extract_tst(payload)) is None:
                        raise ValueError("native filtered raw input differs")
                    event = f"{payload['tst']}:location"
                    if (
                        payload.get("_type") != "location"
                        or logical_digest(f"owntracks:{endpoint}:{stamp}:location")
                        != bytes.fromhex(expected.logical_source_digest)
                        or content_digest(payload) != bytes.fromhex(expected.content_digest)
                    ):
                        raise ValueError("native filtered raw input binding differs")
                    from butlers.connectors.owntracks_input_copies import require_inputs_ended

                    await require_inputs_ended(
                        conn,
                        source["source_input_generation"],
                        source["logical_source_digest"],
                        source["content_digest"],
                    )
                    candidates = await conn.fetch(
                        "SELECT id," + _FILTERED_COLUMNS + " FROM connectors.filtered_events "
                        "WHERE connector_type='owntracks' AND endpoint_identity=$1 "
                        "AND external_message_id=$2 ORDER BY received_at,id LIMIT 257 FOR UPDATE",
                        endpoint,
                        event,
                    )
                    if len(candidates) > 256:
                        raise ValueError("native filtered cohort is incomplete")
                    births = await conn.fetch(
                        "SELECT * FROM connectors.owntracks_filtered_copy_births "
                        "WHERE logical_source_digest=$1 ORDER BY copy_generation",
                        bytes.fromhex(expected.logical_source_digest),
                    )
                    if len(births) != len(candidates):
                        raise ValueError("native filtered cohort is incomplete")
                    by_row = {(b["filtered_id"], b["filtered_received_at"]): b for b in births}
                    if len(by_row) != len(births):
                        raise ValueError("native filtered cohort differs")
                    for candidate in candidates:
                        row = tuple(candidate[name] for name in _FILTERED_COLUMNS.split(","))
                        birth = by_row.get((candidate["id"], candidate["received_at"]))
                        if (
                            birth is None
                            or birth["producer_contract"] != 1
                            or birth["raw_digest"] != bytes.fromhex(expected.content_digest)
                            or filtered_location_binding(endpoint, row)
                            != (birth["logical_source_digest"], birth["raw_digest"])
                            or filtered_row_digest(row) != birth["row_digest"]
                        ):
                            raise ValueError("native filtered captured body differs")
                        reduced = reduced_filtered_row(row)
                        await conn.execute(
                            "UPDATE connectors.filtered_events SET endpoint_identity=$3,"
                            "external_message_id=$4,sender_identity=$5,subject_or_preview=$6,"
                            "filter_reason=$7,status=$8,full_payload=$9,error_detail=$10 "
                            "WHERE id=$1 AND received_at=$2",
                            candidate["id"],
                            row[0],
                            reduced[2],
                            reduced[3],
                            reduced[5],
                            reduced[6],
                            reduced[7],
                            reduced[8],
                            reduced[9],
                            reduced[10],
                        )
                        captured.append(
                            (
                                birth["copy_generation"],
                                birth["row_digest"],
                                filtered_row_digest(reduced),
                            )
                        )
                    await conn.execute(
                        "INSERT INTO connectors.owntracks_filtered_copy_floors "
                        "(logical_source_digest,decision_id,raw_id,source_revision,raw_digest,"
                        "manifest_digest) VALUES($1,$2,$3,$4,$5,$6)",
                        bytes.fromhex(expected.logical_source_digest),
                        plan.decision_id,
                        expected.raw_id,
                        expected.source_revision,
                        bytes.fromhex(expected.content_digest),
                        bytes.fromhex(plan.manifest_digest),
                    )
                receipt = uuid4()
                await conn.execute(
                    "INSERT INTO connectors.owntracks_filtered_copy_batches "
                    "(decision_id,manifest_digest,receipt_id,policy_version,cutoff,expected_count) "
                    "VALUES($1,$2,$3,$4,$5,$6)",
                    plan.decision_id,
                    bytes.fromhex(plan.manifest_digest),
                    receipt,
                    plan.policy_version,
                    plan.cutoff,
                    len(captured),
                )
                for generation, original, reduced in captured:
                    await conn.execute(
                        "INSERT INTO connectors.owntracks_filtered_copy_members "
                        "(receipt_id,copy_generation,original_digest,reduced_digest) "
                        "VALUES($1,$2,$3,$4)",
                        receipt,
                        generation,
                        original,
                        reduced,
                    )
    committed = await read_filtered_receipt(pool, plan)
    if committed is None:
        raise ValueError("committed native filtered receipt unknown")
    # Close only the actual configured buffers after the exact durable floor
    # readback. Other active request/runtime holders are NOT attested here.
    closed = {bytes.fromhex(r.logical_source_digest) for r in plan.rows}
    for buffer in buffers:
        buffer.closed_sources.update(closed)
        buffer.buffer._rows[:] = [
            row
            for row in buffer.buffer._rows
            if (binding := filtered_location_binding(buffer.endpoint, row)) is None
            or binding[0] not in closed
        ]
    return committed
