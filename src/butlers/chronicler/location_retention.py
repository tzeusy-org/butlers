"""Owning policy and durable attempts for conditional OwnTracks forgetting.

SQL here uses the Chronicler pool, never a peer writer. A policy deadline alone
does not remove or conceal evidence. Only independently observed committed
receipts may report deletion; unfinished attempts remain pending or unknown.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from butlers.location_retention import ADAPTER_NAMES, attempt_status, reduced_summary, strict_days

logger = logging.getLogger(__name__)


class PolicyUpdate(BaseModel):
    """Closed owner-control wire: no caller actor, cutoff, provider or grant."""

    model_config = ConfigDict(extra="forbid")
    days: StrictInt = Field(ge=1, le=30)
    expected_version: StrictInt = Field(gt=0, le=2**63 - 1)


class PolicyUnavailableError(RuntimeError):
    """Missing/malformed installed authority is not a default-success policy."""


class PolicyConflictError(RuntimeError):
    """The owner must review the currently committed version before changing it."""


def _policy(row: Any) -> dict[str, Any]:
    if row is None:
        raise PolicyUnavailableError("Location retention policy is unavailable")
    try:
        days = strict_days(row["days"])
        version = row["version"]
        if type(version) is not int or version <= 0 or row["spatial_scheme_version"] != 1:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyUnavailableError("Location retention policy is unavailable") from exc
    return {
        "days": days,
        "version": version,
        "updated_at": row["updated_at"],
        "raw_age_basis": "original effective event time; four-hour server skew fallback",
        "precision_after_forgetting_m": 150,
        "widening_restores_forgotten_points": False,
        "prepared_decisions_may_finish": True,
    }


async def read_policy(pool: asyncpg.Pool) -> dict[str, Any]:
    row = await pool.fetchrow("SELECT * FROM location_retention_policy WHERE singleton")
    return _policy(row)


async def ready_batches(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Fixed owning MCP read of stored ready decisions; no caller raw selector.

    The native readiness transition must have verified each required holder
    outside its transaction. This read never upgrades pending plans to ready.
    Lease renewal names the same frozen decision and batch, not new evidence.
    """
    from butlers.connectors.owntracks_forgetting import ReadyGrant

    results = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            _policy(
                await conn.fetchrow(
                    "SELECT * FROM location_retention_policy WHERE singleton FOR UPDATE"
                )
            )
            grants = await conn.fetch(
                """SELECT g.*,p.policy_version,p.cutoff FROM location_retention_grants g
                   JOIN location_retention_plans p ON p.decision_id=g.decision_id
                   WHERE p.state='ready' ORDER BY p.prepared_at,p.decision_id LIMIT 8
                   FOR UPDATE OF g,p"""
            )
            for stored in grants:
                rows = await conn.fetch(
                    "SELECT * FROM location_retention_plan_rows "
                    "WHERE decision_id=$1 ORDER BY raw_id",
                    stored["decision_id"],
                )
                if not rows or len(rows) > 256:
                    raise PolicyUnavailableError("Stored retention decision is unavailable")
                renewed = await conn.fetchrow(
                    """UPDATE location_retention_grants
                       SET lease_version=lease_version+1,
                         lease_until=clock_timestamp()+interval '120 seconds'
                       WHERE grant_id=$1 RETURNING lease_version,lease_until""",
                    stored["grant_id"],
                )
                grant = ReadyGrant.model_validate(
                    {
                        "grant_id": stored["grant_id"],
                        "decision_id": stored["decision_id"],
                        "batch_id": stored["batch_id"],
                        "policy_version": stored["policy_version"],
                        "cutoff": stored["cutoff"],
                        "manifest_digest": stored["manifest_digest"].hex(),
                        "lease_version": renewed["lease_version"],
                        "lease_until": renewed["lease_until"],
                        "rows": [
                            {
                                "raw_id": row["raw_id"],
                                "source_revision": row["source_revision"],
                                "logical_source_digest": row["logical_source_digest"].hex(),
                                "content_digest": row["content_digest"].hex(),
                                "retention_at": row["retention_at"],
                                "accepted_request_id": row["accepted_request_id"],
                                "accepted_payload_digest": row["accepted_payload_digest"].hex(),
                                "accepted_normalized_digest": row[
                                    "accepted_normalized_digest"
                                ].hex(),
                            }
                            for row in rows
                        ],
                    }
                )
                grant.check_manifest()
                results.append(grant.model_dump(mode="json"))
    return results


