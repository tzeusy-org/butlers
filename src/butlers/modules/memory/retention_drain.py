"""Fixed-cutoff Switchboard episode drain; live access is strictly read-only.

The write engine is callable only for isolated rehearsal databases. A future
live writer needs target-bound managed restore and owner authority that the
current restore reader does not expose. No caller-provided flag substitutes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

ROLE = "butler_switchboard_rw"
SCHEMA = "switchboard"
LOCK_KEY = 1381254482
EVENT = "retention.controlled"


def source_identity() -> str:
    """Bind exact executing source/provenance migration, never credential data."""
    root = Path(__file__).resolve().parents[4]
    paths = [
        Path(__file__),
        root / "scripts/retention_drain.py",
        root / "scripts/repair_attention_ledger_metadata.py",
        root / "src/butlers/db.py",
        Path(__file__).parent / "migrations/010_preserve_episode_provenance.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


class Refused(Exception):
    """Closed failure category; never construct from driver or row text."""


@dataclass(frozen=True)
class Plan:
    run_id: UUID
    environment: str
    database: str
    operator: str
    cutoff: datetime
    window_start: datetime
    window_end: datetime
    batch_rows: int
    total_rows: int
    max_batches: int
    duration_seconds: int
    statement_ms: int
    lock_ms: int
    schema: str = SCHEMA
    role: str = ROLE
    source_sha256: str = ""

    def validate(self) -> None:
        if self.source_sha256 != source_identity():
            raise Refused("source_drift")
        if self.environment not in {"butlers-dev", "isolated"}:
            raise Refused("target_invalid")
        if self.schema != SCHEMA or self.role != ROLE:
            raise Refused("target_invalid")
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,63}", self.database):
            raise Refused("target_invalid")
        if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,80}", self.operator):
            raise Refused("operator_invalid")
        if not isinstance(self.run_id, UUID):
            raise Refused("run_invalid")
        for value in (self.cutoff, self.window_start, self.window_end):
            if not isinstance(value, datetime):
                raise Refused("time_invalid")
            offset = value.utcoffset()
            if offset is None:
                raise Refused("time_invalid")
            if offset.total_seconds() != 0:
                raise Refused("utc_required")
        if not self.cutoff <= self.window_start < self.window_end:
            raise Refused("window_invalid")
        if (self.window_end - self.window_start).total_seconds() > 3600:
            raise Refused("window_invalid")
        bounds = (
            (self.batch_rows, 1, 1000),
            (self.total_rows, 1, 10000),
            (self.max_batches, 1, 100),
            (self.duration_seconds, 1, 300),
            (self.statement_ms, 1, 30000),
            (self.lock_ms, 1, 2000),
        )
        if any(type(v) is not int or not low <= v <= high for v, low, high in bounds):
            raise Refused("bounds_invalid")
        if self.batch_rows > self.total_rows:
            raise Refused("bounds_invalid")
        if self.total_rows > self.batch_rows * self.max_batches:
            raise Refused("bounds_invalid")

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["run_id"] = str(self.run_id)
        for key in ("cutoff", "window_start", "window_end"):
            result[key] = result[key].isoformat()
        return result

    @property
    def digest(self) -> str:
        return _digest(self.public())


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# Expiry is fixed at the plan cutoff; never capacity-driven. Select only
# non-content fields. The left join preserves unknown lineage as UNKNOWN.
CANDIDATES = """
SELECT e.id, e.session_id, e.created_at, e.expires_at, e.consolidation_status,
       e.butler='switchboard' AS known_butler,
       encode(sha256(convert_to(e.retention_class,'UTF8')),'hex') AS policy_fingerprint,
       e.leased_until, e.leased_by IS NOT NULL AS leased,
       encode(sha256(convert_to(e.tenant_id,'UTF8')),'hex') AS tenant_fingerprint,
       encode(sha256(convert_to(s.trigger_source,'UTF8')),'hex') AS provenance_fingerprint,
       CASE WHEN e.butler <> 'switchboard' OR e.session_id IS NULL
                 OR s.id IS NULL OR s.trigger_source IS NULL OR s.trigger_source = ''
                 OR e.metadata IS NULL OR jsonb_typeof(e.metadata) <> 'object'
                 OR p.retention_class IS NULL
            THEN 'unknown'
            WHEN s.trigger_source IN ('schedule:consolidation', 'schedule:memory_consolidation')
                 OR e.metadata ? 'quarantined' OR e.metadata ? 'quarantine'
                 OR p.archive_before_delete
            THEN 'excluded'
            WHEN s.trigger_source IN ('tick','external','trigger','route','classification',
                                      'healing','qa','discretion') OR task.id IS NOT NULL
            THEN 'eligible' ELSE 'unknown' END AS disposition
