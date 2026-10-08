"""Education butler — teaching flow state machine.

Provides the complete teaching flow lifecycle:
- teaching_flow_start: creates mind map, initializes and advances to DIAGNOSING
- teaching_flow_get: reads current flow state from KV store
- teaching_flow_advance: drives the state machine through all transitions, recording
  the pedagogical technique the next concept's type calls for
- teaching_flow_abandon: marks flow abandoned, cleans up review schedules
- teaching_flow_list: lists flows with optional status filter, including mastery_pct
- assemble_session_context: builds structured context for ephemeral sessions
- check_stale_flows: weekly staleness detection, auto-abandons inactive flows

State machine transitions:
  pending → diagnosing
  diagnosing → planning
  planning → teaching
  teaching → quizzing
  quizzing → reviewing
  quizzing → teaching (frontier has unmastered nodes)
  reviewing → teaching (frontier has unmastered nodes)
  reviewing → completed (all nodes mastered)
  any non-terminal → abandoned

CAS semantics: state_compare_and_set is used for concurrent-safe writes.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from datetime import datetime as _dt
from typing import Any

import asyncpg

from butlers.core.state import (
    CASConflictError,
    decode_jsonb,
    state_compare_and_set,
    state_get,
    state_set,
)
from butlers.tools.education._helpers import _transaction
from butlers.tools.education.mastery import mastery_get_map_summary
from butlers.tools.education.mind_map_queries import mind_map_frontier
from butlers.tools.education.mind_maps import mind_map_create, mind_map_update_status
from butlers.tools.education.pedagogy import is_technique, technique_for_node

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TERMINAL_STATES = frozenset({"completed", "abandoned"})
_NON_TERMINAL_STATES = frozenset(
    {"pending", "diagnosing", "planning", "teaching", "quizzing", "reviewing"}
)
_ALL_VALID_STATES = _TERMINAL_STATES | _NON_TERMINAL_STATES
_STALE_DAYS = 30
_CAS_RETRY_DELAY = 0.1  # seconds
_CAS_MAX_RETRIES = 1

# States requiring non-null current_node_id
_NODE_REQUIRED_STATES = frozenset({"teaching", "quizzing", "reviewing"})
# States requiring non-null current_phase
# Note: quizzing allows null current_phase (phase tracking within quizzing is optional)
_PHASE_REQUIRED_STATES = frozenset({"teaching"})

# ---------------------------------------------------------------------------
# Type stubs (schedule delete for abandon cleanup)
# ---------------------------------------------------------------------------

ScheduleDeleteFn = Callable[..., Awaitable[None]]


async def _default_schedule_delete(name: str) -> None:
    """Stub: replaced by the real core schedule_delete at runtime."""
    # pragma: no cover
    raise NotImplementedError("schedule_delete must be provided by core infrastructure")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _flow_key(mind_map_id: str) -> str:
    """Return the KV store key for a flow's state."""
    return f"flow:{mind_map_id}"


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


def _initial_flow_state(mind_map_id: str) -> dict[str, Any]:
    """Build the initial flow state dict at PENDING status."""
    now = _now_iso()
    return {
        "status": "pending",
        "mind_map_id": mind_map_id,
        "current_node_id": None,
        "current_phase": None,
        "current_technique": None,
        "diagnostic_results": {},
        "session_count": 0,
        "started_at": now,
        "last_session_at": now,
    }


def _validate_state_invariants(state: dict[str, Any]) -> None:
    """Enforce field invariants on flow state.

    Raises ValueError if current_node_id or current_phase constraints are violated.
    """
    status = state.get("status", "")
    node_id = state.get("current_node_id")
    phase = state.get("current_phase")
    technique = state.get("current_technique")

    if status in _NODE_REQUIRED_STATES and node_id is None:
        raise ValueError(f"current_node_id must be non-null when status is {status!r}")
    if status in _PHASE_REQUIRED_STATES and phase is None:
        raise ValueError(f"current_phase must be non-null when status is {status!r}")
    if status not in _PHASE_REQUIRED_STATES and phase is not None:
        # current_phase must be null in all other states
        raise ValueError(f"current_phase must be null when status is {status!r}, got {phase!r}")
    if status not in _PHASE_REQUIRED_STATES and technique is not None:
        # A technique outlasting the teaching phase would have the next session
        # explaining a choice it is no longer acting on.
        raise ValueError(
            f"current_technique must be null when status is {status!r}, got {technique!r}"
        )
    if technique is not None and not is_technique(technique):
        raise ValueError(f"current_technique is not a known technique: {technique!r}")