async def plan_status(pool: asyncpg.Pool, decision_id: UUID) -> dict[str, Any]:
    row = await pool.fetchrow(
        """SELECT decision_id,state,policy_version,cutoff,prepared_at
           FROM location_retention_plans WHERE decision_id=$1""",
        decision_id,
    )
    if row is None:
        raise PolicyUnavailableError("Stored retention decision is unavailable")
    members = await pool.fetch(
        "SELECT * FROM location_retention_plan_rows WHERE decision_id=$1 ORDER BY raw_id",
        decision_id,
    )
    if not members or len(members) > 256:
        raise PolicyUnavailableError("Stored retention decision is unavailable")
    from butlers.connectors.owntracks_forgetting import FrozenRaw

    return {
        **dict(row),
        "manifest_digest": (
            await pool.fetchval(
                "SELECT manifest_digest FROM location_retention_plans WHERE decision_id=$1",
                decision_id,
            )
        ).hex(),
        "rows": [
            FrozenRaw.model_validate(
                {
                    **{
                        key: member[key]
                        for key in (
                            "raw_id",
                            "source_revision",
                            "retention_at",
                            "accepted_request_id",
                        )
                    },
                    **{
                        key: member[key].hex()
                        for key in (
                            "logical_source_digest",
                            "content_digest",
                            "accepted_payload_digest",
                            "accepted_normalized_digest",
                        )
                    },
                }
            ).model_dump(mode="json")
            for member in members
        ],
    }


async def set_policy(
    pool: asyncpg.Pool,
    *,
    days: int,
    expected_version: int,
    server_actor: str,
) -> dict[str, Any]:
    """Private HTTP owner-control consumer; never expose as an MCP model tool.

    The real route must establish current owner authority before invoking this
    function. The actor string is attribution, not an authentication check.
    Preparation takes this same first lock; already committed plans survive a
    later widening, and the response explicitly says so.
    """
    strict_days(days)
    if type(expected_version) is not int or expected_version <= 0:
        raise ValueError("expected version must be a positive integer")
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM location_retention_policy WHERE singleton FOR UPDATE"
            )
            policy = _policy(current)
            if policy["version"] != expected_version:
                raise PolicyConflictError("Location retention policy changed; reload before saving")
            row = await conn.fetchrow(
                """UPDATE location_retention_policy
                   SET days=$1, version=version+1,updated_at=clock_timestamp(),updated_by=$2
                   WHERE singleton RETURNING *""",
                days,
                server_actor,
            )
            if days < policy["days"]:
                # Wake only the native deterministic job. No caller-supplied job
                # selector, source endpoint or raw cutoff crosses this door.
                await conn.execute(
                    """UPDATE scheduled_tasks SET next_run_at=clock_timestamp()
                       WHERE name='chronicler_location_retention'
                         AND job_name='chronicler_location_retention'
                         AND dispatch_mode='job' AND enabled"""
                )
    return _policy(row)


async def start_attempt(pool: asyncpg.Pool) -> UUID:
    """Commit attempt_start independently, so rollback cannot revive old green."""
    run_id = uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            policy = _policy(
                await conn.fetchrow(
                    "SELECT * FROM location_retention_policy WHERE singleton FOR UPDATE"
                )
            )
            anchor = await conn.fetchval("SELECT clock_timestamp()")
            await conn.execute(
                """INSERT INTO location_retention_runs
                   (run_id,policy_version,cutoff,attempt_started_at,lease_until,status)
                   VALUES($1,$2,$3,$4,$5,'preparing')""",
                run_id,
                policy["version"],
                anchor - timedelta(days=policy["days"]),
                anchor,
                anchor + timedelta(minutes=10),
            )
    return run_id


async def fail_attempt(pool: asyncpg.Pool, run_id: UUID, *, cancelled: bool = False) -> None:
    """Run outside the failed business transaction; never persist exceptions."""
    await pool.execute(
        """UPDATE location_retention_runs
           SET status=$2,reason_code=$3,completion_at=clock_timestamp()
           WHERE run_id=$1 AND completion_at IS NULL""",
        run_id,
        "cancelled" if cancelled else "failed",
        "cancelled" if cancelled else "storage_error",
    )