FROM switchboard.episodes e
LEFT JOIN switchboard.sessions s ON s.id = e.session_id
LEFT JOIN switchboard.memory_policies p ON p.retention_class=e.retention_class
LEFT JOIN switchboard.scheduled_tasks task ON s.trigger_source='schedule:' || task.name
WHERE e.expires_at < $1
  AND (e.consolidation_status <> 'pending' OR e.expires_at < $1 - interval '7 days')
ORDER BY e.created_at, e.id
"""


def _row_identity(row: Any) -> dict[str, Any]:
    return {
        key: (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )
        for key, value in dict(row).items()
    }


async def _snapshot(conn: Any, plan: Plan) -> tuple[list[Any], dict[str, Any]]:
    rows = []
    counts = {key: 0 for key in ("eligible", "excluded", "unknown")}
    digest = hashlib.sha256()
    async for row in conn.cursor(CANDIDATES, plan.cutoff, prefetch=256):
        counts[row["disposition"]] += 1
        encoded = json.dumps(_row_identity(row), sort_keys=True, separators=(",", ":")).encode()
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
        if len(rows) <= plan.total_rows:
            rows.append(row)
    return rows, {
        **counts,
        "observed": sum(counts.values()),
        "scope_over_limit": sum(counts.values()) > plan.total_rows,
        "candidate_fingerprint": digest.hexdigest(),
    }


async def _identity(conn: Any, plan: Plan) -> str:
    row = await conn.fetchrow("""
        SELECT current_database() AS db, current_user AS actor,
          (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user) AS elevated,
          clock_timestamp() AS instant,
          (SELECT oid FROM pg_database WHERE datname=current_database())::text AS database_oid,
          inet_server_addr()::text AS address, inet_server_port() AS port
    """)
    if row["db"] != plan.database or row["actor"] != ROLE or row["elevated"]:
        raise Refused("database_identity")
    if not plan.window_start <= row["instant"] < plan.window_end:
        raise Refused("outside_window")
    # Require the actual deployed provenance trigger, not merely its table name.
    valid = await conn.fetchval("""
        SELECT count(*) = 1 FROM pg_trigger t
        JOIN pg_proc p ON p.oid=t.tgfoid
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE t.tgrelid='switchboard.episodes'::regclass
          AND t.tgname='trg_episodes_preserve_provenance'
          AND t.tgenabled='O' AND t.tgtype=11 AND NOT t.tgisinternal
          AND p.proname='record_episode_tombstone' AND n.nspname='switchboard'
          AND NOT p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog']::text[]
    """)
    if not valid:
        raise Refused("provenance_unavailable")
    body = await conn.fetchval(
        "SELECT prosrc FROM pg_proc WHERE oid="
        "'switchboard.record_episode_tombstone()'::regprocedure"
    )
    expected = """BEGIN EXECUTE format(
      'INSERT INTO %I.episode_tombstones (episode_id) '
      'VALUES ($1) ON CONFLICT (episode_id) DO NOTHING', TG_TABLE_SCHEMA
      ) USING OLD.id; RETURN OLD; END;"""

    def normalize(value):
        return " ".join(re.sub(r"--[^\n]*", "", value).split())

    if normalize(body) != normalize(expected):
        raise Refused("provenance_unavailable")
    return _digest({k: row[k] for k in ("db", "actor", "database_oid", "address", "port")})


async def _configure(conn: Any, plan: Plan) -> str:
    await conn.execute("SELECT set_config('statement_timeout',$1,true)", str(plan.statement_ms))
    await conn.execute("SELECT set_config('lock_timeout',$1,true)", str(plan.lock_ms))
    return await _identity(conn, plan)


async def _event(conn: Any, plan: Plan, payload: dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO switchboard.memory_events
          (event_type, actor, actor_butler, request_id, memory_type, payload)
        VALUES ($1,current_user,'switchboard',$2,'episode',$3::text::jsonb)
    """,
        EVENT,
        str(plan.run_id),
        json.dumps(payload, sort_keys=True),
    )


