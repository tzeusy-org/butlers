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

from butlers.chronicler.location_copy_pools import (
    _api_copy_pools as _api_copy_pools,
)
from butlers.chronicler.location_copy_pools import (
    _copy_pools as _copy_pools,
)
from butlers.chronicler.location_copy_pools import (
    native_copy_pool as native_copy_pool,
)
from butlers.chronicler.location_copy_pools import (
    register_api_copy_pool as register_api_copy_pool,
)
from butlers.chronicler.location_copy_pools import (
    register_native_copy_pool as register_native_copy_pool,
)
from butlers.chronicler.location_copy_pools import (
    unregister_native_copy_pool as unregister_native_copy_pool,
)
from butlers.chronicler.location_policy import (
    PolicyConflictError as PolicyConflictError,
)
from butlers.chronicler.location_policy import (
    PolicyUnavailableError as PolicyUnavailableError,
)
from butlers.chronicler.location_policy import (
    _policy as _policy,
)
from butlers.chronicler.location_policy import (
    read_policy as read_policy,
)
from butlers.location_retention import ADAPTER_NAMES, attempt_status, reduced_summary, strict_days

logger = logging.getLogger(__name__)


def _api_capture_configured(pool: Any) -> bool:
    if pool in _api_copy_pools:
        return True
    if isinstance(pool, asyncpg.Pool):
        raise PolicyUnavailableError("Owning API source is not configured")
    # Existing unconfigured non-database adapters/doubles retain their reader
    # contract. They provide no birth, closure, role or SQL proof.
    return False