async def prepare_batch(pool: asyncpg.Pool, run_id: UUID) -> UUID | None:
    """Irreversible own preparation; missing holder receipts still withhold READY.

    Only actual source rows with all current native adapter witnesses qualify.
    Output UUID kinds are explicit, not inferred from a coincidentally existing
    leg/place. Policy, adapter locks and sorted outputs share the writer order.
    """
    from butlers.connectors.owntracks_forgetting import FrozenRaw, frozen_manifest

    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read"):
            policy = _policy(
                await conn.fetchrow(
                    "SELECT * FROM location_retention_policy WHERE singleton FOR UPDATE"
                )
            )
            for name in ADAPTER_NAMES:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", name)
            run = await conn.fetchrow(
                "SELECT * FROM location_retention_runs WHERE run_id=$1 FOR UPDATE", run_id
            )
            if run is None or run["policy_version"] != policy["version"]:
                raise PolicyConflictError("Policy changed before preparation")
            source_present = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='connectors' AND table_name='owntracks_points')"
            )
            if not source_present:
                await conn.execute(
                    """UPDATE location_retention_runs SET status='unavailable',
                       reason_code='source_unavailable',completion_at=clock_timestamp()
                       WHERE run_id=$1""",
                    run_id,
                )
                return None
            overdue = await conn.fetchval(
                "SELECT count(*) FROM connectors.owntracks_points WHERE retention_at<$1",
                run["cutoff"],
            )
            blocked = await conn.fetchval(
                """SELECT count(*) FROM connectors.owntracks_points p
                   WHERE p.retention_at<$1 AND NOT (
                     p.accepted_request_id IS NOT NULL
                     AND p.logical_source_digest IS NOT NULL AND p.content_digest IS NOT NULL
                     AND p.accepted_payload_digest IS NOT NULL
                     AND p.accepted_normalized_digest IS NOT NULL
                     AND (SELECT count(DISTINCT c.adapter_name)
                         FROM location_projection_coverage c
                         JOIN location_projection_heads h ON h.adapter_name=c.adapter_name
                           AND h.mapping_revision=c.mapping_revision
                         WHERE c.raw_id=p.id AND c.source_revision=p.source_revision
                           AND c.logical_source_digest=p.logical_source_digest
                           AND c.content_digest=p.content_digest
                           AND c.adapter_name=ANY($2::text[])
                           AND c.disposition IN ('complete','terminal_no_output'))=3)""",
                run["cutoff"],
                list(ADAPTER_NAMES),
            )
            await conn.execute(
                """UPDATE location_retention_runs SET overdue_count=$2,blocked_count=$3,
                   holder_pending_count=$2-$3,counts_observed_at=clock_timestamp()
                   WHERE run_id=$1""",
                run_id,
                overdue,
                blocked,
            )
            rows = await conn.fetch(
                """SELECT p.* FROM connectors.owntracks_points p
                   WHERE p.retention_at<$1 AND p.accepted_request_id IS NOT NULL
                     AND p.logical_source_digest IS NOT NULL AND p.content_digest IS NOT NULL
                     AND p.accepted_payload_digest IS NOT NULL
                     AND p.accepted_normalized_digest IS NOT NULL
                     AND NOT EXISTS(SELECT 1 FROM location_retention_plan_rows r
                         WHERE r.raw_id=p.id AND r.source_revision=p.source_revision)
                     AND (SELECT count(DISTINCT c.adapter_name)
                         FROM location_projection_coverage c
                         JOIN location_projection_heads h ON h.adapter_name=c.adapter_name
                           AND h.mapping_revision=c.mapping_revision
                         WHERE c.raw_id=p.id AND c.source_revision=p.source_revision
                           AND c.logical_source_digest=p.logical_source_digest
                           AND c.content_digest=p.content_digest
                           AND c.adapter_name=ANY($2::text[])
                           AND c.disposition IN ('complete','terminal_no_output'))=3
                   ORDER BY p.id LIMIT 256""",
                run["cutoff"],
                list(ADAPTER_NAMES),
            )
            if not rows:
                await conn.execute(
                    """UPDATE location_retention_runs SET status='pending',
                       reason_code=CASE WHEN $2=0 THEN 'holder_pending'
                         ELSE 'projection_pending' END,
                       completion_at=NULL
                       WHERE run_id=$1""",
                    run_id,
                    blocked,
                )
                return None
            from butlers.chronicler.location_projection import output_digest

            for raw in rows:
                coverage = await conn.fetch(
                    """SELECT c.* FROM location_projection_coverage c
                       JOIN location_projection_heads h ON h.adapter_name=c.adapter_name
                         AND h.mapping_revision=c.mapping_revision
                       WHERE raw_id=$1 AND source_revision=$2 ORDER BY c.adapter_name""",
                    raw["id"],
                    raw["source_revision"],
                )
                for witness in coverage:
                    actual_outputs = await conn.fetch(
                        """SELECT output_kind,output_id FROM location_projection_outputs
                           WHERE raw_id=$1 AND source_revision=$2 AND adapter_name=$3
                             AND mapping_revision=$4 ORDER BY output_kind,output_id""",
                        raw["id"],
                        raw["source_revision"],
                        witness["adapter_name"],
                        witness["mapping_revision"],
                    )
                    current = await output_digest(
                        conn,
                        {(output["output_kind"], output["output_id"]) for output in actual_outputs},
                    )
                    if current != witness["output_revision"]:
                        raise PolicyUnavailableError("Native output generation changed")
            decision_id = uuid4()
            frozen = [
                FrozenRaw.model_validate(
                    {
                        "raw_id": row["id"],
                        "source_revision": row["source_revision"],
                        "logical_source_digest": row["logical_source_digest"].hex(),
                        "content_digest": row["content_digest"].hex(),
                        "retention_at": row["retention_at"],
                        "accepted_request_id": row["accepted_request_id"],
                        "accepted_payload_digest": row["accepted_payload_digest"].hex(),
                        "accepted_normalized_digest": row["accepted_normalized_digest"].hex(),
                    }
                )
                for row in rows
            ]
            manifest = frozen_manifest(decision_id, run["policy_version"], run["cutoff"], frozen)
            await conn.execute(
                """INSERT INTO location_retention_plans
                   (decision_id,run_id,policy_version,cutoff,manifest_digest,state)
                   VALUES($1,$2,$3,$4,$5,'holder_pending')""",
                decision_id,
                run_id,
                run["policy_version"],
                run["cutoff"],
                manifest,
            )
            for row in frozen:
                await conn.execute(
                    """INSERT INTO location_retention_plan_rows
                       (decision_id,raw_id,source_revision,logical_source_digest,content_digest,
                        retention_at,accepted_request_id,accepted_payload_digest,
                        accepted_normalized_digest) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                    decision_id,
                    row.raw_id,
                    row.source_revision,
                    bytes.fromhex(row.logical_source_digest),
                    bytes.fromhex(row.content_digest),
                    row.retention_at,
                    row.accepted_request_id,
                    bytes.fromhex(row.accepted_payload_digest),
                    bytes.fromhex(row.accepted_normalized_digest),
                )
            outputs = await conn.fetch(
                """SELECT DISTINCT o.* FROM location_projection_outputs o
                   JOIN location_projection_heads h ON h.adapter_name=o.adapter_name
                     AND h.mapping_revision=o.mapping_revision
                   WHERE o.raw_id=ANY($1::uuid[]) ORDER BY o.raw_id,o.output_kind,o.output_id""",
                [row.raw_id for row in frozen],
            )
            for output in outputs:
                await conn.execute(
                    """INSERT INTO location_retention_plan_outputs
                       (decision_id,raw_id,source_revision,adapter_name,mapping_revision,
                        output_kind,output_id) VALUES($1,$2,$3,$4,$5,$6,$7)""",
                    decision_id,
                    output["raw_id"],
                    output["source_revision"],
                    output["adapter_name"],
                    output["mapping_revision"],
                    output["output_kind"],
                    output["output_id"],
                )
            episode_ids = sorted(
                {o["output_id"] for o in outputs if o["output_kind"] == "episode"}, key=str
            )
            episodes = await conn.fetch(
                "SELECT * FROM episodes WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
                episode_ids,
            )
            if {episode["id"] for episode in episodes} != set(episode_ids):
                raise PolicyUnavailableError("Native summary generation is unavailable")
            for episode in episodes:
                payload = episode["payload"]
                if episode["source_name"] == "owntracks.points" and "path_m" not in payload:
                    raise PolicyUnavailableError("Native movement metrics are unavailable")
                await conn.execute(
                    "UPDATE episodes SET payload=$2,title='Location summary',"
                    "updated_at=clock_timestamp() "
                    "WHERE id=$1",
                    episode["id"],
                    reduced_summary(payload),
                )
                await conn.execute(
                    """INSERT INTO location_summary_floors
                       (episode_id,decision_id,spatial_precision_m,spatial_scheme_version)
                       VALUES($1,$2,150,1) ON CONFLICT(episode_id) DO NOTHING""",
                    episode["id"],
                    decision_id,
                )
            await conn.execute(
                """UPDATE location_retention_runs SET status='pending',reason_code='holder_pending',
                   prepared_count=$2 WHERE run_id=$1""",
                run_id,
                len(rows),
            )
            return decision_id


async def observe_source_copy(pool: asyncpg.Pool, switchboard_client: Any) -> int:
    """Read one bounded owning source receipt after its separate owning commit.

    This closes ONLY the native skipped-source holder. It deliberately cannot
    certify receiver/session/bundle/carry/cache/prose holders or issue READY.
    A successful action response is never its own committed readback.
    """
    plans = await pool.fetch(
        "SELECT decision_id,manifest_digest FROM location_retention_plans "
        "WHERE state='holder_pending' ORDER BY prepared_at,decision_id LIMIT 8"
    )
    unknown = 0
    for plan in plans:
        decision = plan["decision_id"]
        manifest = plan["manifest_digest"]
        expected_count = await pool.fetchval(
            "SELECT count(*) FROM location_retention_plan_rows WHERE decision_id=$1", decision
        )
        try:
            await switchboard_client.call_tool(
                "owntracks_retention_source_forget", {"decision_id": str(decision)}
            )
            actual = await switchboard_client.call_tool(
                "owntracks_retention_source_receipt", {"decision_id": str(decision)}
            )
            receipt = source_copy_receipt(actual, decision, manifest, expected_count)
        except asyncio.CancelledError:
            raise
        except Exception:
            unknown += 1
            continue
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.fetchrow(
                    "SELECT * FROM location_retention_policy WHERE singleton FOR UPDATE"
                )
                locked = await conn.fetchrow(
                    "SELECT state,manifest_digest FROM location_retention_plans "
                    "WHERE decision_id=$1 FOR UPDATE",
                    decision,
                )
                if locked is None or locked["manifest_digest"] != manifest:
                    raise PolicyUnavailableError("Stored retention decision changed")
                prior = await conn.fetchrow(
                    "SELECT * FROM location_retention_holder_receipts WHERE decision_id=$1 "
                    "AND owning_butler='switchboard' AND holder_kind='switchboard_skipped'",
                    decision,
                )
                if prior is not None:
                    if (
                        prior["receipt_id"] != receipt
                        or prior["source_digest"] != manifest
                        or prior["holder_generation"] != decision
                    ):
                        raise PolicyUnavailableError("Owning source receipt changed")
                else:
                    await conn.execute(
                        "INSERT INTO location_retention_holder_receipts "
                        "(decision_id,owning_butler,holder_kind,holder_generation,"
                        "source_digest,receipt_id) "
                        "VALUES($1,'switchboard','switchboard_skipped',$1,$2,$3)",
                        decision,
                        manifest,
                        receipt,
                    )
        # Separate acquisition checks that our observation actually committed.
        stored = await pool.fetchval(
            "SELECT receipt_id FROM location_retention_holder_receipts WHERE decision_id=$1 "
            "AND owning_butler='switchboard' AND holder_kind='switchboard_skipped'",
            decision,
        )
        if stored != receipt:
            raise PolicyUnavailableError("Committed holder observation is unknown")
    return unknown


def source_copy_receipt(actual: Any, decision: UUID, manifest: bytes, expected_count: int) -> UUID:
    """Closed metadata binding, not authentication from a UUID or digest."""
    from datetime import datetime

    if not isinstance(actual, dict) or not 1 <= expected_count <= 256:
        raise ValueError("Owning source receipt is unavailable")
    try:
        committed = actual["committed_at"]
        if isinstance(committed, str):
            committed = datetime.fromisoformat(committed)
        if (
            not isinstance(committed, datetime)
            or committed.tzinfo is None
            or committed.utcoffset() is None
            or UUID(str(actual["decision_id"])) != decision
            or actual["manifest_digest"] != manifest.hex()
            or actual["source_kind"] != "switchboard_skipped"
            or type(actual["forgotten_count"]) is not int
            or actual["forgotten_count"] != expected_count
        ):
            raise ValueError
        return UUID(str(actual["receipt_id"]))
    except (ValueError, KeyError, TypeError):
        raise ValueError("Owning source receipt is unavailable") from None


async def retention_status(pool: asyncpg.Pool) -> dict[str, Any]:
    """Read actual latest durable attempt, not a fabricated default success."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM location_retention_runs "
            "ORDER BY attempt_started_at DESC,run_id DESC LIMIT 1"
        )
        now = await conn.fetchval("SELECT clock_timestamp()")
    if row is None:
        return {"status": "unknown", "reason_code": "no_attempt", "receipt": None}
    outcome = attempt_status(
        started_at=row["attempt_started_at"],
        lease_until=row["lease_until"],
        completed_at=row["completion_at"],
        outcome=row["status"],
        now=now,
    )
    return {
        "status": outcome,
        "reason_code": row["reason_code"],
        "receipt": str(row["run_id"]),
        "attempt_started_at": row["attempt_started_at"],
        "completion_at": row["completion_at"],
        "cutoff": row["cutoff"],
        "prepared_count": row["prepared_count"],
        "deleted_count": row["deleted_count"],
        "blocked_count": row["blocked_count"] if row["counts_observed_at"] else None,
        # No complete all-holder frontier has been implemented or observed.
        # The internal bounded-plan transport counter cannot stand in for it.
        "unknown_count": None,
        "overdue_count": row["overdue_count"],
        "holder_pending_count": row["holder_pending_count"],
        "counts_observed_at": row["counts_observed_at"],
    }


