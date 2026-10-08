"""Deterministic operational notification; no GitHub, Beads or credential bridge.

The host export is content blind. A durable attempting marker precedes the
external send; ambiguous acknowledgement never retries that send. Confirmed
outcomes retry only their ledger write. This does not change global notify's
best-effort semantics or introduce a new ledger vocabulary.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from butlers.api.routers.audit import append
from butlers.core.approvals_policy import get_approvals_policy_quiet_hours, is_policy_quiet_now
from butlers.core.attention_ledger import get_suppressing_context_signal, normalize_priority
from butlers.credential_store import resolve_owner_telegram_recipient
from butlers.nightly_assurance import (
    EXPORT_PATH,
    MAX_EXPORT_BYTES,
    EvidenceUnavailable,
    digest,
    read_json,
    validate_export,
)

_ACTOR = "nightly_assurance"
_ACTION = "nightly_incident_delivery"
_ROLE = "butler_switchboard_rw"


def _target(incident: dict) -> str:
    return f"nightly:{incident['incident_id']}:{incident['episode_id']}"


async def _lock(connection: asyncpg.Connection, target: str) -> None:
    await connection.execute("SELECT pg_advisory_xact_lock($1)", int(digest(target)[:15], 16))
    if await connection.fetchval("SELECT current_user") != _ROLE:
        raise EvidenceUnavailable("wrong-runtime-role")


async def _latest(connection: asyncpg.Connection, target: str) -> dict | None:
    row = await connection.fetchrow(
        "SELECT id, metadata FROM public.audit_log WHERE actor=$1 AND action=$2 AND target=$3 "
        "ORDER BY id DESC LIMIT 1",
        _ACTOR,
        _ACTION,
        target,
    )
    if row is None:
        return None
    metadata = row["metadata"]
    # Production pools install the JSONB codec. Do not silently credit a
    # quoted/double-encoded scalar as the owning marker.
    if not isinstance(metadata, dict) or metadata.get("version") != 1:
        raise EvidenceUnavailable("invalid-delivery-marker")
    return {**metadata, "audit_id": row["id"]}


async def _marker(connection: asyncpg.Connection, target: str, state: dict) -> int:
    return await append(
        connection,
        _ACTOR,
        _ACTION,
        target=target,
        metadata={"version": 1, **state},
        result=state["state"],
    )


async def _claim(pool: asyncpg.Pool, target: str) -> dict:
    async with pool.acquire() as connection, connection.transaction():
        await _lock(connection, target)
        previous = await _latest(connection, target)
        if previous:
            if previous["state"] == "attempting":
                uncertain = {"state": "uncertain", "attempt_key": previous["attempt_key"]}
                await _marker(connection, target, uncertain)
                return uncertain
            return previous
        state = {"state": "attempting", "attempt_key": str(uuid.uuid4())}
        await _marker(connection, target, state)
        return {**state, "claimed": True}


async def _decision(pool: asyncpg.Pool, incident: dict, *, now: datetime) -> dict:
    # Gate lookup failure is unavailable, never an assumed open window. The
    # caller retains attempting/uncertain without a fabricated delivered row.
    policy = await get_approvals_policy_quiet_hours(pool)
    if policy is None:
        raise EvidenceUnavailable("policy-unavailable")
    if is_policy_quiet_now(policy, now=now):
        return {"outcome": "suppressed", "reason": "quiet_hours", "notification_ref": None}
    context = await get_suppressing_context_signal(pool, now=now)
    if context:
        return {"outcome": "suppressed", "reason": "context_bus", "notification_ref": None}
    recipient = await resolve_owner_telegram_recipient(pool)
    if not recipient:
        return {"outcome": "failed", "reason": "no_recipient_configured", "notification_ref": None}
    from butlers.tools.switchboard.notification.deliver import deliver

    result = await deliver(
        pool,
        channel="telegram",
        recipient=recipient,
        source_butler="switchboard",
        message=(
            f"Nightly assurance needs attention: {incident['incident_id']}. "
            "Open the dashboard issue detail for its owned evidence."
        ),
        metadata={
            "origin": _ACTOR,
            "incident_id": incident["incident_id"],
            "episode_id": incident["episode_id"],
        },
    )
    status = result.get("status") if isinstance(result, dict) else None
    if status not in {"sent", "failed"}:
        raise EvidenceUnavailable("delivery-acknowledgement-unknown")
    reference = result.get("notification_id")
    if reference is not None:
        try:
            reference = str(uuid.UUID(str(reference)))
        except ValueError:
            reference = None  # Never reflect arbitrary transport text.
    return {
        "outcome": "delivered" if status == "sent" else "failed",
        "reason": None if status == "sent" else "delivery_failed",
        "notification_ref": reference,
    }


async def _known(pool: asyncpg.Pool, target: str, attempt_key: str, decision: dict) -> None:
    async with pool.acquire() as connection, connection.transaction():
        await _lock(connection, target)
        prior = await _latest(connection, target)
        if prior is None or prior["attempt_key"] != attempt_key:
            raise EvidenceUnavailable("delivery-incarnation-mismatch")
        if prior["state"] in {"attempting", "uncertain"}:
            await _marker(
                connection, target, {"state": "known", "attempt_key": attempt_key, **decision}
            )


async def _persist_ledger(pool: asyncpg.Pool, target: str, incident: dict) -> str | None:
    """Savepoint preserves known outcome; row and ID binding commit together."""
    row_id = None
    async with pool.acquire() as connection, connection.transaction():
        await _lock(connection, target)
        marker = await _latest(connection, target)
        if marker is None:
            raise EvidenceUnavailable("delivery-marker-unavailable")
        if marker["state"] == "ledgered":
            row_id = marker["ledger_id"]
        elif marker["state"] == "known":
            priority_label, priority_score = normalize_priority("normal")
            try:
                async with connection.transaction():
                    row_id = str(
                        await connection.fetchval(
                            "INSERT INTO public.attention_ledger "
                            "(origin_butler,source,channel,intent,priority_label,priority_score,dedup_key,"
                            "outcome,reason,notification_ref,metadata) "
                            "VALUES ('switchboard','notify','telegram','send',"
                            "$1,$2,$3,$4,$5,$6,$7::jsonb) "
                            "RETURNING id",
                            priority_label,
                            priority_score,
                            target,
                            marker["outcome"],
                            marker["reason"],
                            marker["notification_ref"],
                            {
                                "version": 1,
                                "origin": _ACTOR,
                                "incident_id": incident["incident_id"],
                                "episode_id": incident["episode_id"],
                                "attempt_key": marker["attempt_key"],
                            },
                        )
                    )
                    await _marker(
                        connection,
                        target,
                        {
                            "state": "ledgered",
                            "ledger_id": row_id,
                            "attempt_key": marker["attempt_key"],
                            "outcome": marker["outcome"],
                        },
                    )
            except (asyncpg.PostgresError, OSError):
                row_id = None
    if row_id is None:
        return None
    # Separate acquisition verifies durable binding and row, not in-TX visibility.
    async with pool.acquire() as connection:
        if await connection.fetchval("SELECT current_user") != _ROLE:
            raise EvidenceUnavailable("wrong-readback-role")
        stored = await _latest(connection, target)
        row = await connection.fetchrow(
            "SELECT id,source,dedup_key,metadata FROM public.attention_ledger WHERE id=$1::uuid",
            uuid.UUID(row_id),
        )
        if (
            stored is None
            or stored.get("ledger_id") != row_id
            or row is None
            or row["source"] != "notify"
            or row["dedup_key"] != target
            or not isinstance(row["metadata"], dict)
            or row["metadata"].get("episode_id") != incident["episode_id"]
        ):
            raise EvidenceUnavailable("ledger-readback-unavailable")
    return row_id


async def run_nightly_assurance(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Observe trusted host export and notify once per incident episode."""
    path = Path(os.environ.get("BUTLERS_NIGHTLY_INCIDENT_EXPORT", str(EXPORT_PATH)))
    now = now or datetime.now(UTC)
    try:
        incidents = validate_export(read_json(path, limit=MAX_EXPORT_BYTES), now=now)
    except (OSError, ValueError):
        return {"available": False, "reason": "nightly_export_unavailable"}
    results = []
    for incident in incidents:
        if incident["status"] != "open":
            continue
        target = _target(incident)
        try:
            state = await _claim(pool, target)
            if state.get("claimed"):
                try:
                    decision = await _decision(pool, incident, now=now)
                except Exception:
                    # An external boundary may have accepted before ACK loss.
                    # Never emit its raw message or send automatically again.
                    results.append({"incident_id": incident["incident_id"], "state": "uncertain"})
                    continue
                await _known(pool, target, state["attempt_key"], decision)
            row_id = await _persist_ledger(pool, target, incident)
            results.append(
                {
                    "incident_id": incident["incident_id"],
                    "state": "ledgered" if row_id else "pending",
                    "ledger_id": row_id,
                }
            )
        except (EvidenceUnavailable, asyncpg.PostgresError, OSError):
            results.append({"incident_id": incident["incident_id"], "state": "unavailable"})
    return {"available": True, "incidents": results}