class PolicyUpdate(BaseModel):
    """Closed owner-control wire: no caller actor, cutoff, provider or grant."""

    model_config = ConfigDict(extra="forbid")
    days: StrictInt = Field(ge=1, le=30)
    expected_version: StrictInt = Field(gt=0, le=2**63 - 1)


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
                   WHERE p.state IN ('ready','raw_unknown')
                   ORDER BY p.prepared_at,p.decision_id LIMIT 8
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

    catalog_loans = await pool.fetch(
        "SELECT DISTINCT l.*,g.artifact_generation,"
        "(b.exclusive_input AND NOT EXISTS("
        "SELECT 1 FROM location_native_copy_births c "
        "JOIN location_native_dispatch_parents i USING(copy_generation,input_digest) "
        "WHERE i.input_generation=a.input_generation AND NOT EXISTS("
        "SELECT 1 FROM location_retention_plan_outputs p WHERE p.decision_id=$1 "
        "AND p.output_kind=c.output_kind AND p.output_id=c.output_id))) AS complete_input "
        "FROM location_native_catalog_loans l "
        "JOIN location_native_catalog_generations g USING(source_generation) "
        "JOIN location_native_memory_artifacts a USING(artifact_generation) "
        "JOIN location_native_memory_bundles b USING(input_generation) "
        "JOIN location_native_dispatch_parents i USING(input_generation) "
        "JOIN location_native_copy_births c USING(copy_generation,input_digest) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "WHERE p.decision_id=$1 ORDER BY l.loan_id",
        decision_id,
    )
    return {
        **dict(row),
        "manifest_digest": (
            await pool.fetchval(
                "SELECT manifest_digest FROM location_retention_plans WHERE decision_id=$1",
                decision_id,
            )
        ).hex(),
        "catalog_loans": [
            {
                key: loan[key]
                if key == "complete_input"
                else loan[key].hex()
                if isinstance(loan[key], bytes)
                else str(loan[key])
                for key in (
                    "loan_id",
                    "source_generation",
                    "receiver_name",
                    "receiving_incarnation",
                    "body_digest",
                    "complete_input",
                )
            }
            for loan in catalog_loans
        ],
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


async def _output_generation_cohort(
    conn: asyncpg.Connection, episode_ids: list[UUID], event_ids: list[UUID]
) -> list:
    """Freeze every real complete contributor before a monotone privacy write.

    Called under policy/ordered adapter locks. A later bounded plan must inherit
    the legitimate reduction without blessing unrelated edits. Original native
    lineage and previous frozen plans remain unchanged.
    """
    from butlers.chronicler.location_projection import output_digest

    cohort = await conn.fetch(
        """SELECT c.* FROM location_projection_coverage c
           WHERE c.disposition IN ('complete','terminal_no_output') AND EXISTS(
             SELECT 1 FROM location_projection_outputs o
             WHERE o.raw_id=c.raw_id AND o.source_revision=c.source_revision
               AND o.adapter_name=c.adapter_name AND o.mapping_revision=c.mapping_revision
               AND ((o.output_kind='episode' AND o.output_id=ANY($1::uuid[]))
                 OR (o.output_kind='point_event' AND o.output_id=ANY($2::uuid[]))))
           ORDER BY c.raw_id,c.source_revision,c.adapter_name,c.mapping_revision FOR UPDATE""",
        episode_ids,
        event_ids,
    )
    for contributor in cohort:
        outputs = await _contributor_outputs(conn, contributor)
        if await output_digest(conn, outputs) != contributor["output_revision"]:
            raise PolicyUnavailableError("Native output generation changed")
    return list(cohort)


async def _contributor_outputs(conn: asyncpg.Connection, contributor: Any) -> set:
    rows = await conn.fetch(
        """SELECT output_kind,output_id FROM location_projection_outputs
           WHERE raw_id=$1 AND source_revision=$2 AND adapter_name=$3
             AND mapping_revision=$4 ORDER BY output_kind,output_id""",
        contributor["raw_id"],
        contributor["source_revision"],
        contributor["adapter_name"],
        contributor["mapping_revision"],
    )
    return {(row["output_kind"], row["output_id"]) for row in rows}


async def _commit_privacy_generations(
    conn: asyncpg.Connection,
    decision_id: UUID,
    cohort: list,
    *,
    phase: str = "coarsen",
) -> None:
    """Own reductions have separate immutable coarsening/disposal generations."""
    if phase not in {"coarsen", "dispose"}:
        raise ValueError("Unregistered privacy transition")
    from butlers.chronicler.location_projection import output_digest

    for contributor in cohort:
        reduced = await output_digest(conn, await _contributor_outputs(conn, contributor))
        previous = contributor["output_revision"]
        if previous == reduced:
            continue
        keys = (
            contributor["raw_id"],
            contributor["source_revision"],
            contributor["adapter_name"],
            contributor["mapping_revision"],
        )
        await conn.execute(
            """INSERT INTO location_projection_privacy_transitions
               (decision_id,raw_id,source_revision,adapter_name,mapping_revision,
                phase,previous_revision,reduced_revision) VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
            decision_id,
            *keys,
            phase,
            previous,
            reduced,
        )
        updated = await conn.fetchval(
            """UPDATE location_projection_coverage SET output_revision=$5
               WHERE raw_id=$1 AND source_revision=$2 AND adapter_name=$3
                 AND mapping_revision=$4 AND output_revision=$6 RETURNING raw_id""",
            *keys,
            reduced,
            previous,
        )
        if updated != contributor["raw_id"]:
            raise PolicyUnavailableError("Native output generation changed")


async def _record_local_preparation(
    conn: asyncpg.Connection,
    decision_id: UUID,
    manifest: bytes,
) -> None:
    """Own coarsening receipt, never evidence of raw point/all-holder disposal.

    Point-event DELETE/tombstones must wait for genuine all-holder closure.
    They cannot run merely because summaries and this receipt have committed.
    """
    await conn.execute(
        """INSERT INTO location_retention_local_receipts
           (decision_id,manifest_digest,receipt_id,removed_event_count) VALUES($1,$2,$3,0)""",
        decision_id,
        manifest,
        uuid4(),
    )


async def read_local_receipt(pool: asyncpg.Pool, decision_id: UUID) -> dict[str, Any] | None:
    """Separate acquisition reads actual own preparation COMMIT, not all copies."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM location_retention_local_receipts WHERE decision_id=$1",
            decision_id,
        )
    return dict(row) if row is not None else None


async def prepare_batch(pool: asyncpg.Pool, run_id: UUID) -> UUID | None:
    """Preserve original preparation outcome, with a closed failure diagnostic."""
    try:
        return await _prepare_batch(pool, run_id)
    except Exception as exc:
        from butlers.chronicler.location_policy import closed_failure

        category, label, state = closed_failure(exc)
        logger.warning(
            "Location preparation failure stage=preparation category=%s sqlstate=%s class=%s",
            category,
            state,
            label,
        )
        raise  # Diagnosis never changes refusal, original exception or transaction outcome.


async def _prepare_batch(pool: asyncpg.Pool, run_id: UUID) -> UUID | None:
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
            event_ids = sorted(
                {o["output_id"] for o in outputs if o["output_kind"] == "point_event"},
                key=str,
            )
            generation_cohort = await _output_generation_cohort(conn, episode_ids, event_ids)
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
            await _record_local_preparation(conn, decision_id, manifest)
            await _commit_privacy_generations(conn, decision_id, generation_cohort)
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
    known = (
        type(blocked) is int
        and blocked >= 0
        and (
            blocked > 0
            or (status.get("unknown_count") == 0 and status.get("holder_pending_count") == 0)
        )
    )
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
    unhealthy = status["status"] in {"failed", "cancelled", "unknown", "stale", "unavailable"} or (
        status["status"] == "pending" and status.get("unknown_count") is None
    )
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
        await reconcile_raw_batches(pool)
        await classify_legacy_caches(pool)
        await prepare_batch(pool, run_id)
        if switchboard_client is not None:
            unknown = await observe_source_copy(pool, switchboard_client)
            await pool.execute(
                "UPDATE location_retention_runs SET unknown_count=$2 WHERE run_id=$1",
                run_id,
                unknown,
            )
        # Actual downstream stages are reached, but the unknown frontier
        # remains fail closed. A local/source receipt cannot synthesize it.
        decisions = await pool.fetch(
            "SELECT decision_id FROM location_retention_plans "
            "WHERE state='holder_pending' ORDER BY prepared_at,decision_id LIMIT 8"
        )
        for decision in decisions:
            from butlers.chronicler.location_memory_context import dispose_own_contexts

            await dispose_own_contexts(pool, decision["decision_id"])
            await dispose_bound_native_copies(pool, decision["decision_id"])
            from butlers.chronicler.location_catalog_copies import (
                dispose_catalog_artifacts,
                reconcile_catalog_loans,
            )
            from butlers.chronicler.location_memory_copies import dispose_native_memory

            await reconcile_catalog_loans(pool, decision["decision_id"])
            await dispose_catalog_artifacts(pool, decision["decision_id"])
            await dispose_native_memory(pool, decision["decision_id"])
            await observe_native_api_copies(pool, decision["decision_id"])
            await seal_native_frontier(pool, decision["decision_id"])
            disposed = await dispose_ready_point_evidence(pool, decision["decision_id"])
            if disposed is not None:
                await issue_ready_grant(pool, decision["decision_id"], disposed)
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
    # Native committed privacy changes notify the managed dashboard. This
    # content-blind wake is not a disposal ACK or source coverage verdict.
    try:
        from butlers.fleet_events import publish_fleet_event

        revision = await pool.fetchval(
            "SELECT count(*) FROM location_projection_privacy_transitions"
        )
        if type(revision) is not int or revision < 0:
            raise PolicyUnavailableError("Committed privacy revision is unknown")
        await publish_fleet_event(pool, "chronicles", {"privacy_revision": str(revision)})
    except Exception:
        logger.warning("location_retention_dashboard_notification_unavailable")
    await publish_conditions(pool, status)
    return status


async def _current_cache_bindings(conn: Any) -> bool:
    """Current persisted bodies must still equal their producer-owned heads."""
    from butlers.chronicler.location_projection import _digest_value
    from butlers.location_retention import content_digest

    heads = await conn.fetch(
        "SELECT h.cache_key,h.body_digest FROM location_native_cache_heads h ORDER BY h.cache_key"
    )
    for head in heads:
        body = await conn.fetchrow(
            "SELECT * FROM tier2_cache WHERE cache_key=$1", head["cache_key"]
        )
        if body is None or head["body_digest"] != content_digest(
            {"cache": _digest_value(dict(body))}
        ):
            return False
    return True


async def _all_committed_holders(conn: asyncpg.Connection, plan: Any) -> Any | None:
    """Require a nonempty native sealed cohort and exact committed readbacks.

    A native producer/transport must populate this ledger from its actual
    current inventory. No caller object, source-kind partial receipt or an
    empty list is a substitute. The producer binding remains an installation
    obligation; this predicate cannot manufacture it by inspecting rows.
    """
    frontier = await conn.fetchrow(
        "SELECT * FROM location_retention_frontiers WHERE decision_id=$1 FOR UPDATE",
        plan["decision_id"],
    )
    if frontier is None:
        return None
    if (
        frontier["manifest_digest"] != plan["manifest_digest"]
        or frontier["producer_contract"] != 1
        or frontier["expected_count"] <= 0
    ):
        raise PolicyUnavailableError("Holder frontier changed")
    # A copy born after the sealed snapshot must be in that exact cohort too.
    # This query uses actual producer-captured output lineage, not a citation
    # or temporal overlap. Missing receiving-holder closure remains unknown.
    unbound = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_copy_births b "
        "JOIN location_retention_plan_outputs o USING(output_kind,output_id) "
        "WHERE o.decision_id=$1 AND (NOT b.lineage_known OR NOT EXISTS ("
        "SELECT 1 FROM location_retention_frontier_holders h "
        "WHERE h.decision_id=$1 AND h.owning_butler='chronicler' "
        "AND h.holder_kind=CASE WHEN b.producer_kind='api_export' THEN 'api_server' "
        "ELSE 'mcp_output' END AND h.holder_generation=b.copy_generation "
        "AND h.source_digest=b.input_digest)))",
        plan["decision_id"],
    )
    if unbound:
        return None
    # ASGI completion closes only the source-owned response lifetime. It does
    # not authenticate or erase a browser/user download (outside this cohort).
    # Legacy prose has no causal attribution until its exact local body is
    # replaced by a genuinely bound native generation. Neither can be waived
    # by supplying a frontier row or by dropping an FK-linked point event.
    unknown_prose = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_legacy_cache_observations o "
        "WHERE NOT EXISTS(SELECT 1 FROM location_legacy_cache_replacements r "
        "WHERE r.observation_id=o.observation_id)) OR EXISTS("
        "SELECT 1 FROM location_native_cache_exports e WHERE NOT EXISTS("
        "SELECT 1 FROM location_native_api_dispositions d "
        "WHERE d.copy_generation=e.copy_generation "
        "AND d.server_request=e.receiving_server_request AND d.body_digest=e.body_digest "
        "AND d.producer_kind='cache') AND (e.cache_generation IS NULL "
        "OR EXISTS(SELECT 1 FROM location_native_cache_inputs i "
        "JOIN location_native_copy_births b USING(copy_generation) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "WHERE i.cache_key=e.cache_key AND i.cache_generation=e.cache_generation "
        "AND p.decision_id=$1)))",
        plan["decision_id"],
    )
    if unknown_prose or not await _current_cache_bindings(conn):
        return None
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_memory_parents m "
        "JOIN location_native_copy_births b USING(copy_generation,input_digest) "
        "JOIN location_retention_plan_outputs o USING(output_kind,output_id) "
        "WHERE o.decision_id=$1 AND NOT EXISTS("
        "SELECT 1 FROM location_native_memory_dispositions d "
        "WHERE d.reservation_id=m.reservation_id))",
        plan["decision_id"],
    ):
        return None
    from butlers.chronicler.location_catalog_copies import (
        catalog_frontier_closed,
        catalog_holder_inventory,
    )

    if not await catalog_frontier_closed(conn, plan["decision_id"]):
        return None
    holders = await conn.fetch(
        "SELECT h.*,r.receipt_id FROM location_retention_frontier_holders h "
        "LEFT JOIN location_retention_holder_receipts r USING "
        "(decision_id,owning_butler,holder_kind,holder_generation,source_digest) "
        "WHERE h.decision_id=$1 ORDER BY owning_butler,holder_kind,holder_generation",
        plan["decision_id"],
    )
    if len(holders) != frontier["expected_count"] or any(
        holder["receipt_id"] is None for holder in holders
    ):
        return None
    # A later native generation/loan must not hide behind an earlier seal.
    sealed = {
        (h["owning_butler"], h["holder_kind"], h["holder_generation"], h["source_digest"])
        for h in holders
    }
    catalog = await catalog_holder_inventory(conn, plan["decision_id"])
    if any(
        h["receipt_id"] is None
        or (h["owning_butler"], h["holder_kind"], h["holder_generation"], h["source_digest"])
        not in sealed
        for h in catalog
    ):
        return None
    return frontier