async def publish_conditions(pool: asyncpg.Pool, status: dict[str, Any]) -> None:
    """Count-only state, independent of delivery; never coordinates or raw refs."""
    from butlers.core.owner_conditions import Observation, compute_fingerprint, reconcile_snapshot

    blocked = status.get("blocked_count")
    known = type(blocked) is int and blocked >= 0
    observations = []
    if known and blocked:
        observations.append(
            Observation(
                fingerprint=compute_fingerprint(
                    source="chronicler:location-retention-projection-lag",
                    version=1,
                    identity_facts={"contract": "owntracks-retention-v1"},
                ),
                summary="Location retention is overdue while projection is incomplete",
                metadata={"overdue_count": blocked},
                identity_version=1,
            )
        )
    await reconcile_snapshot(
        pool,
        source="chronicler:location-retention-projection-lag",
        observations=observations,
        snapshot_complete=known,
        initial_grace_seconds=0,
    )
    unhealthy = status["status"] in {"failed", "cancelled", "unknown", "stale", "unavailable"}
    await reconcile_snapshot(
        pool,
        source="chronicler:location-retention-failure",
        observations=(
            [
                Observation(
                    fingerprint=compute_fingerprint(
                        source="chronicler:location-retention-failure",
                        version=1,
                        identity_facts={"contract": "owntracks-retention-v1"},
                    ),
                    summary="Location retention could not be confirmed",
                    metadata={"status": status["status"]},
                    identity_version=1,
                )
            ]
            if unhealthy
            else []
        ),
        snapshot_complete=True,
        initial_grace_seconds=0,
    )