async def _determine_next_node(pool: asyncpg.Pool, mind_map_id: str) -> dict[str, Any] | None:
    """Return the highest-priority frontier node, or None when there is none.

    The whole node is returned rather than its ID because entering TEACHING also
    needs its ``metadata.concept_type`` to pick a technique.
    """
    nodes = await mind_map_frontier(pool, mind_map_id)
    return nodes[0] if nodes else None


def _enter_teaching(state: dict[str, Any], node: dict[str, Any]) -> None:
    """Point flow *state* at *node* in the TEACHING status, technique and all."""
    state["status"] = "teaching"
    state["current_node_id"] = str(node["id"])
    state["current_phase"] = "explaining"
    state["current_technique"] = technique_for_node(node)


async def _all_nodes_mastered(pool: asyncpg.Pool, mind_map_id: str) -> bool:
    """Return True if all nodes in the mind map are mastered."""
    summary = await mastery_get_map_summary(pool, mind_map_id)
    total = summary["total_nodes"]
    mastered = summary["mastered_count"]
    return total > 0 and mastered == total


async def _get_state_with_version(
    pool: asyncpg.Pool, mind_map_id: str
) -> tuple[dict[str, Any] | None, int | None]:
    """Fetch state dict and version from the KV store.

    Returns (state_dict, version) or (None, None) if key not found.
    """
    row = await pool.fetchrow(
        "SELECT value, version FROM state WHERE key = $1",
        _flow_key(mind_map_id),
    )
    if row is None:
        return None, None
    val = decode_jsonb(row["value"])
    return val, row["version"]


async def _write_state_cas(
    pool: asyncpg.Pool,
    mind_map_id: str,
    new_state: dict[str, Any],
    *,
    expected_version: int | None,
) -> None:
    """Write flow state using CAS if expected_version is set, otherwise plain set.

    Retries once on CAS conflict after a short backoff.
    """
    key = _flow_key(mind_map_id)
    if expected_version is None:
        await state_set(pool, key, new_state)
        return

    for attempt in range(_CAS_MAX_RETRIES + 1):
        try:
            await state_compare_and_set(pool, key, expected_version, new_state)
            return
        except CASConflictError:
            if attempt < _CAS_MAX_RETRIES:
                logger.warning(
                    "CAS conflict for flow %s (attempt %d/%d) — retrying after backoff",
                    mind_map_id,
                    attempt + 1,
                    _CAS_MAX_RETRIES + 1,
                )
                await asyncio.sleep(_CAS_RETRY_DELAY)
            else:
                logger.error(
                    "CAS conflict for flow %s — all retries exhausted, aborting write",
                    mind_map_id,
                )
                raise


# ---------------------------------------------------------------------------
# Core flow tools
# ---------------------------------------------------------------------------


async def teaching_flow_start(
    pool: asyncpg.Pool,
    topic: str,
    goal: str | None = None,
) -> dict[str, Any]:
    """Start a new teaching flow for a topic.

    This is the mandatory entry point for any new curriculum. Call this FIRST
    whenever the user wants to learn a new topic. Never produce a curriculum
    plan as conversational text without calling this function to persist it.

    Before calling, check ``mind_map_list(status="active")`` for existing maps
    on similar topics — prefer extending an existing map (via
    ``mind_map_node_create`` / ``mind_map_edge_create`` + ``curriculum_replan``)
    over creating a new one.

    Creates a mind map row, initializes KV state at PENDING, immediately
    transitions to DIAGNOSING, and returns the resulting flow state dict.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    topic:
        Human-readable topic title (e.g. "Python", "Calculus").
    goal:
        Optional learning goal stored in mind map metadata.

    Returns
    -------
    dict
        Flow state dict after transition to DIAGNOSING.
    """
    async with _transaction(pool) as connection:
        # Create the mind map
        mind_map_id = await mind_map_create(connection, topic)

        # Store goal in metadata if provided
        if goal is not None:
            await connection.execute(
                """
                UPDATE education.mind_maps
                SET metadata = metadata || $1::jsonb, updated_at = now()
                WHERE id = $2
                """,
                {"goal": goal},
                mind_map_id,
            )

        # Initialize flow state at PENDING
        initial_state = _initial_flow_state(mind_map_id)
        await state_set(connection, _flow_key(mind_map_id), initial_state)

        # Immediately advance to DIAGNOSING
        return await teaching_flow_advance(connection, mind_map_id)


