"""Native OwnTracks projection transaction and causal contribution witnesses.

This private context is installed by the owning adapter run, not a tool input.
Only actual native output writers may record contributions. It is not a source
identity credential and does not certify other adapters or foreign copies.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult
from butlers.chronicler.storage import get_checkpoint, mark_source_active, upsert_checkpoint
from butlers.location_retention import ADAPTER_NAMES, content_digest, logical_digest


@dataclass
class _Witness:
    rows: list[Any]
    outputs: dict[UUID, set[UUID]] = field(default_factory=dict)
    typed_outputs: dict[UUID, set[tuple[str, UUID]]] = field(default_factory=dict)
    pending: set[UUID] = field(default_factory=set)
    evaluated: set[UUID] = field(default_factory=set)
    closed_outputs: set[UUID] = field(default_factory=set)
    closed_raw_ids: set[UUID] = field(default_factory=set)


logger = logging.getLogger(__name__)


_current: ContextVar[_Witness | None] = ContextVar("location_native_projection", default=None)


def native_projection_active() -> bool:
    """Only owning run installation establishes this transaction context."""
    return _current.get() is not None


def record_contribution(
    raw_ids: list[UUID],
    output_ids: list[UUID],
    *,
    pending: bool = False,
    output_kind: str = "episode",
) -> None:
    """Consume actual native rows/output IDs inside the installed run context."""
    witness = _current.get()
    if witness is None:
        return
    if output_kind not in {"episode", "point_event"}:
        raise ValueError("unknown native output kind")
    available = {row["id"] for row in witness.rows}
    for raw_id in raw_ids:
        if raw_id not in available:
            raise RuntimeError("projection contribution is outside the native batch")
        witness.evaluated.add(raw_id)
        witness.outputs.setdefault(raw_id, set()).update(output_ids)
        witness.typed_outputs.setdefault(raw_id, set()).update(
            (output_kind, key) for key in output_ids
        )
        if pending:
            witness.pending.add(raw_id)


def record_closed_output(output_id: UUID) -> None:
    """Native gap/cluster evaluation closed this actual written output."""
    witness = _current.get()
    if witness is not None:
        witness.closed_outputs.add(output_id)


def record_closed_carry(raw_ids: list[str]) -> None:
    """An actual native boundary finalized these stored carry contributions."""
    witness = _current.get()
    if witness is not None:
        witness.closed_raw_ids.update(UUID(value) for value in raw_ids)


class ProjectionConnection:
    """Existing pool-shaped helpers remain on this one owning transaction."""

    def __init__(self, conn: asyncpg.Connection, rows: list[Any]) -> None:
        self.conn = conn
        self.location_rows = rows

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self.conn, name)


def _digest_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Native output time is unavailable")
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _digest_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_digest_value(item) for item in value]
    return value


async def output_digest(conn: asyncpg.Connection, outputs: set[tuple[str, UUID]]) -> bytes:
    """Bind actual native semantic bodies; UUID existence is not generation proof."""
    bodies = []
    for kind, key in sorted(outputs, key=lambda pair: (pair[0], str(pair[1]))):
        if kind == "point_event":
            row = await conn.fetchrow("SELECT * FROM point_events WHERE id=$1", key)
            if row is None:
                # A genuine committed own tombstone is a new diminished
                # generation, never a missing-row success or raw reconstruction.
                row = await conn.fetchrow(
                    "SELECT * FROM location_evidence_tombstones WHERE event_id=$1",
                    key,
                )
        elif kind == "episode":
            row = await conn.fetchrow("SELECT * FROM episodes WHERE id=$1", key)
        else:
            raise ValueError("Unregistered native output kind")
        if row is None:
            raise ValueError("Native output generation is unavailable")
        # Storage touch timestamps do not change an otherwise identical
        # semantic generation. Every remaining native persisted field does.
        body = {
            name: value
            for name, value in dict(row).items()
            if name not in {"created_at", "updated_at"}
        }
        bodies.append({"kind": kind, "body": _digest_value(body)})
    return content_digest({"native_outputs": bodies})


async def run_projection(adapter: Any, *, chronicler_pool: asyncpg.Pool) -> AdapterResult:
    """Commit outputs, coverage, carry and checkpoint together under own locks.

    The pending anti-join revisits tied/backdated arrivals. Already evaluated
    open carries remain pending for closure, without double-projecting their
    points. No existing episode or timestamp is accepted as coverage.
    """
    if adapter.source_name not in ADAPTER_NAMES:
        raise ValueError("unregistered location adapter")
    adapter._llm_probe()
    stage = "policy_lock"
    try:
        async with chronicler_pool.acquire() as conn:
            async with conn.transaction():
                policy = await conn.fetchrow(
                    "SELECT version FROM location_retention_policy WHERE singleton FOR UPDATE"
                )
                if policy is None or type(policy["version"]) is not int or policy["version"] <= 0:
                    raise RuntimeError("location policy unavailable")
                stage = "adapter_locks"
                for name in ADAPTER_NAMES:
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", name)
                reachable = await conn.fetchval(
                    "SELECT COALESCE((SELECT has_schema_privilege(oid,'USAGE') "
                    "FROM pg_namespace WHERE nspname='connectors'),false)"
                )
                if not reachable or not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='connectors' AND table_name='owntracks_points')"
                ):
                    await mark_source_active(
                        conn,
                        adapter.source_name,
                        active=False,
                        inactive_reason="source_unavailable",
                    )
                    return AdapterResult(
                        source_name=adapter.source_name,
                        skipped=True,
                        skipped_reason="source_unavailable",
                    )
                stage = "source_selection"
                config = adapter.retention_mapping_revision()
                await conn.execute(
                    """INSERT INTO location_projection_heads(adapter_name,mapping_revision)
                       VALUES($1,$2) ON CONFLICT(adapter_name) DO UPDATE
                       SET replay_pending=CASE WHEN location_projection_heads.mapping_revision
                         IS DISTINCT FROM EXCLUDED.mapping_revision THEN true
                         ELSE location_projection_heads.replay_pending END,
                         replay_watermark=CASE WHEN location_projection_heads.mapping_revision
                         IS DISTINCT FROM EXCLUDED.mapping_revision THEN NULL
                         ELSE location_projection_heads.replay_watermark END,
                         replay_raw_id=CASE WHEN location_projection_heads.mapping_revision
                         IS DISTINCT FROM EXCLUDED.mapping_revision THEN NULL
                         ELSE location_projection_heads.replay_raw_id END,
                         mapping_revision=EXCLUDED.mapping_revision""",
                    adapter.source_name,
                    config,
                )
                checkpoint = await get_checkpoint(conn, adapter.source_name)
                head = await conn.fetchrow(
                    "SELECT * FROM location_projection_heads WHERE adapter_name=$1",
                    adapter.source_name,
                )
                replay_required = getattr(adapter, "retention_replay_required", lambda _: False)
                # An interrupted legacy UUID cursor requests a bounded real
                # source replay. Its durable private head survives pages; it
                # cannot transform legacy invalid lineage into purge authority.
                replay = bool(head and head["replay_pending"])
                if not replay and replay_required(checkpoint):
                    replay = True
                    head = await conn.fetchrow(
                        """UPDATE location_projection_heads SET replay_pending=true,
                           replay_watermark=NULL,replay_raw_id=NULL WHERE adapter_name=$1
                           RETURNING *""",
                        adapter.source_name,
                    )
                if replay:
                    rows = list(
                        await conn.fetch(
                            """SELECT p.* FROM connectors.owntracks_points p
                           WHERE $1::timestamptz IS NULL OR (p.ts,p.id)>($1,$2::uuid)
                           ORDER BY p.ts,p.id LIMIT $3""",
                            head["replay_watermark"],
                            head["replay_raw_id"],
                            adapter.batch_limit,
                        )
                    )
                else:
                    rows = list(
                        await conn.fetch(
                            """SELECT p.* FROM connectors.owntracks_points p
                           WHERE NOT EXISTS(SELECT 1 FROM location_projection_coverage c
                             WHERE c.raw_id=p.id AND c.source_revision=p.source_revision
                               AND c.adapter_name=$1 AND c.mapping_revision=$2)
                           ORDER BY p.recorded_at,p.id LIMIT $3""",
                            adapter.source_name,
                            config,
                            adapter.batch_limit,
                        )
                    )
                witness = _Witness(rows)
                token = _current.set(witness)
                try:
                    bound = ProjectionConnection(conn, rows)
                    stage = "native_projection"
                    result = await adapter.project(
                        bound,
                        chronicler_pool=bound,
                        since=checkpoint.watermark if checkpoint is not None else None,
                    )
                finally:
                    _current.reset(token)
                if result.skipped and not result.error:
                    # The genuine optional-source read made no projection writes.
                    # Do not advance coverage/cursor or convert it into failure.
                    await mark_source_active(
                        conn,
                        adapter.source_name,
                        active=False,
                        inactive_reason="source_unavailable",
                    )
                    return result
                if result.error:
                    raise RuntimeError("native location projection did not complete")
                stage = "coverage_commit"
                batch_id = uuid4()
                for row in rows:
                    # Legacy evaluation must not starve later arrivals. Store an
                    # invalid witness for missing immutable accepted lineage;
                    # this records real evaluation but never earns forgetting.
                    lineage_known = all(
                        row[key] is not None
                        for key in (
                            "logical_source_digest",
                            "content_digest",
                            "accepted_request_id",
                            "accepted_payload_digest",
                            "accepted_normalized_digest",
                        )
                    )
                    raw_id = row["id"]
                    evaluated = raw_id in witness.evaluated
                    outputs = sorted(witness.outputs.get(raw_id, set()), key=str)
                    disposition = (
                        "invalid"
                        if not evaluated or not lineage_known
                        else "pending"
                        if raw_id in witness.pending
                        else "complete"
                        if outputs
                        else "terminal_no_output"
                    )
                    if replay:
                        await conn.execute(
                            """DELETE FROM location_projection_outputs WHERE raw_id=$1
                               AND source_revision=$2 AND adapter_name=$3
                               AND mapping_revision=$4""",
                            raw_id,
                            row["source_revision"],
                            adapter.source_name,
                            config,
                        )
                    write_status = await conn.execute(
                        """INSERT INTO location_projection_coverage
                           (raw_id,logical_source_digest,content_digest,source_revision,
                            adapter_name,mapping_revision,projection_batch_id,disposition,
                            output_ids,output_revision,original_output_revision,completed_at)
                           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10,
                             CASE WHEN $8 IN ('complete','terminal_no_output')
                                  THEN clock_timestamp() ELSE NULL END)
                           ON CONFLICT(raw_id,source_revision,adapter_name,mapping_revision)
                           DO UPDATE SET projection_batch_id=EXCLUDED.projection_batch_id,
                             disposition=EXCLUDED.disposition,output_ids=EXCLUDED.output_ids,
                             output_revision=EXCLUDED.output_revision,
                             completed_at=EXCLUDED.completed_at,last_attempt_at=clock_timestamp()
                           WHERE location_projection_coverage.logical_source_digest=
                             EXCLUDED.logical_source_digest AND
                             location_projection_coverage.content_digest=EXCLUDED.content_digest""",
                        raw_id,
                        row["logical_source_digest"] or logical_digest(row["idempotency_key"]),
                        row["content_digest"] or content_digest({"unverified_raw_id": str(raw_id)}),
                        row["source_revision"],
                        adapter.source_name,
                        config,
                        batch_id,
                        disposition,
                        outputs,
                        await output_digest(conn, witness.typed_outputs.get(raw_id, set())),
                    )
                    if write_status == "INSERT 0 0":
                        raise RuntimeError("stored projection birth differs")
                    for output_kind, output_id in sorted(
                        witness.typed_outputs.get(raw_id, set()),
                        key=lambda pair: (pair[0], str(pair[1])),
                    ):
                        await conn.execute(
                            """INSERT INTO location_projection_outputs
                               (raw_id,source_revision,adapter_name,mapping_revision,
                                output_kind,output_id) VALUES($1,$2,$3,$4,$5,$6)""",
                            raw_id,
                            row["source_revision"],
                            adapter.source_name,
                            config,
                            output_kind,
                            output_id,
                        )
                stage = "carry_closure"
                closed_identities = set()
                for output_id in sorted(witness.closed_outputs, key=str):
                    closed_rows = await conn.fetch(
                        """UPDATE location_projection_coverage
                           SET disposition='complete',completed_at=clock_timestamp()
                           WHERE adapter_name=$1 AND mapping_revision=$2
                             AND disposition='pending' AND EXISTS(
                               SELECT 1 FROM location_projection_outputs o
                               WHERE o.raw_id=location_projection_coverage.raw_id
                                 AND o.source_revision=location_projection_coverage.source_revision
                                 AND o.adapter_name=location_projection_coverage.adapter_name
                                 AND o.mapping_revision=
                                   location_projection_coverage.mapping_revision
                                 AND o.output_kind='episode' AND o.output_id=$3)
                           RETURNING raw_id,source_revision""",
                        adapter.source_name,
                        config,
                        output_id,
                    )
                    closed_identities.update(
                        (row["raw_id"], row["source_revision"]) for row in closed_rows
                    )
                if witness.closed_raw_ids:
                    closed_rows = await conn.fetch(
                        """UPDATE location_projection_coverage
                           SET disposition=CASE WHEN cardinality(output_ids)>0
                             THEN 'complete' ELSE 'terminal_no_output' END,
                             completed_at=clock_timestamp()
                           WHERE adapter_name=$1 AND mapping_revision=$2
                             AND disposition='pending' AND raw_id=ANY($3::uuid[])
                           RETURNING raw_id,source_revision""",
                        adapter.source_name,
                        config,
                        sorted(witness.closed_raw_ids, key=str),
                    )
                    closed_identities.update(
                        (row["raw_id"], row["source_revision"]) for row in closed_rows
                    )
                # Growing/closing a carry changes the persisted summary body.
                # Freeze the final generation for every newly closed native
                # contribution; a stale pre-growth digest cannot earn deletion.
                for raw_id, source_revision in sorted(
                    closed_identities, key=lambda pair: str(pair[0])
                ):
                    typed = await conn.fetch(
                        """SELECT output_kind,output_id FROM location_projection_outputs
                           WHERE raw_id=$1 AND source_revision=$2 AND adapter_name=$3
                             AND mapping_revision=$4 ORDER BY output_kind,output_id""",
                        raw_id,
                        source_revision,
                        adapter.source_name,
                        config,
                    )
                    revision = await output_digest(
                        conn,
                        {(row["output_kind"], row["output_id"]) for row in typed},
                    )
                    await conn.execute(
                        """UPDATE location_projection_coverage SET output_revision=$5
                           WHERE raw_id=$1 AND source_revision=$2 AND adapter_name=$3
                             AND mapping_revision=$4""",
                        raw_id,
                        source_revision,
                        adapter.source_name,
                        config,
                        revision,
                    )
                stage = "checkpoint_commit"
                if replay:
                    await conn.execute(
                        """UPDATE location_projection_heads SET replay_pending=$2,
                           replay_watermark=$3,replay_raw_id=$4 WHERE adapter_name=$1""",
                        adapter.source_name,
                        bool(rows),
                        rows[-1]["ts"] if rows else None,
                        rows[-1]["id"] if rows else None,
                    )
                if rows:
                    last = rows[-1]
                    await conn.execute(
                        """INSERT INTO location_projection_cursors(adapter_name,recorded_at,raw_id)
                           VALUES($1,$2,$3) ON CONFLICT(adapter_name) DO UPDATE
                           SET recorded_at=EXCLUDED.recorded_at,raw_id=EXCLUDED.raw_id""",
                        adapter.source_name,
                        last["recorded_at"],
                        last["id"],
                    )
                await mark_source_active(conn, adapter.source_name, active=True)
                await upsert_checkpoint(
                    conn,
                    adapter.source_name,
                    watermark=result.watermark,
                    success=True,
                    rows_projected=result.rows_projected,
                )
                return result
    except Exception as exc:
        # Only predetermined stage and validated SQLSTATE: never exception
        # text, source/SSID/coordinates, SQL arguments, IDs or provider strings.
        state = getattr(exc, "sqlstate", None)
        state = (
            state if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state) else "unknown"
        )
        category = "postgres" if isinstance(exc, asyncpg.PostgresError) else "native"
        logger.warning(
            "Location projection failure stage=%s category=%s sqlstate=%s", stage, category, state
        )
        # Outside the failed transaction, and with a closed reason only. If this
        # second write also fails it propagates; no fabricated successful state.
        warnings = []
        try:
            await upsert_checkpoint(
                chronicler_pool,
                adapter.source_name,
                success=False,
                error="location_projection_failed",
            )
        except Exception:
            warnings.append("failure_receipt_unavailable")
        return AdapterResult(
            source_name=adapter.source_name, error="location_projection_failed", warnings=warnings
        )