async def readback(conn: Any, plan: Plan) -> list[dict[str, Any]]:
    """Authoritative same-run batch readback; never authorize an automatic retry."""
    rows = await conn.fetch(
        """
        SELECT CASE WHEN octet_length(payload::text)<=8192 THEN payload::text END AS payload,
               actor, actor_butler, memory_type FROM switchboard.memory_events
        WHERE event_type=$1 AND request_id=$2 ORDER BY id LIMIT 102
    """,
        EVENT,
        str(plan.run_id),
    )
    if len(rows) > 101:
        raise Refused("run_evidence_invalid")
    keys = {
        "plan_digest",
        "sequence",
        "candidate_fingerprint",
        "deleted",
        "committed_total",
        "plan",
    }
    events = []
    for row in rows:
        if (
            row["payload"] is None
            or row["actor"] != ROLE
            or row["actor_butler"] != "switchboard"
            or row["memory_type"] != "episode"
        ):
            raise Refused("run_evidence_invalid")
        event = json.loads(row["payload"])
        if not isinstance(event, dict) or set(event) != keys:
            raise Refused("run_evidence_invalid")
        if event["plan_digest"] != plan.digest or event["plan"] != plan.public():
            raise Refused("run_identity_conflict")
        if (
            any(type(event[k]) is not int for k in ("sequence", "deleted", "committed_total"))
            or not 0 <= event["sequence"] <= plan.max_batches
            or not 0 <= event["deleted"] <= plan.batch_rows
            or not 0 <= event["committed_total"] <= plan.total_rows
            or not isinstance(event["candidate_fingerprint"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", event["candidate_fingerprint"])
        ):
            raise Refused("run_evidence_invalid")
        events.append(event)
    events.sort(key=lambda e: e["sequence"])
    if [e["sequence"] for e in events] != list(range(len(events))):
        raise Refused("run_evidence_invalid")
    total = 0
    for event in events:
        total += event["deleted"]
        if (
            event["committed_total"] != total
            or event["candidate_fingerprint"] != events[0]["candidate_fingerprint"]
            or (event["sequence"] == 0 and event["deleted"] != 0)
            or (event["sequence"] > 0 and event["deleted"] == 0)
        ):
            raise Refused("run_evidence_invalid")
    return events


async def _fence_owned(conn: Any) -> None:
    held = await conn.fetchval(
        """SELECT EXISTS (
        SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()
        AND classid=0 AND objid=$1 AND objsubid=1 AND granted)""",
        LOCK_KEY,
    )
    if not held:
        raise Refused("fence_lost")


async def dry_run(conn: Any, plan: Plan) -> dict[str, Any]:
    """No DML/DDL. PostgreSQL READ ONLY is installed before identity/candidates."""
    plan.validate()
    locked = False
    try:
        async with asyncio.timeout(plan.duration_seconds):
            async with conn.transaction(isolation="repeatable_read", readonly=True):
                target = await _configure(conn, plan)
                if await conn.fetchval("SHOW transaction_read_only") != "on":
                    raise Refused("write_denial_unavailable")
                locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY)
                if not locked:
                    raise Refused("operator_busy")
                if await readback(conn, plan):
                    raise Refused("run_replayed")
                _, stats = await _snapshot(conn, plan)
                if stats["unknown"]:
                    return {
                        "status": "REFUSED",
                        "reason": "unknown_provenance",
                        "plan": plan.public(),
                        "database_fingerprint": target,
                        **stats,
                    }
                if stats["scope_over_limit"]:
                    return {
                        "status": "REFUSED",
                        "reason": "candidate_limit",
                        "plan": plan.public(),
                        "database_fingerprint": target,
                        **stats,
                    }
                return {
                    "status": "READ_ONLY",
                    "plan": plan.public(),
                    "database_fingerprint": target,
                    **stats,
                }
    finally:
        if locked:
            await _unlock(conn)


async def _unlock(conn: Any) -> None:
    try:
        released = await asyncio.wait_for(
            conn.fetchval("SELECT pg_advisory_unlock($1)", LOCK_KEY), 2
        )
        if released is not True:
            conn.terminate()
            raise Refused("fence_release_unproven")
    except BaseException:
        conn.terminate()
        raise


async def rehearse_batches(conn: Any, plan: Plan, expected_fingerprint: str) -> dict[str, Any]:
    """Actual atomic engine, restricted to isolated fixture DBs (no live CLI).

    The returned UNKNOWN is a STOP. readback() can identify already committed
    batches, but never resumes a run or manufactures a second authorization.
    """
    plan.validate()
    if plan.environment != "isolated" or not plan.database.startswith("test_retention_"):
        raise Refused("live_delete_authority_unavailable")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_fingerprint):
        raise Refused("fingerprint_invalid")
    locked = False
    committed = 0
    sequence = 0
    outcome = "ROLLED_BACK"
    try:
        async with asyncio.timeout(plan.duration_seconds):
            async with conn.transaction(readonly=True):
                await _configure(conn, plan)
                locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY)
                if not locked:
                    raise Refused("operator_busy")
                if await readback(conn, plan):
                    raise Refused("run_replayed")
                initial, stats = await _snapshot(conn, plan)
                if stats["unknown"]:
                    raise Refused("unknown_provenance")
                if stats["scope_over_limit"]:
                    raise Refused("candidate_limit")
                if stats["candidate_fingerprint"] != expected_fingerprint:
                    raise Refused("candidate_drift")
                expected_scope = [_row_identity(r) for r in initial]
                expected = [r for r in expected_scope if r["disposition"] == "eligible"]
            outcome = "UNKNOWN"
            async with conn.transaction():
                await _configure(conn, plan)
                await _fence_owned(conn)
                await _event(
                    conn,
                    plan,
                    {
                        "plan_digest": plan.digest,
                        "sequence": 0,
                        "plan": plan.public(),
                        "candidate_fingerprint": expected_fingerprint,
                        "deleted": 0,
                        "committed_total": 0,
                    },
                )
            outcome = "COMMITTED"
            batches = 0
            for sequence in range(1, plan.max_batches + 1):
                if not expected:
                    break
                outcome = "ROLLED_BACK"
                transaction = conn.transaction(isolation="serializable")
                await transaction.start()
                try:
                    await _configure(conn, plan)
                    await _fence_owned(conn)
                    # Freeze source rows against concurrent writers before the
                    # second complete snapshot. Serializable protects phantoms.
                    await conn.fetch(
                        "SELECT id FROM switchboard.episodes "
                        "WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
                        [UUID(r["id"]) for r in expected],
                    )
                    current, check = await _snapshot(conn, plan)
                    if (
                        check["unknown"]
                        or check["scope_over_limit"]
                        or [_row_identity(r) for r in current] != expected_scope
                    ):
                        raise Refused("candidate_drift")
                    chosen = [r for r in current if r["disposition"] == "eligible"][
                        : plan.batch_rows
                    ]
                    ids = [r["id"] for r in chosen]
                    if any(r["leased"] or r["leased_until"] is not None for r in chosen):
                        raise Refused("candidate_leased")
                    result = await conn.execute(
                        "DELETE FROM switchboard.episodes WHERE id=ANY($1::uuid[])", ids
                    )
                    if result != f"DELETE {len(ids)}":
                        raise Refused("count_mismatch")
                    tombstones = await conn.fetchval(
                        "SELECT count(*) FROM switchboard.episode_tombstones "
                        "WHERE episode_id=ANY($1::uuid[])",
                        ids,
                    )
                    if tombstones != len(ids):
                        raise Refused("provenance_failed")
                    await _event(
                        conn,
                        plan,
                        {
                            "plan_digest": plan.digest,
                            "sequence": sequence,
                            "candidate_fingerprint": expected_fingerprint,
                            "deleted": len(ids),
                            "committed_total": committed + len(ids),
                            "plan": plan.public(),
                        },
                    )
                except BaseException:
                    outcome = "UNKNOWN"
                    await transaction.rollback()
                    outcome = "ROLLED_BACK"
                    raise
                # Once COMMIT is sent, transport/cancellation failure cannot be
                # interpreted as rollback. Never launch another batch afterward.
                outcome = "UNKNOWN"
                await transaction.commit()
                committed += len(ids)
                batches += 1
                expected = expected[len(ids) :]
                removed = {str(value) for value in ids}
                expected_scope = [r for r in expected_scope if r["id"] not in removed]
                outcome = "COMMITTED"
            return {
                "status": "COMMITTED",
                "committed": committed,
                "remaining": len(expected),
                "batches": batches,
            }
    except Refused:
        raise
    except (Exception, asyncio.CancelledError) as exc:
        # Preserve cancellation for caller cleanup, retaining durable readback as
        # the only authority for progress. Driver text never crosses this API.
        if isinstance(exc, asyncio.CancelledError):
            raise
        return {"status": outcome, "committed": committed, "reason": "batch_failed"}
    finally:
        if locked:
            await _unlock(conn)