async def teaching_flow_get(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> dict[str, Any] | None:
    """Read current flow state from the KV store.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.

    Returns
    -------
    dict or None
        Current flow state dict, or None if no flow exists for this mind map.
    """
    return await state_get(pool, _flow_key(mind_map_id))


async def teaching_flow_advance(
    pool: asyncpg.Pool,
    mind_map_id: str,
) -> dict[str, Any]:
    """Advance the teaching flow state machine to the next state.

    Computes the valid next state based on the current state and frontier,
    writes the new state atomically (CAS), and returns the updated state.

    Entering TEACHING also records ``current_technique`` — the evidence-based
    technique the concept's ``metadata.concept_type`` calls for, with the
    principle behind it, because the ephemeral teaching session reads the flow
    state and nothing else. It is cleared on every other transition.

    Valid transitions:
    - pending → diagnosing
    - diagnosing → planning
    - planning → teaching (sets current_node_id from frontier, phase=explaining,
      current_technique from the node's concept_type)
    - teaching → quizzing (clears current_phase)
    - quizzing → teaching (if frontier has unmastered nodes)
    - quizzing → reviewing (if no frontier nodes but not all mastered)
    - quizzing → completed (if all nodes mastered)
    - reviewing → teaching (if frontier has unmastered nodes)
    - reviewing → completed (if all nodes mastered)

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.

    Returns
    -------
    dict
        The updated flow state dict.

    Raises
    ------
    ValueError
        If no flow exists, the transition is invalid, or state invariants
        would be violated after the transition.
    """
    state, version = await _get_state_with_version(pool, mind_map_id)
    if state is None:
        raise ValueError(f"No flow found for mind_map_id {mind_map_id!r}")

    current_status = state["status"]

    if current_status in _TERMINAL_STATES:
        raise ValueError(
            f"Cannot advance flow {mind_map_id!r}: status is {current_status!r} (terminal)"
        )

    # Build the new state
    new_state = dict(state)
    new_state["last_session_at"] = _now_iso()
    new_state["session_count"] = state.get("session_count", 0) + 1

    if current_status == "pending":
        new_state["status"] = "diagnosing"
        new_state["current_node_id"] = None
        new_state["current_phase"] = None
        new_state["current_technique"] = None

    elif current_status == "diagnosing":
        new_state["status"] = "planning"
        new_state["current_node_id"] = None
        new_state["current_phase"] = None
        new_state["current_technique"] = None

    elif current_status == "planning":
        map_status = await pool.fetchval(
            "SELECT status FROM education.mind_maps WHERE id = $1", mind_map_id
        )
        if map_status != "active":
            raise ValueError("Cannot advance to teaching: curriculum has not been generated")
        # Advance to teaching: find first frontier node
        next_node = await _determine_next_node(pool, mind_map_id)
        if next_node is None:
            raise ValueError(
                f"Cannot advance from planning to teaching: no frontier nodes "
                f"found for mind_map_id {mind_map_id!r}"
            )
        _enter_teaching(new_state, next_node)

    elif current_status == "teaching":
        new_state["status"] = "quizzing"
        new_state["current_phase"] = None
        new_state["current_technique"] = None
        # current_node_id stays the same

    elif current_status == "quizzing":
        # Branch: check frontier
        if await _all_nodes_mastered(pool, mind_map_id):
            new_state["status"] = "completed"
            new_state["current_node_id"] = None
            new_state["current_phase"] = None
            new_state["current_technique"] = None
            # Update mind map to completed
            if (
                await pool.fetchval(
                    "SELECT status FROM education.mind_maps WHERE id = $1", mind_map_id
                )
                != "completed"
            ):
                await mind_map_update_status(pool, mind_map_id, "completed")
        else:
            next_node = await _determine_next_node(pool, mind_map_id)
            if next_node is not None:
                _enter_teaching(new_state, next_node)
            else:
                # No frontier but not all mastered: go to reviewing
                new_state["status"] = "reviewing"
                new_state["current_phase"] = None
                new_state["current_technique"] = None
                # current_node_id unchanged

    elif current_status == "reviewing":
        # Branch: check all mastered or frontier
        if await _all_nodes_mastered(pool, mind_map_id):
            new_state["status"] = "completed"
            new_state["current_node_id"] = None
            new_state["current_phase"] = None
            new_state["current_technique"] = None
            if (
                await pool.fetchval(
                    "SELECT status FROM education.mind_maps WHERE id = $1", mind_map_id
                )
                != "completed"
            ):
                await mind_map_update_status(pool, mind_map_id, "completed")
        else:
            next_node = await _determine_next_node(pool, mind_map_id)
            if next_node is not None:
                _enter_teaching(new_state, next_node)
            else:
                # Remain in reviewing (no teachable frontier yet)
                new_state["status"] = "reviewing"
                new_state["current_phase"] = None
                new_state["current_technique"] = None

    else:
        raise ValueError(f"Unknown flow status {current_status!r} for mind_map_id {mind_map_id!r}")

    # Validate state invariants before writing
    _validate_state_invariants(new_state)

    # Write atomically
    await _write_state_cas(pool, mind_map_id, new_state, expected_version=version)

    return new_state


async def teaching_flow_abandon(
    pool: asyncpg.Pool,
    mind_map_id: str,
    *,
    schedule_delete: ScheduleDeleteFn = _default_schedule_delete,
) -> None:
    """Abandon a teaching flow and clean up pending review schedules.

    Sets flow status to 'abandoned', updates the mind map status, and
    deletes all pending review scheduled tasks for nodes in this mind map.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.
    schedule_delete:
        Async callable for deleting a scheduled task by name.

    Raises
    ------
    ValueError
        If no flow exists for this mind map, or the flow is already terminal.
    """
    async with _transaction(pool) as connection:
        state, version = await _get_state_with_version(connection, mind_map_id)
        if state is None:
            raise ValueError(f"No flow found for mind_map_id {mind_map_id!r}")
        if state["status"] in _TERMINAL_STATES:
            raise ValueError(
                f"Cannot abandon flow {mind_map_id!r}: terminal state {state['status']}"
            )
        await mind_map_update_status(connection, mind_map_id, "abandoned")
        new_state = dict(state)
        new_state.update(
            status="abandoned",
            last_session_at=_now_iso(),
            current_phase=None,
            current_technique=None,
        )
        await _write_state_cas(connection, mind_map_id, new_state, expected_version=version)

    # Clean up pending review schedules for all nodes in this map
    await _cleanup_review_schedules(pool, mind_map_id, schedule_delete=schedule_delete)


async def _cleanup_review_schedules(
    pool: asyncpg.Pool,
    mind_map_id: str,
    *,
    schedule_delete: ScheduleDeleteFn,
) -> int:
    """Delete all pending review schedules for nodes in a mind map.

    Returns the count of deleted schedules.
    """
    try:
        node_rows = await pool.fetch(
            "SELECT id FROM education.mind_map_nodes WHERE mind_map_id = $1",
            mind_map_id,
        )
    except Exception:
        logger.warning("Could not fetch nodes for schedule cleanup of map %s", mind_map_id)
        return 0

    deleted = 0

    for row in node_rows:
        node_id = str(row["id"])
        # Get all review schedule names for this node
        schedule_names = await _list_node_schedule_names(pool, node_id)
        for name in schedule_names:
            try:
                await schedule_delete(name)
                deleted += 1
            except Exception:
                pass

    # Delete the batch schedule for the map (if any)
    batch_name = f"review-{mind_map_id}-batch"
    try:
        batch_names = await _list_batch_schedule_names(pool, mind_map_id)
        for name in batch_names:
            await schedule_delete(name)
            deleted += 1
    except Exception:
        # Try by canonical name anyway
        try:
            await schedule_delete(batch_name)
            deleted += 1
        except Exception:
            pass

    return deleted


async def _list_node_schedule_names(pool: asyncpg.Pool, node_id: str) -> list[str]:
    """Return all known review schedule names for a node."""
    try:
        rows = await pool.fetch(
            "SELECT name FROM scheduled_tasks WHERE name LIKE $1",
            f"review-{node_id}-rep%",
        )
        return [str(row["name"]) for row in rows]
    except Exception:
        return []


async def _list_batch_schedule_names(pool: asyncpg.Pool, mind_map_id: str) -> list[str]:
    """Return the batch schedule name for a map if it exists."""
    batch_name = f"review-{mind_map_id}-batch"
    try:
        rows = await pool.fetch(
            "SELECT name FROM scheduled_tasks WHERE name = $1",
            batch_name,
        )
        return [str(row["name"]) for row in rows]
    except Exception:
        return [batch_name]


async def teaching_flow_list(
    pool: asyncpg.Pool,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List teaching flows with optional status filter.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    status:
        Optional status filter (e.g. 'teaching', 'completed'). When None,
        all flows are returned.

    Returns
    -------
    list of dict
        Each entry includes: mind_map_id, title, status, session_count,
        started_at, last_session_at, mastery_pct.
        Ordered by last_session_at DESC NULLS LAST.
    """
    # Fetch all mind maps (optionally filtered by status from KV perspective)
    rows = await pool.fetch(
        "SELECT id, title FROM education.mind_maps ORDER BY created_at DESC",
    )

    results: list[dict[str, Any]] = []

    for row in rows:
        map_id = str(row["id"])
        title = str(row["title"])

        flow_state = await state_get(pool, _flow_key(map_id))
        if flow_state is None:
            continue

        flow_status = flow_state.get("status", "unknown")

        # Apply status filter
        if status is not None and flow_status != status:
            continue

        # Compute mastery percentage
        try:
            summary = await mastery_get_map_summary(pool, map_id)
            total = summary["total_nodes"]
            mastered = summary["mastered_count"]
            mastery_pct = mastered / total if total > 0 else 0.0
        except Exception:
            mastery_pct = 0.0

        results.append(
            {
                "mind_map_id": map_id,
                "title": title,
                "status": flow_status,
                "session_count": flow_state.get("session_count", 0),
                "started_at": flow_state.get("started_at"),
                "last_session_at": flow_state.get("last_session_at"),
                "mastery_pct": mastery_pct,
            }
        )

    # Sort by last_session_at DESC NULLS LAST
    results.sort(
        key=lambda r: (r["last_session_at"] is None, r["last_session_at"] or ""),
        reverse=True,
    )
    # Nulls last: items with None last_session_at sort to the end
    results.sort(key=lambda r: r["last_session_at"] is None)

    return results


# ---------------------------------------------------------------------------
# Session context assembly
# ---------------------------------------------------------------------------


async def assemble_session_context(
    pool: asyncpg.Pool,
    mind_map_id: str,
    *,
    fetch_memory_context: Callable[[], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the structured context block for an ephemeral session.

    Gathers four components in order:
    1. Current flow state from KV store
    2. Frontier nodes from DB query
    3. Recent quiz responses (last 10) for the current node
    4. Memory context (fail-open on error)

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.
    fetch_memory_context:
        Optional async callable that returns memory context. If it raises,
        the failure is logged and the session proceeds without it.

    Returns
    -------
    dict with keys: flow_state, frontier, recent_responses, memory_context.
    """
    # 1. Flow state
    flow_state = await state_get(pool, _flow_key(mind_map_id))

    # 2. Frontier nodes
    try:
        frontier = await mind_map_frontier(pool, mind_map_id)
    except Exception:
        logger.warning("Failed to fetch frontier for mind_map %s", mind_map_id)
        frontier = []

    # 3. Recent quiz responses for current node
    recent_responses: list[dict[str, Any]] = []
    if flow_state is not None:
        current_node_id = flow_state.get("current_node_id")
        if current_node_id is not None:
            try:
                rows = await pool.fetch(
                    """
                    SELECT question_text, user_answer, quality, response_type, responded_at
                    FROM education.quiz_responses
                    WHERE node_id = $1
                    ORDER BY responded_at DESC
                    LIMIT 10
                    """,
                    current_node_id,
                )
                recent_responses = [dict(row) for row in rows]
                # Serialize datetimes and UUIDs for JSON compatibility
                for resp in recent_responses:
                    for k, v in resp.items():
                        if isinstance(v, _dt):
                            resp[k] = v.isoformat()
                        elif isinstance(v, _uuid.UUID):
                            resp[k] = str(v)
            except Exception:
                logger.warning("Failed to fetch recent responses for node %s", current_node_id)

    # 4. Memory context (fail-open)
    memory_context: Any = None
    if fetch_memory_context is not None:
        try:
            memory_context = await fetch_memory_context()
        except Exception:
            logger.warning("fetch_memory_context() failed — proceeding without memory context")

    return {
        "flow_state": flow_state,
        "frontier": frontier,
        "recent_responses": recent_responses,
        "memory_context": memory_context,
    }


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------


async def check_stale_flows(
    pool: asyncpg.Pool,
    *,
    stale_days: int = _STALE_DAYS,
    schedule_delete: ScheduleDeleteFn = _default_schedule_delete,
) -> list[str]:
    """Sweep stalled drafts and inactive populated maps, including flow-less maps.

    Empty drafts use their strict 24-hour creation boundary. Populated maps use
    flow inactivity, or newest node activity when no flow exists. Completed and
    all-mastered maps remain unchanged; abandoned maps retry only schedule cleanup.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    stale_days:
        Number of days of inactivity before a flow is considered stale.
        Defaults to 30.
    schedule_delete:
        Async callable for deleting a scheduled task by name.

    Returns
    -------
    list of str
        UUIDs of mind maps that were abandoned by this check.
    """
    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(days=stale_days)
    draft_cutoff = now - timedelta(hours=24)
    rows = await pool.fetch(
        """SELECT m.id, m.status, m.created_at,
                  (SELECT count(*) FROM education.mind_map_nodes n
                   WHERE n.mind_map_id = m.id) AS node_count,
                  (SELECT max(updated_at) FROM education.mind_map_nodes n
                   WHERE n.mind_map_id = m.id) AS last_activity,
                  (SELECT bool_and(mastery_status = 'mastered') FROM education.mind_map_nodes n
                   WHERE n.mind_map_id = m.id) AS all_mastered
           FROM education.mind_maps m WHERE m.status IN ('draft', 'active', 'abandoned')"""
    )
    abandoned: list[str] = []
    for row in rows:
        map_id = str(row["id"])
        flow_state = await state_get(pool, _flow_key(map_id))
        if row["status"] == "abandoned":
            # Repeat only idempotent schedule cleanup after a prior committed abandonment.
            await _cleanup_review_schedules(pool, map_id, schedule_delete=schedule_delete)
            continue
        if (flow_state and flow_state.get("status") in _TERMINAL_STATES) or row.get("all_mastered"):
            continue
        empty_draft = row["status"] == "draft" and row["node_count"] == 0
        if empty_draft and row["created_at"] >= draft_cutoff:
            continue
        stalled_draft = empty_draft and row["created_at"] < draft_cutoff
        if flow_state:
            activity = flow_state.get("last_session_at")
            try:
                last_activity = datetime.fromisoformat(activity) if activity else None
                if last_activity and last_activity.tzinfo is None:
                    last_activity = last_activity.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                last_activity = None
        else:
            last_activity = row["last_activity"]
        if not stalled_draft and not (last_activity and last_activity < cutoff):
            continue
        if flow_state:
            await teaching_flow_abandon(pool, map_id, schedule_delete=schedule_delete)
        else:
            await mind_map_update_status(pool, map_id, "abandoned")
            await _cleanup_review_schedules(pool, map_id, schedule_delete=schedule_delete)
        abandoned.append(map_id)
    return abandoned