async def run_retention(pool: asyncpg.Pool, *, switchboard_client: Any = None) -> dict[str, Any]:
    """Real deterministic entry: durable start before any preparation mutation.

    Copy-holder closure is mandatory. Until its native receipts exist this
    entry leaves plans pending; it cannot turn a deadline or an empty source
    query into a successful deletion receipt.
    """
    run_id = await start_attempt(pool)
    try:
        await prepare_batch(pool, run_id)
        if switchboard_client is not None:
            unknown = await observe_source_copy(pool, switchboard_client)
            await pool.execute(
                "UPDATE location_retention_runs SET unknown_count=$2 WHERE run_id=$1",
                run_id,
                unknown,
            )
        # Missing transport leaves an unknown holder frontier, not a zero count.
        # No READY transition until every other native holder is closed.
    except asyncio.CancelledError:
        try:
            await asyncio.shield(fail_attempt(pool, run_id, cancelled=True))
        except Exception:
            logger.warning("location_retention_completion_unavailable")
        raise
    except Exception:
        try:
            await fail_attempt(pool, run_id)
        except Exception:
            logger.warning("location_retention_completion_unavailable")
        # Persisted closed reason is the only error detail exposed here.
        logger.warning("location_retention_attempt_failed")
    status = await retention_status(pool)
    await publish_conditions(pool, status)
    return status