async def dispose_ready_point_evidence(pool: asyncpg.Pool, decision_id: UUID) -> UUID | None:
    """Own exact point disposal and then a separate committed readback.

    This is not the frontier producer: until real native capture/transport is
    installed and proves a full current cohort, it never deletes or grants.
    Existing local coarsening receipts remain immutable and distinct. Only
    this own transaction creates minimal tombstones and disposition receipts.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Owning writer identity differs")
            await conn.execute("SET LOCAL lock_timeout='2s'")
            await conn.execute("SET LOCAL statement_timeout='5s'")
            _policy(
                await conn.fetchrow(
                    "SELECT * FROM location_retention_policy WHERE singleton FOR UPDATE"
                )
            )
            for name in ADAPTER_NAMES:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", name)
            plan = await conn.fetchrow(
                "SELECT * FROM location_retention_plans WHERE decision_id=$1 FOR UPDATE",
                decision_id,
            )
            if plan is None:
                raise PolicyUnavailableError("Stored retention decision is unavailable")
            prior = await conn.fetchrow(
                "SELECT * FROM location_retention_disposal_receipts WHERE decision_id=$1",
                decision_id,
            )
            if prior is not None:
                if prior["manifest_digest"] != plan["manifest_digest"]:
                    raise PolicyUnavailableError("Committed disposal changed")
                receipt_id = prior["receipt_id"]
            else:
                if plan["state"] != "holder_pending":
                    raise PolicyUnavailableError("Stored retention transition differs")
                frontier = await _all_committed_holders(conn, plan)
                if frontier is None:
                    return None
                members = await conn.fetch(
                    "SELECT * FROM location_retention_plan_rows "
                    "WHERE decision_id=$1 ORDER BY raw_id",
                    decision_id,
                )
                if not 1 <= len(members) <= 256:
                    raise PolicyUnavailableError("Stored retention members differ")
                # No independently existing event/episode may authorize deletion.
                # Typed native output lineage and all three current heads bind
                # each exact raw revision before any own evidence disappears.
                for member in members:
                    count = await conn.fetchval(
                        "SELECT count(DISTINCT c.adapter_name) FROM location_projection_coverage c "
                        "JOIN location_projection_heads h USING(adapter_name,mapping_revision) "
                        "WHERE c.raw_id=$1 AND c.source_revision=$2 "
                        "AND c.logical_source_digest=$3 AND c.content_digest=$4 "
                        "AND c.adapter_name=ANY($5::text[]) "
                        "AND c.disposition IN ('complete','terminal_no_output')",
                        member["raw_id"],
                        member["source_revision"],
                        member["logical_source_digest"],
                        member["content_digest"],
                        list(ADAPTER_NAMES),
                    )
                    if count != len(ADAPTER_NAMES):
                        raise PolicyUnavailableError("Native coverage changed")
                outputs = await conn.fetch(
                    "SELECT DISTINCT o.output_id,r.raw_id,r.source_revision,"
                    "r.logical_source_digest "
                    "FROM location_retention_plan_outputs o JOIN location_retention_plan_rows r "
                    "USING(decision_id,raw_id,source_revision) "
                    "WHERE o.decision_id=$1 AND o.output_kind='point_event' ORDER BY o.output_id",
                    decision_id,
                )
                event_ids = [row["output_id"] for row in outputs]
                if len(set(event_ids)) != len(event_ids):
                    raise PolicyUnavailableError("Native evidence lineage is ambiguous")
                events = await conn.fetch(
                    "SELECT * FROM point_events WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
                    event_ids,
                )
                by_id = {row["id"]: row for row in events}
                if set(by_id) != set(event_ids) or any(
                    row["source_name"] != "owntracks.points" for row in events
                ):
                    raise PolicyUnavailableError("Native point generation is unavailable")
                cohort = await _output_generation_cohort(conn, [], event_ids)
                # The digest comparison above positions unrelated-edit refusal;
                # each minimal tombstone replaces only an exact native event.
                for output in outputs:
                    event = by_id[output["output_id"]]
                    await conn.execute(
                        "INSERT INTO location_evidence_tombstones "
                        "(event_id,raw_id,source_revision,logical_source_digest,decision_id,"
                        "spatial_precision_m,spatial_scheme_version,occurred_at,privacy) "
                        "VALUES($1,$2,$3,$4,$5,150,1,$6,$7)",
                        event["id"],
                        output["raw_id"],
                        output["source_revision"],
                        output["logical_source_digest"],
                        decision_id,
                        event["occurred_at"],
                        event["privacy"],
                    )
                await conn.execute(
                    "INSERT INTO location_expired_evidence_links(episode_id,event_id,relation) "
                    "SELECT episode_id,event_id,relation FROM episode_event_links "
                    "WHERE event_id=ANY($1::uuid[])",
                    event_ids,
                )
                removed = await conn.fetch(
                    "DELETE FROM point_events WHERE id=ANY($1::uuid[]) RETURNING id",
                    event_ids,
                )
                if {row["id"] for row in removed} != set(event_ids):
                    raise PolicyUnavailableError("Native point disposal differs")
                # Point bodies now resolve to minimal tombstones; advance every
                # genuine contributor through the same monotone generation log.
                await _commit_privacy_generations(conn, decision_id, cohort, phase="dispose")
                receipt_id = uuid4()
                await conn.execute(
                    "INSERT INTO location_retention_disposal_receipts "
                    "(decision_id,manifest_digest,frontier_generation,receipt_id,"
                    "removed_event_count) "
                    "VALUES($1,$2,$3,$4,$5)",
                    decision_id,
                    plan["manifest_digest"],
                    frontier["frontier_generation"],
                    receipt_id,
                    len(removed),
                )
    async with pool.acquire() as committed:
        stored = await committed.fetchrow(
            "SELECT * FROM location_retention_disposal_receipts WHERE decision_id=$1",
            decision_id,
        )
    if stored is None or stored["receipt_id"] != receipt_id:
        raise PolicyUnavailableError("Committed point disposal is unknown")
    return receipt_id


async def issue_ready_grant(pool: asyncpg.Pool, decision_id: UUID, disposal_receipt: UUID) -> UUID:
    """Consume separately committed own disposal, then commit the fixed raw claim.

    A caller receipt UUID is only a locator: stored manifest/current frontier
    and own disposition are rechecked under policy/adapter/decision locks.
    The scheduled producer calls this only after its separate disposal read.
    Complete native cohort production/readback remains mandatory; a partial
    source receipt or absent frontier can never reach this transition.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Owning writer identity differs")
            _policy(
                await conn.fetchrow(
                    "SELECT * FROM location_retention_policy WHERE singleton FOR UPDATE"
                )
            )
            for name in ADAPTER_NAMES:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", name)
            plan = await conn.fetchrow(
                "SELECT * FROM location_retention_plans WHERE decision_id=$1 FOR UPDATE",
                decision_id,
            )
            if plan is None or plan["state"] not in {"holder_pending", "ready"}:
                raise PolicyUnavailableError("Stored retention transition differs")
            frontier = await _all_committed_holders(conn, plan)
            disposed = await conn.fetchrow(
                "SELECT * FROM location_retention_disposal_receipts WHERE decision_id=$1",
                decision_id,
            )
            if (
                frontier is None
                or disposed is None
                or disposed["receipt_id"] != disposal_receipt
                or disposed["manifest_digest"] != plan["manifest_digest"]
                or disposed["frontier_generation"] != frontier["frontier_generation"]
            ):
                raise PolicyUnavailableError("Committed holder disposal is unavailable")
            prior = await conn.fetchrow(
                "SELECT * FROM location_retention_grants WHERE decision_id=$1", decision_id
            )
            if prior is None:
                if plan["state"] != "holder_pending":
                    raise PolicyUnavailableError("Stored ready claim is unavailable")
                grant_id = uuid4()
                await conn.execute(
                    "INSERT INTO location_retention_grants "
                    "(grant_id,batch_id,decision_id,manifest_digest,lease_version,lease_until) "
                    "VALUES($1,$2,$3,$4,1,clock_timestamp()+interval '120 seconds')",
                    grant_id,
                    uuid4(),
                    decision_id,
                    plan["manifest_digest"],
                )
                await conn.execute(
                    "UPDATE location_retention_plans SET state='ready' WHERE decision_id=$1",
                    decision_id,
                )
            else:
                if prior["manifest_digest"] != plan["manifest_digest"] or plan["state"] != "ready":
                    raise PolicyUnavailableError("Committed ready claim differs")
                grant_id = prior["grant_id"]
    async with pool.acquire() as committed:
        stored = await committed.fetchval(
            "SELECT grant_id FROM location_retention_grants WHERE decision_id=$1", decision_id
        )
    if stored != grant_id:
        raise PolicyUnavailableError("Committed ready claim is unknown")
    return grant_id


