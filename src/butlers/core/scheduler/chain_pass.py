"""Tick pass 3: event-chain materialisation and firing."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from butlers.core.scheduler.rows import (
    _dict_to_jsonb,
    _parse_deadline_threshold_reference,
    _parse_uuid_reference,
)

logger = logging.getLogger("butlers.core.scheduler")


async def _fire_chain(
    pool: asyncpg.Pool,
    *,
    chain_id,
    chain_name: str,
    actions,
    now: datetime,
    trigger_label: str,
) -> None:
    """Materialize chain actions into scheduled_tasks and mark the chain as fired.

    Args:
        pool: asyncpg connection pool.
        chain_id: UUID of the event_chains row to fire.
        chain_name: Human-readable chain name (used in task name generation).
        actions: Parsed list of action dicts.
        now: Current tick timestamp.
        trigger_label: Log label describing what triggered this chain.
    """
    from butlers.core.temporal.event_chains import materialize_chain_actions

    tasks = materialize_chain_actions(
        chain_name=chain_name,
        actions=actions,
        fired_at=now,
    )

    for task in tasks:
        try:
            # Bind job_args as a plain dict, NOT a json.dumps() string: see
            # _dict_to_jsonb's docstring (bu-cymc4/bu-xfcpf). No ::jsonb cast
            # in the SQL either -- Postgres infers the parameter's jsonb type
            # from the target column.
            await pool.execute(
                """
                INSERT INTO scheduled_tasks
                    (name, cron, dispatch_mode, prompt, job_name, job_args,
                     source, next_run_at, until_at, enabled)
                VALUES
                    ($1, '* * * * *', $2, $3, $4, $5,
                     'chain', $6, $7, true)
                ON CONFLICT (name) DO NOTHING
                """,
                task["name"],
                task["dispatch_mode"],
                task.get("prompt"),
                task.get("job_name"),
                _dict_to_jsonb(task.get("job_args")) if task.get("job_args") else None,
                task["next_run_at"],
                task["until_at"],
            )
        except Exception:
            logger.exception("Failed to insert chain task %r", task["name"])

    await pool.execute(
        """
        UPDATE event_chains SET status = 'fired'
        WHERE id = $1
        """,
        chain_id,
    )
    logger.info("Fired event chain %r triggered by %s", chain_name, trigger_label)


async def _tick_event_chain_pass(
    pool: asyncpg.Pool,
    dispatch_fn,
    now: datetime,
) -> int:
    """Detect event chain triggers and materialize actions.

    Handles:
    - deadline_passed: deadline tasks that transitioned to 'expired' or 'completed'
    - deadline_threshold: deadline tasks where a matching severity threshold has fired

    Returns the number of chains fired.
    """
    import json as _json

    from butlers.core.temporal.event_chains import should_fire_chain

    chains_fired = 0

    # --- Trigger: deadline_passed ---
    # Detect active chains whose referenced deadline has reached a terminal status
    # (expired or completed).  The deadline_status was already updated in Pass 1
    # (_tick_deadline_pass) earlier in the same tick() call.
    #
    # Migrated schema is required; storage faults remain visible.
    deadline_passed_chains = await pool.fetch(
        """
        SELECT id AS chain_id, name AS chain_name, actions, trigger_reference
        FROM event_chains
        WHERE trigger_type = 'deadline_passed'
          AND status = 'active'
        """
    )

    deadline_passed_refs: dict[Any, tuple[uuid.UUID, str, Any]] = {}
    for row in deadline_passed_chains:
        chain_id = row["chain_id"]
        chain_name = row["chain_name"]
        trigger_ref = row["trigger_reference"]
        deadline_id = _parse_uuid_reference(trigger_ref)
        if deadline_id is None:
            if trigger_ref is not None:
                logger.warning(
                    "Event chain %r has invalid deadline_passed trigger_reference %r "
                    "(expected '<deadline-uuid>'); skipping",
                    chain_name,
                    trigger_ref,
                )
            continue
        deadline_passed_refs[chain_id] = (deadline_id, chain_name, row["actions"])

    deadline_passed_statuses: dict[uuid.UUID, str] = {}
    if deadline_passed_refs:
        status_rows = await pool.fetch(
            """
            SELECT id, deadline_status
            FROM scheduled_tasks
            WHERE id = ANY($1::uuid[])
              AND task_type = 'deadline'
              AND deadline_status IN ('expired', 'completed')
            """,
            list({deadline_id for deadline_id, _, _ in deadline_passed_refs.values()}),
        )
        deadline_passed_statuses = {row["id"]: row["deadline_status"] for row in status_rows}

    for chain_id, (deadline_id, chain_name, actions) in deadline_passed_refs.items():
        dl_status = deadline_passed_statuses.get(deadline_id)
        if dl_status is None:
            continue
        if isinstance(actions, str):
            actions = _json.loads(actions)

        if not should_fire_chain(chain_depth=0, chain_name=chain_name):
            continue

        await _fire_chain(
            pool,
            chain_id=chain_id,
            chain_name=chain_name,
            actions=actions,
            now=now,
            trigger_label=f"deadline_passed (status={dl_status!r})",
        )
        chains_fired += 1

    # --- Trigger: deadline_threshold ---
    # Detect active chains whose referenced deadline has fired the severity in
    # trigger_reference.  Format: "<deadline-uuid>:<severity>" (e.g. "abc-123:critical").
    # The chain fires when that severity appears in the deadline's fired_thresholds.
    deadline_threshold_chains = await pool.fetch(
        """
        SELECT id AS chain_id, name AS chain_name, actions, trigger_reference
        FROM event_chains
        WHERE trigger_type = 'deadline_threshold'
          AND status = 'active'
        """
    )

    deadline_threshold_refs: dict[Any, tuple[uuid.UUID, str, Any, str]] = {}
    for row in deadline_threshold_chains:
        chain_id = row["chain_id"]
        chain_name = row["chain_name"]
        trigger_ref = row["trigger_reference"]
        parsed_ref = _parse_deadline_threshold_reference(trigger_ref)
        if parsed_ref is None:
            if trigger_ref is not None:
                logger.warning(
                    "Event chain %r has invalid deadline_threshold trigger_reference %r "
                    "(expected '<deadline-uuid>:<severity>'); skipping",
                    chain_name,
                    trigger_ref,
                )
            continue
        deadline_id, expected_severity = parsed_ref
        deadline_threshold_refs[chain_id] = (
            deadline_id,
            expected_severity,
            row["actions"],
            chain_name,
        )

    deadline_fired_thresholds: dict[uuid.UUID, Any] = {}
    if deadline_threshold_refs:
        threshold_rows = await pool.fetch(
            """
            SELECT id, fired_thresholds
            FROM scheduled_tasks
            WHERE id = ANY($1::uuid[])
              AND task_type = 'deadline'
              AND fired_thresholds IS NOT NULL
              AND fired_thresholds != '[]'::jsonb
            """,
            list({deadline_id for deadline_id, _, _, _ in deadline_threshold_refs.values()}),
        )
        deadline_fired_thresholds = {row["id"]: row["fired_thresholds"] for row in threshold_rows}

    for chain_id, (
        deadline_id,
        expected_severity,
        actions,
        chain_name,
    ) in deadline_threshold_refs.items():
        raw_fired = deadline_fired_thresholds.get(deadline_id)
        if raw_fired is None:
            continue
        if isinstance(actions, str):
            actions = _json.loads(actions)

        fired_thresholds: list[dict] = (
            raw_fired if isinstance(raw_fired, list) else _json.loads(raw_fired or "[]")
        )
        severity_fired = any(t.get("severity") == expected_severity for t in fired_thresholds)
        if not severity_fired:
            continue

        if not should_fire_chain(chain_depth=0, chain_name=chain_name):
            continue

        await _fire_chain(
            pool,
            chain_id=chain_id,
            chain_name=chain_name,
            actions=actions,
            now=now,
            trigger_label=f"deadline_threshold (severity={expected_severity!r})",
        )
        chains_fired += 1

    return chains_fired