async def capture_native_read(pool: asyncpg.Pool, kind: str, reader: Any) -> list[Any]:
    """Native tool producer captures actual input before response serialization.

    The private caller supplies its fixed owning SQL reader, never a model
    citation or asserted actor. Input bytes are represented only by a digest;
    typed output IDs come from the actual returned rows. This is copy birth,
    not receiving-holder authority or a terminal disposition. An unbound
    recipient remains an outstanding holder until genuine own readback.
    """
    if not native_copy_pool(pool):
        if isinstance(pool, asyncpg.Pool):
            return await _read_unconfigured(pool, kind, reader)
        return await reader(pool)
    return await _capture_read(pool, kind, reader, api_export=False)


async def capture_api_read(pool: asyncpg.Pool, kind: str, reader: Any) -> list[Any]:
    try:
        return await _capture_api_read(pool, kind, reader)
    except Exception as exc:
        from butlers.chronicler.location_policy import closed_failure

        category, error_class, state = closed_failure(exc)
        logger.warning(
            "Location API read failure stage=owning_api_read category=%s sqlstate=%s class=%s",
            category,
            state,
            error_class,
        )
        raise


async def _capture_api_read(pool: asyncpg.Pool, kind: str, reader: Any) -> list[Any]:
    """Fixed API reader captures its own export; no recipient authority.

    API-managed pools deliberately retain their existing database identity.
    This records actual bytes/typed IDs on that same pool before emission; it
    never enrolls the API as a receiving runtime or creates a disposal receipt.
    Actual native ASGI completion can settle only the source-owned response
    lifetime, never the remote recipient. An unbound/active server copy stays
    held. A failed birth
    COMMIT/readback refuses emission instead of silently producing a copy.
    """
    if isinstance(pool, asyncpg.Pool) and (
        pool not in _api_copy_pools
        or await pool.fetchval("SELECT current_schema()") != "chronicler"
    ):
        # The legacy ordinary reader may own an inline/public schema. Pool
        # registration does not turn that schema into Chronicle's producer.
        # Actual persisted source/ancestry classification still refuses any
        # location input before emission; no copy birth or terminal proof.
        return await _read_unconfigured(pool, kind, reader)
    if not _api_capture_configured(pool):
        return await reader(pool)
    return await _capture_read(pool, kind, reader, api_export=True)


async def _read_unconfigured(pool: Any, kind: str, reader: Any) -> list[Any]:
    """Actual ordinary own rows only; no enrollment, birth or disposal credit."""
    if kind not in {"episode", "point_event"}:
        raise ValueError("Unregistered native read producer")
    rows = await reader(pool)
    ids = [row.id if hasattr(row, "id") else row["id"] for row in rows]
    native = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_projection_outputs "
        "WHERE output_kind=$1 AND output_id=ANY($2::uuid[])) OR EXISTS("
        "SELECT 1 FROM location_native_copy_births "
        "WHERE output_kind=$1 AND output_id=ANY($2::uuid[]))",
        kind,
        ids,
    )
    if native or any(
        (row.source_name if hasattr(row, "source_name") else row["source_name"]).startswith(
            "owntracks."
        )
        for row in rows
    ):
        raise PolicyUnavailableError("Owning native source is not configured")
    return rows


async def _capture_read(
    pool: asyncpg.Pool, kind: str, reader: Any, *, api_export: bool
) -> list[Any]:
    from dataclasses import asdict, is_dataclass

    from butlers.chronicler.location_projection import _digest_value
    from butlers.core.copy_lifetime import _current_copy_invocation
    from butlers.location_retention import content_digest

    if kind not in {"point_event", "episode"}:
        raise ValueError("Unregistered native read producer")

    def value(row, name):
        return getattr(row, name) if is_dataclass(row) else row[name]

    # Source-owned processing copies begin at the actual SELECT, not only
    # serialization. Lock policy/adapter producers before reading any bodies.
    async with pool.acquire() as conn:
        async with conn.transaction():
            _policy(
                await conn.fetchrow(
                    "SELECT * FROM location_retention_policy WHERE singleton FOR UPDATE"
                )
            )
            for name in ADAPTER_NAMES:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", name)
            if api_export:
                if await conn.fetchval("SELECT current_schema()") != "chronicler":
                    raise PolicyUnavailableError("Owning export schema differs")
            elif await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Owning copy writer identity differs")
            rows = await reader(conn)
            ids = [UUID(str(value(row, "id"))) for row in rows]
            native = await conn.fetch(
                "SELECT DISTINCT o.output_id FROM location_projection_outputs o "
                "JOIN location_projection_coverage c "
                "USING(raw_id,source_revision,adapter_name,mapping_revision) "
                "WHERE o.output_kind=$1 AND o.output_id=ANY($2::uuid[])",
                kind,
                ids,
            )
            native_ids = {row["output_id"] for row in native}
            copies = [
                row
                for row in rows
                if UUID(str(value(row, "id"))) in native_ids
                or value(row, "source_name").startswith("owntracks.")
            ]
            if not copies:
                return rows
            digest = content_digest(
                {
                    "native_input": _digest_value(
                        [asdict(row) if is_dataclass(row) else dict(row) for row in copies]
                    )
                }
            )
            # Sealing and a later point DELETE are separate commits. The
            # same producer fence prevents a new exact-evidence copy in that
            # interval; late writers cannot invalidate an immutable snapshot.
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_retention_plan_outputs p "
                "JOIN location_retention_frontiers f USING(decision_id) "
                "JOIN location_retention_plans d USING(decision_id) "
                "WHERE p.output_kind=$1 AND p.output_id=ANY($2::uuid[]) "
                "AND d.state<>'complete')",
                kind,
                [UUID(str(value(row, "id"))) for row in copies],
            ):
                raise PolicyUnavailableError("Native source disposal is in progress")
            generation = uuid4()
            from butlers.chronicler.location_export_lifetime import (
                native_export_request,
                register_native_export,
            )

            server_request = native_export_request() if api_export else None
            if api_export and server_request is None:
                raise PolicyUnavailableError("Owning export lifetime is unavailable")
            invocation = _current_copy_invocation.get()
            try:
                receiving_session = (
                    UUID(invocation.runtime_session)
                    if not api_export
                    and invocation is not None
                    and invocation.target == "chronicler"
                    else None
                )
            except (TypeError, ValueError):
                receiving_session = None
            for row in copies:
                await conn.execute(
                    "INSERT INTO location_native_copy_births "
                    "(copy_generation,output_kind,output_id,input_digest,lineage_known,"
                    "receiving_session,exclusive_input,producer_kind,receiving_server_request) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                    generation,
                    kind,
                    UUID(str(value(row, "id"))),
                    digest,
                    UUID(str(value(row, "id"))) in native_ids,
                    receiving_session,
                    len(copies) == len(rows),
                    "api_export" if api_export else "native_mcp",
                    server_request,
                )
            if api_export:
                register_native_export(pool, "native_read", generation, digest)
    # An ACK is insufficient. Unknown commit never emits the untracked bytes.
    async with pool.acquire() as committed:
        count = await committed.fetchval(
            "SELECT count(*) FROM location_native_copy_births "
            "WHERE copy_generation=$1 AND input_digest=$2",
            generation,
            digest,
        )
    if count != len(copies):
        raise PolicyUnavailableError("Committed copy birth is unknown")
    return rows


async def lock_native_session_completion(conn: Any, session_id: UUID) -> bool:
    """Own source-registered pool only; never a caller session-string authority.

    Policy precedes the session UPDATE. A prior actual copy disposition fences
    late result/tool-trace persistence; ordinary unrelated completions survive.
    """
    from butlers.chronicler.storage import _lock_location_writes

    if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
        raise PolicyUnavailableError("Owning completion writer identity differs")
    await _lock_location_writes(conn)
    return bool(
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_copy_dispositions "
            "WHERE receiving_session=$1)",
            session_id,
        )
    )


async def bind_native_cache_inputs(conn: Any, cache_key: str, native_result: Any) -> None:
    """Actual scheduler result binds persisted prose to native captured input.

    Legacy dicts/citations retain compatibility but establish no lineage.
    Only the source-produced SpawnerResult and actual stored copy births can
    bind this writer. A disposed input never produces a new cached copy.
    """
    from butlers.chronicler.location_input_binding import (
        native_result as native_result_is_registered,
    )

    if not native_result_is_registered(native_result) or native_result.session_id is None:
        return
    session_id = native_result.session_id
    births = await conn.fetch(
        "SELECT DISTINCT copy_generation FROM location_native_copy_births "
        "WHERE receiving_session=$1",
        session_id,
    )
    if not births:
        return
    if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
        raise PolicyUnavailableError("Owning cache writer identity differs")
    if await lock_native_session_completion(conn, session_id):
        raise PolicyUnavailableError("Native cache input has been disposed")
    # The caller has written this exact body in the same owning transaction.
    # Digest the persisted body, not citations or an asserted input context.
    from butlers.chronicler.location_projection import _digest_value
    from butlers.location_retention import content_digest

    cache = await conn.fetchrow(
        "SELECT * FROM tier2_cache WHERE cache_key=$1 FOR UPDATE", cache_key
    )
    if cache is None:
        raise PolicyUnavailableError("Native cache body is unavailable")
    digest = content_digest({"cache": _digest_value(dict(cache))})
    generation = uuid4()
    await conn.execute(
        "INSERT INTO location_native_cache_heads(cache_key,cache_generation,body_digest) "
        "VALUES($1,$2,$3) ON CONFLICT(cache_key) DO UPDATE SET "
        "cache_generation=EXCLUDED.cache_generation,body_digest=EXCLUDED.body_digest",
        cache_key,
        generation,
        digest,
    )
    for birth in births:
        await conn.execute(
            "INSERT INTO location_native_cache_inputs(cache_key,cache_generation,copy_generation) "
            "VALUES($1,$2,$3)",
            cache_key,
            generation,
            birth["copy_generation"],
        )


async def observe_legacy_cache(conn: Any, cache_key: str) -> UUID | None:
    """Classify the actual current body before export/replacement, never by refs.

    Legacy prose has no authenticated input lineage. Its opaque observation
    remains unknown until the actual owning writer replaces that exact body;
    matching a title/citation/window does not assign it to a raw source.
    """
    from butlers.chronicler.location_projection import _digest_value
    from butlers.location_retention import content_digest

    cache = await conn.fetchrow(
        "SELECT * FROM tier2_cache WHERE cache_key=$1 FOR UPDATE", cache_key
    )
    if cache is None:
        return None
    head = await conn.fetchrow(
        "SELECT * FROM location_native_cache_heads WHERE cache_key=$1 FOR UPDATE", cache_key
    )
    digest = content_digest({"cache": _digest_value(dict(cache))})
    if head is not None and head["body_digest"] == digest:
        return None
    observed = await conn.fetchval(
        "INSERT INTO location_legacy_cache_observations "
        "(cache_key,body_digest,observation_id) VALUES($1,$2,$3) "
        "ON CONFLICT(cache_key,body_digest) DO NOTHING RETURNING observation_id",
        cache_key,
        digest,
        uuid4(),
    )
    if observed is None:
        observed = await conn.fetchval(
            "SELECT observation_id FROM location_legacy_cache_observations "
            "WHERE cache_key=$1 AND body_digest=$2",
            cache_key,
            digest,
        )
    if observed is None:
        raise PolicyUnavailableError("Legacy cache observation is unknown")
    return observed


async def capture_api_cache_export(pool: asyncpg.Pool, cache_key: str, reader: Any) -> Any:
    """Actual API cache bytes before emission; legacy/mixed inputs remain unbound.

    This source-owned hook records no prose in the ledger. A known current
    head binds only the persisted cache generation, not browser disposition.
    The independently observed COMMIT is mandatory before returning bytes.
    """
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.storage import _lock_location_writes
    from butlers.location_retention import content_digest

    if not _api_capture_configured(pool):
        return await reader(pool)
    generation = uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_schema()") != "chronicler":
                raise PolicyUnavailableError("Owning export schema differs")
            await _lock_location_writes(conn)
            row = await reader(conn)
            if row is None:
                return None
            await observe_legacy_cache(conn, cache_key)
            cache = await conn.fetchrow("SELECT * FROM tier2_cache WHERE cache_key=$1", cache_key)
            if cache is None:
                raise PolicyUnavailableError("Current export body is unavailable")
            head = await conn.fetchrow(
                "SELECT * FROM location_native_cache_heads WHERE cache_key=$1", cache_key
            )
            current = content_digest({"cache": _digest_value(dict(cache))})
            bound = head is not None and head["body_digest"] == current
            emitted = content_digest({"export": _digest_value(dict(row))})
            from butlers.chronicler.location_export_lifetime import (
                native_export_request,
                register_native_export,
            )

            server_request = native_export_request()
            if server_request is None:
                raise PolicyUnavailableError("Owning export lifetime is unavailable")
            await conn.execute(
                "INSERT INTO location_native_cache_exports "
                "(copy_generation,cache_key,cache_generation,body_digest,receiving_server_request) "
                "VALUES($1,$2,$3,$4,$5)",
                generation,
                cache_key,
                head["cache_generation"] if bound else None,
                emitted,
                server_request,
            )
            register_native_export(pool, "cache", generation, emitted)
    async with pool.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT body_digest FROM location_native_cache_exports WHERE copy_generation=$1",
            generation,
        )
    if observed != emitted:
        raise PolicyUnavailableError("Committed cache export is unknown")
    return row


async def record_legacy_replacement(conn: Any, cache_key: str, observation: UUID | None) -> None:
    """Same actual writer TX: old body gone and fresh native inputs bound.

    This receipt covers only the local old cache body. Exported copies and an
    unbound new report remain unknown; it is not an all-holder certificate.
    """
    if observation is None:
        return
    from butlers.chronicler.location_projection import _digest_value
    from butlers.location_retention import content_digest

    old = await conn.fetchrow(
        "SELECT * FROM location_legacy_cache_observations WHERE observation_id=$1", observation
    )
    cache = await conn.fetchrow("SELECT * FROM tier2_cache WHERE cache_key=$1", cache_key)
    head = await conn.fetchrow(
        "SELECT * FROM location_native_cache_heads WHERE cache_key=$1", cache_key
    )
    if old is None or cache is None or head is None or old["cache_key"] != cache_key:
        return  # A legacy/dict result established no genuine new input binding.
    current = content_digest({"cache": _digest_value(dict(cache))})
    if current == old["body_digest"] or head["body_digest"] != current:
        return
    await conn.execute(
        "INSERT INTO location_legacy_cache_replacements "
        "(observation_id,receipt_id,replacement_generation) VALUES($1,$2,$3) "
        "ON CONFLICT(observation_id) DO NOTHING",
        observation,
        uuid4(),
        head["cache_generation"],
    )


async def classify_legacy_caches(pool: asyncpg.Pool) -> None:
    """Scheduled bounded census of actual unbound own bodies, with durable readback."""
    from butlers.chronicler.storage import _lock_location_writes

    observations = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Owning cache census identity differs")
            await _lock_location_writes(conn)
            # This keyset checkpoint is progress, never a coverage certificate.
            # Wrap each pass so newly committed/backdated keys are revisited.
            from butlers.core.state import state_get, state_set

            key = "location-retention:legacy-cache-scan"
            cursor = await state_get(conn, key)
            cursor = cursor.get("after") if isinstance(cursor, dict) else None
            if cursor is not None and not isinstance(cursor, str):
                raise PolicyUnavailableError("Legacy cache cursor is malformed")
            rows = await conn.fetch(
                "SELECT cache_key FROM tier2_cache c WHERE NOT EXISTS ("
                "SELECT 1 FROM location_native_cache_heads h WHERE h.cache_key=c.cache_key) "
                "AND ($1::text IS NULL OR c.cache_key>$1) ORDER BY cache_key LIMIT 32",
                cursor,
            )
            for row in rows:
                observed = await observe_legacy_cache(conn, row["cache_key"])
                if observed is not None:
                    observations.append(observed)
            await state_set(conn, key, {"after": rows[-1]["cache_key"] if rows else None})
    async with pool.acquire() as committed:
        count = await committed.fetchval(
            "SELECT count(*) FROM location_legacy_cache_observations "
            "WHERE observation_id=ANY($1::uuid[])",
            observations,
        )
    if count != len(observations):
        raise PolicyUnavailableError("Committed legacy census is unknown")


async def dispose_bound_native_copies(pool: asyncpg.Pool, decision_id: UUID) -> None:
    """Dispose only genuinely bound, settled, exclusive owning input copies.

    Unbound receiving holders, mixed source cohorts, unknown lineage and active
    sessions survive. A native input digest is a binding, never authentication.
    These own receipts do not certify any foreign/uncaptured copy holder.
    """
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.storage import _lock_location_writes
    from butlers.location_retention import content_digest

    committed_receipts = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Owning copy writer identity differs")
            await _lock_location_writes(conn)
            plan = await conn.fetchrow(
                "SELECT * FROM location_retention_plans WHERE decision_id=$1 FOR UPDATE",
                decision_id,
            )
            if plan is None or plan["state"] != "holder_pending":
                return
            cohorts = await conn.fetch(
                "SELECT DISTINCT b.copy_generation FROM location_native_copy_births b "
                "JOIN location_retention_plan_outputs o USING(output_kind,output_id) "
                "WHERE o.decision_id=$1 ORDER BY b.copy_generation",
                decision_id,
            )
            for cohort in cohorts:
                generation = cohort["copy_generation"]
                births = await conn.fetch(
                    "SELECT b.*,EXISTS(SELECT 1 FROM location_retention_plan_outputs o "
                    "WHERE o.decision_id=$2 AND o.output_kind=b.output_kind "
                    "AND o.output_id=b.output_id) AS selected "
                    "FROM location_native_copy_births b WHERE b.copy_generation=$1",
                    generation,
                    decision_id,
                )
                if not births or any(
                    not b["lineage_known"]
                    or not b["exclusive_input"]
                    or not b["selected"]
                    or b["receiving_session"] is None
                    for b in births
                ):
                    continue
                session_id, digest = births[0]["receiving_session"], births[0]["input_digest"]
                if any(
                    b["receiving_session"] != session_id or b["input_digest"] != digest
                    for b in births
                ):
                    raise PolicyUnavailableError("Native copy generation differs")
                prior = await conn.fetchrow(
                    "SELECT * FROM location_native_copy_dispositions WHERE copy_generation=$1",
                    generation,
                )
                if prior is not None:
                    if prior["input_digest"] != digest or prior["receiving_session"] != session_id:
                        raise PolicyUnavailableError("Committed native copy disposition differs")
                    committed_receipts.append(prior["receipt_id"])
                    continue
                # Every source input to this receiving session must be in the
                # selected old cohort; fresh/unrelated/mixed input stays held.
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_native_copy_births b "
                    "WHERE b.receiving_session=$1 AND (NOT b.lineage_known "
                    "OR NOT b.exclusive_input OR NOT EXISTS ("
                    "SELECT 1 FROM location_retention_plan_outputs o "
                    "WHERE o.decision_id=$2 AND o.output_kind=b.output_kind "
                    "AND o.output_id=b.output_id)))",
                    session_id,
                    decision_id,
                ):
                    continue
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_native_copy_births b "
                    "JOIN location_projection_outputs o USING(output_kind,output_id) "
                    "WHERE b.copy_generation=$1 AND NOT EXISTS ("
                    "SELECT 1 FROM location_retention_plan_rows r "
                    "WHERE r.decision_id=$2 AND r.raw_id=o.raw_id "
                    "AND r.source_revision=o.source_revision))",
                    generation,
                    decision_id,
                ):
                    continue
                session = await conn.fetchrow(
                    "SELECT * FROM sessions WHERE id=$1 FOR UPDATE", session_id
                )
                if (
                    session is None
                    or session["completed_at"] is None
                    or session["success"] is not True
                    or session["error"] is not None
                ):
                    continue
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_native_dispatch_sessions d "
                    "JOIN location_native_dispatch_inputs i USING(input_generation) "
                    "LEFT JOIN location_native_memory_runtime_receipts r USING(input_generation) "
                    "WHERE d.receiving_session=$1 AND i.origin_kind='native_memory' "
                    "AND (r.input_generation IS NULL OR r.memory_context_present) AND NOT EXISTS("
                    "SELECT 1 FROM location_runtime_context_bindings c "
                    "JOIN location_runtime_context_dispositions x USING(input_generation) "
                    "WHERE c.receiving_session=d.receiving_session AND x.decision_id=$2))",
                    session_id,
                    decision_id,
                ):
                    continue  # Independently composed context is never erased by a subset.
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_context_intents i "
                    "WHERE i.receiving_session=$1 AND NOT EXISTS("
                    "SELECT 1 FROM location_runtime_context_dispositions d "
                    "WHERE d.input_generation=i.input_generation AND d.decision_id=$2))",
                    session_id,
                    decision_id,
                ):
                    continue  # Preserve frozen full session body until its own context closes.
                calls = session["tool_calls"]
                allowed = {
                    "chronicler_list_events",
                    "chronicler_list_episodes",
                    "chronicler_day_close_bundle",
                    "chronicler_get_episode",
                }
                if (
                    not isinstance(calls, list)
                    or (
                        not calls
                        and births[0]["producer_kind"] not in {"native_dispatch", "native_memory"}
                    )
                    or any(
                        not isinstance(call, dict) or call.get("name") not in allowed
                        for call in calls
                    )
                ):
                    continue
                caches = await conn.fetch(
                    "SELECT h.* FROM location_native_cache_heads h WHERE EXISTS ("
                    "SELECT 1 FROM location_native_cache_inputs i WHERE i.cache_key=h.cache_key "
                    "AND i.cache_generation=h.cache_generation AND i.copy_generation=$1) "
                    "ORDER BY h.cache_key FOR UPDATE OF h",
                    generation,
                )
                for head in caches:
                    # A current cache with any other captured parent survives.
                    if await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_native_cache_inputs i "
                        "JOIN location_native_copy_births b USING(copy_generation) "
                        "WHERE i.cache_key=$1 AND i.cache_generation=$2 AND NOT EXISTS ("
                        "SELECT 1 FROM location_retention_plan_outputs o "
                        "WHERE o.decision_id=$3 AND o.output_kind=b.output_kind "
                        "AND o.output_id=b.output_id))",
                        head["cache_key"],
                        head["cache_generation"],
                        decision_id,
                    ):
                        raise PolicyUnavailableError("Current cache has unrelated input")
                    cache = await conn.fetchrow(
                        "SELECT * FROM tier2_cache WHERE cache_key=$1 FOR UPDATE", head["cache_key"]
                    )
                    if (
                        cache is None
                        or content_digest({"cache": _digest_value(dict(cache))})
                        != head["body_digest"]
                    ):
                        raise PolicyUnavailableError("Native cache generation changed")
                    await conn.execute(
                        "DELETE FROM tier2_cache WHERE cache_key=$1", head["cache_key"]
                    )
                    await conn.execute(
                        "DELETE FROM location_native_cache_heads WHERE cache_key=$1",
                        head["cache_key"],
                    )
                await conn.execute(
                    "UPDATE session_process_logs "
                    "SET command=CASE WHEN EXISTS(SELECT 1 FROM location_native_dispatch_sessions "
                    "WHERE receiving_session=$1) THEN '[Location-derived diagnostic forgotten]' "
                    "ELSE command END,"
                    "stderr=NULL WHERE session_id=$1",
                    session_id,
                )
                await conn.execute(
                    "UPDATE sessions SET result='[Location-derived output forgotten]',"
                    "tool_calls='[]'::jsonb,error=NULL WHERE id=$1",
                    session_id,
                )
                # Output-only MCP births do not authorize erasing the
                # pre-existing prompt. Only the native pre-dispatch bundle
                # binds that stored generated input; system prompt stays intact.
                await conn.execute(
                    "UPDATE sessions SET prompt='[Location-derived input forgotten]' "
                    "WHERE id=$1 AND EXISTS(SELECT 1 FROM location_native_dispatch_sessions "
                    "WHERE receiving_session=$1)",
                    session_id,
                )
                receipt_id = uuid4()
                await conn.execute(
                    "INSERT INTO location_native_copy_dispositions "
                    "(copy_generation,receipt_id,input_digest,receiving_session) "
                    "VALUES($1,$2,$3,$4)",
                    generation,
                    receipt_id,
                    digest,
                    session_id,
                )
                committed_receipts.append(receipt_id)
    async with pool.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT count(*) FROM location_native_copy_dispositions "
            "WHERE receipt_id=ANY($1::uuid[])",
            committed_receipts,
        )
    if observed != len(committed_receipts):
        raise PolicyUnavailableError("Committed native copy disposition is unknown")
    # A separate real owning read supplied these receipt IDs. Observe them in
    # another commit; do not treat the action ACK as the holder readback.
    async with pool.acquire() as observation:
        async with observation.transaction():
            if await observation.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Owning observation writer identity differs")
            await _lock_location_writes(observation)
            await observation.execute(
                "INSERT INTO location_retention_holder_receipts "
                "(decision_id,owning_butler,holder_kind,holder_generation,"
                "source_digest,receipt_id) "
                "SELECT $1,'chronicler','mcp_output',copy_generation,input_digest,receipt_id "
                "FROM location_native_copy_dispositions WHERE receipt_id=ANY($2::uuid[]) "
                "ON CONFLICT(decision_id,owning_butler,holder_kind,holder_generation) DO NOTHING",
                decision_id,
                committed_receipts,
            )
            matched = await observation.fetchval(
                "SELECT count(*) FROM location_retention_holder_receipts h "
                "JOIN location_native_copy_dispositions d "
                "ON h.holder_generation=d.copy_generation AND h.source_digest=d.input_digest "
                "AND h.receipt_id=d.receipt_id "
                "WHERE h.decision_id=$1 AND h.owning_butler='chronicler' "
                "AND h.holder_kind='mcp_output' AND d.receipt_id=ANY($2::uuid[])",
                decision_id,
                committed_receipts,
            )
            if matched != len(committed_receipts):
                raise PolicyUnavailableError("Owning holder observation differs")
    async with pool.acquire() as committed:
        matched = await committed.fetchval(
            "SELECT count(*) FROM location_retention_holder_receipts "
            "WHERE decision_id=$1 AND owning_butler='chronicler' AND holder_kind='mcp_output' "
            "AND receipt_id=ANY($2::uuid[])",
            decision_id,
            committed_receipts,
        )
    if matched != len(committed_receipts):
        raise PolicyUnavailableError("Committed holder observation is unknown")


async def observe_native_api_copies(pool: asyncpg.Pool, decision_id: UUID) -> None:
    """Independent owning read of exact native server-response dispositions.

    The default API pool's private ASGI producer, not a client ACK, created
    these records. This read is separate from both birth and final-send TXs.
    Remote recipients are outside the server copy cohort; managed UI cache
    fencing remains a separate required product control.
    """
    from butlers.chronicler.storage import _lock_location_writes

    async with pool.acquire() as source:
        receipts = await source.fetch(
            "SELECT DISTINCT b.copy_generation,b.input_digest,d.receipt_id "
            "FROM location_native_copy_births b JOIN location_retention_plan_outputs p "
            "USING(output_kind,output_id) JOIN location_native_api_dispositions d "
            "ON d.copy_generation=b.copy_generation "
            "AND d.server_request=b.receiving_server_request "
            "AND d.body_digest=b.input_digest AND d.producer_kind='native_read' "
            "WHERE p.decision_id=$1 AND b.producer_kind='api_export' AND b.lineage_known",
            decision_id,
        )
    async with pool.acquire() as own:
        async with own.transaction():
            if await own.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Owning observation writer identity differs")
            await _lock_location_writes(own)
            plan = await own.fetchrow(
                "SELECT state FROM location_retention_plans WHERE decision_id=$1 FOR UPDATE",
                decision_id,
            )
            if plan is None or plan["state"] != "holder_pending":
                return
            for receipt in receipts:
                prior = await own.fetchrow(
                    "SELECT * FROM location_retention_holder_receipts WHERE decision_id=$1 "
                    "AND owning_butler='chronicler' AND holder_kind='api_server' "
                    "AND holder_generation=$2",
                    decision_id,
                    receipt["copy_generation"],
                )
                if prior is not None:
                    if (
                        prior["source_digest"] != receipt["input_digest"]
                        or prior["receipt_id"] != receipt["receipt_id"]
                    ):
                        raise PolicyUnavailableError("Owning API observation differs")
                else:
                    await own.execute(
                        "INSERT INTO location_retention_holder_receipts "
                        "(decision_id,owning_butler,holder_kind,holder_generation,"
                        "source_digest,receipt_id) "
                        "VALUES($1,'chronicler','api_server',$2,$3,$4)",
                        decision_id,
                        receipt["copy_generation"],
                        receipt["input_digest"],
                        receipt["receipt_id"],
                    )
    async with pool.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT count(*) FROM location_retention_holder_receipts WHERE decision_id=$1 "
            "AND owning_butler='chronicler' AND holder_kind='api_server' "
            "AND receipt_id=ANY($2::uuid[])",
            decision_id,
            [r["receipt_id"] for r in receipts],
        )
    if observed != len(receipts):
        raise PolicyUnavailableError("Committed API observation is unknown")


async def reconcile_raw_batches(pool: asyncpg.Pool) -> None:
    """Read the approved connector-owned ledger after its separate raw COMMIT.

    Existing narrow SELECT conveys observation only. Header plus every exact
    member/count must bind the stored immutable grant; missing raw rows and
    incoming ACKs are never proof. Unknown resumes the same IDs/finite lease.
    """
    from butlers.chronicler.storage import _lock_location_writes

    pending = await pool.fetch(
        "SELECT p.*,g.grant_id,g.batch_id FROM location_retention_plans p "
        "JOIN location_retention_grants g USING(decision_id) "
        "WHERE p.state IN ('ready','raw_unknown') ORDER BY p.prepared_at,p.decision_id LIMIT 8"
    )
    for plan in pending:
        # These acquisitions observe actual connector COMMIT independently of
        # both the issuing own transaction and the connector action ACK.
        async with pool.acquire() as source:
            header = await source.fetchrow(
                "SELECT * FROM connectors.owntracks_retention_batches WHERE batch_id=$1",
                plan["batch_id"],
            )
            actual_rows = await source.fetch(
                "SELECT * FROM connectors.owntracks_retention_batch_rows WHERE batch_id=$1 "
                "ORDER BY raw_id,source_revision",
                plan["batch_id"],
            )
        async with pool.acquire() as own:
            async with own.transaction():
                if await own.fetchval("SELECT current_user") != "butler_chronicler_rw":
                    raise PolicyUnavailableError("Owning reconciliation writer identity differs")
                await _lock_location_writes(own)
                locked = await own.fetchrow(
                    "SELECT * FROM location_retention_plans WHERE decision_id=$1 FOR UPDATE",
                    plan["decision_id"],
                )
                if locked is None or locked["manifest_digest"] != plan["manifest_digest"]:
                    raise PolicyUnavailableError("Stored raw decision changed")
                if locked["state"] == "complete":
                    continue
                if locked["state"] not in {"ready", "raw_unknown"}:
                    raise PolicyUnavailableError("Stored raw transition differs")
                if header is None:
                    if actual_rows:
                        raise PolicyUnavailableError("Committed raw ledger is inconsistent")
                    await own.execute(
                        "UPDATE location_retention_plans SET state='raw_unknown' "
                        "WHERE decision_id=$1",
                        plan["decision_id"],
                    )
                    await own.execute(
                        "UPDATE location_retention_runs SET reason_code='receipt_unknown' "
                        "WHERE run_id=$1",
                        plan["run_id"],
                    )
                    continue
                expected = await own.fetch(
                    "SELECT * FROM location_retention_plan_rows WHERE decision_id=$1 "
                    "ORDER BY raw_id,source_revision",
                    plan["decision_id"],
                )
                if (
                    header["decision_id"] != plan["decision_id"]
                    or header["grant_id"] != plan["grant_id"]
                    or header["manifest_digest"] != plan["manifest_digest"]
                    or header["policy_version"] != plan["policy_version"]
                    or header["cutoff"] != plan["cutoff"]
                    or not 1 <= len(expected) <= 256
                    or len(actual_rows) != len(expected)
                ):
                    raise PolicyUnavailableError("Committed raw receipt differs")
                wanted = {
                    (r["raw_id"], r["source_revision"], r["logical_source_digest"])
                    for r in expected
                }
                observed = {
                    (r["raw_id"], r["source_revision"], r["logical_source_digest"])
                    for r in actual_rows
                }
                deleted = sum(r["disposition"] == "deleted" for r in actual_rows)
                already = sum(r["disposition"] == "already_forgotten" for r in actual_rows)
                if (
                    observed != wanted
                    or len(observed) != len(actual_rows)
                    or deleted + already != len(expected)
                    or header["deleted_count"] != deleted
                    or header["already_forgotten_count"] != already
                ):
                    raise PolicyUnavailableError("Committed raw member disposition differs")
                await own.execute(
                    "UPDATE location_retention_plans SET state='complete' WHERE decision_id=$1",
                    plan["decision_id"],
                )
                # Recompute actual committed count instead of incrementing on
                # each replay. This is a measured subset, not all-source success.
                await own.execute(
                    "UPDATE location_retention_runs r SET deleted_count=("
                    "SELECT COALESCE(sum(b.deleted_count),0) "
                    "FROM location_retention_plans p "
                    "JOIN location_retention_grants g USING(decision_id) "
                    "JOIN connectors.owntracks_retention_batches b ON b.batch_id=g.batch_id "
                    "WHERE p.run_id=r.run_id AND p.state='complete') WHERE r.run_id=$1",
                    plan["run_id"],
                )
        async with pool.acquire() as committed:
            state = await committed.fetchval(
                "SELECT state FROM location_retention_plans WHERE decision_id=$1",
                plan["decision_id"],
            )
        if state != "complete":
            raise PolicyUnavailableError("Committed raw reconciliation is unknown")


async def seal_native_frontier(pool: asyncpg.Pool, decision_id: UUID) -> UUID | None:
    """Inventory actual owning copies under the same producer/writer fence.

    Source skip and local preparation must already have separate committed
    observations. No request inventory, receipt ACK, caller coverage boolean
    or synthetic contract object enters this producer. Legacy/unbound session
    and cache copies are real UNKNOWN holders, never an empty-query waiver.
    The snapshot contains nonempty independently observed holders; point/raw
    deletion remains the subsequent, separately committed operation.
    """
    from butlers.chronicler.storage import _lock_location_writes

    if not native_copy_pool(pool):
        raise PolicyUnavailableError("Native frontier producer is unavailable")
    generation = uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Native frontier writer identity differs")
            await _lock_location_writes(conn)
            plan = await conn.fetchrow(
                "SELECT * FROM location_retention_plans WHERE decision_id=$1 FOR UPDATE",
                decision_id,
            )
            if plan is None or plan["state"] != "holder_pending":
                return None
            prior = await conn.fetchrow(
                "SELECT * FROM location_retention_frontiers WHERE decision_id=$1", decision_id
            )
            if prior is not None:
                if prior["manifest_digest"] != plan["manifest_digest"]:
                    raise PolicyUnavailableError("Native frontier manifest differs")
                return (
                    prior["frontier_generation"]
                    if await _all_committed_holders(conn, plan)
                    else None
                )
            source = await conn.fetchrow(
                "SELECT * FROM location_retention_holder_receipts WHERE decision_id=$1 "
                "AND owning_butler='switchboard' AND holder_kind='switchboard_skipped'",
                decision_id,
            )
            local = await conn.fetchrow(
                "SELECT * FROM location_retention_local_receipts WHERE decision_id=$1", decision_id
            )
            if (
                source is None
                or local is None
                or source["holder_generation"] != decision_id
                or source["source_digest"] != plan["manifest_digest"]
                or local["manifest_digest"] != plan["manifest_digest"]
            ):
                return None
            # An opaque old receiving body cannot be assigned by timestamp,
            # source text matching or citations. Treat it as UNKNOWN until an
            # actual owning classification/disposition exists. Empty content
            # contributes no stored copy; native input/output births bind the
            # full receiving holder, even after its body has been disposed.
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM sessions s WHERE "
                "(COALESCE(s.prompt,'')<>'' OR COALESCE(s.result,'')<>'' "
                "OR COALESCE(s.tool_calls,'[]'::jsonb)<>'[]'::jsonb) AND NOT EXISTS ("
                "SELECT 1 FROM location_native_copy_births b WHERE b.receiving_session=s.id))"
            ):
                return None
            # Census current legacy bodies as well as prior classified bodies.
            # A bounded scan cursor is progress, never complete coverage.
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM tier2_cache c WHERE NOT EXISTS("
                "SELECT 1 FROM location_native_cache_heads h WHERE h.cache_key=c.cache_key)) "
                "OR EXISTS(SELECT 1 FROM location_legacy_cache_observations o WHERE NOT EXISTS("
                "SELECT 1 FROM location_legacy_cache_replacements r "
                "WHERE r.observation_id=o.observation_id))"
            ):
                return None
            copies = await conn.fetch(
                "SELECT DISTINCT b.copy_generation,b.input_digest,b.producer_kind "
                "FROM location_native_copy_births b JOIN location_retention_plan_outputs p "
                "USING(output_kind,output_id) WHERE p.decision_id=$1 "
                "ORDER BY b.copy_generation",
                decision_id,
            )
            holders = [dict(source)]
            for copy in copies:
                kind = "api_server" if copy["producer_kind"] == "api_export" else "mcp_output"
                observed = await conn.fetchrow(
                    "SELECT * FROM location_retention_holder_receipts WHERE decision_id=$1 "
                    "AND owning_butler='chronicler' AND holder_kind=$2 "
                    "AND holder_generation=$3 AND source_digest=$4",
                    decision_id,
                    kind,
                    copy["copy_generation"],
                    copy["input_digest"],
                )
                if observed is None:
                    return None
                holders.append(dict(observed))
            from butlers.chronicler.location_catalog_copies import catalog_holder_inventory

            catalog = await catalog_holder_inventory(conn, decision_id)
            if any(h["receipt_id"] is None for h in catalog):
                return None
            for holder in catalog:
                # These are readbacks of actual owning immutable dispositions,
                # not a sealer-created erasure verdict or an empty cohort.
                await conn.execute(
                    "INSERT INTO location_retention_holder_receipts "
                    "(decision_id,owning_butler,holder_kind,holder_generation,"
                    "source_digest,receipt_id) "
                    "VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
                    decision_id,
                    holder["owning_butler"],
                    holder["holder_kind"],
                    holder["holder_generation"],
                    holder["source_digest"],
                    holder["receipt_id"],
                )
                holders.append(dict(holder))
            # The local preparation has a real earlier COMMIT/readback, and
            # remains distinct from the later point-disposal receipt.
            await conn.execute(
                "INSERT INTO location_retention_holder_receipts "
                "(decision_id,owning_butler,holder_kind,holder_generation,"
                "source_digest,receipt_id) "
                "VALUES($1,'chronicler','projection_prepare',$1,$2,$3) "
                "ON CONFLICT(decision_id,owning_butler,holder_kind,holder_generation) DO NOTHING",
                decision_id,
                plan["manifest_digest"],
                local["receipt_id"],
            )
            local_observed = await conn.fetchrow(
                "SELECT * FROM location_retention_holder_receipts WHERE decision_id=$1 "
                "AND owning_butler='chronicler' AND holder_kind='projection_prepare' "
                "AND source_digest=$2 AND receipt_id=$3",
                decision_id,
                plan["manifest_digest"],
                local["receipt_id"],
            )
            if local_observed is None:
                raise PolicyUnavailableError("Native preparation observation differs")
            holders.append(dict(local_observed))
            await conn.execute(
                "INSERT INTO location_retention_frontiers "
                "(decision_id,manifest_digest,frontier_generation,"
                "producer_contract,expected_count) "
                "VALUES($1,$2,$3,1,$4)",
                decision_id,
                plan["manifest_digest"],
                generation,
                len(holders),
            )
            for holder in holders:
                await conn.execute(
                    "INSERT INTO location_retention_frontier_holders "
                    "(decision_id,owning_butler,holder_kind,holder_generation,source_digest) "
                    "VALUES($1,$2,$3,$4,$5)",
                    decision_id,
                    holder["owning_butler"],
                    holder["holder_kind"],
                    holder["holder_generation"],
                    holder["source_digest"],
                )
            if await _all_committed_holders(conn, plan) is None:
                # Do not commit an immutable incomplete snapshot that could
                # later be confused with a complete native cohort.
                raise PolicyUnavailableError("Native holder inventory is incomplete")
    async with pool.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT frontier_generation FROM location_retention_frontiers "
            "WHERE decision_id=$1 AND manifest_digest=$2",
            decision_id,
            plan["manifest_digest"],
        )
    if observed != generation:
        raise PolicyUnavailableError("Committed native frontier is unknown")
    return generation
