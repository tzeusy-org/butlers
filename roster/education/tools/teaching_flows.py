"""Education butler — teaching flow state machine.

Provides the complete teaching flow lifecycle:
- teaching_flow_start: creates mind map, initializes and advances to DIAGNOSING
- teaching_flow_get: reads current flow state from KV store
- teaching_flow_advance: drives the state machine through all transitions
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
import json
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
from butlers.tools.education.mastery import mastery_get_map_summary
from butlers.tools.education.mind_map_queries import mind_map_frontier
from butlers.tools.education.mind_maps import mind_map_create, mind_map_update_status

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

    if status in _NODE_REQUIRED_STATES and node_id is None:
        raise ValueError(f"current_node_id must be non-null when status is {status!r}")
    if status in _PHASE_REQUIRED_STATES and phase is None:
        raise ValueError(f"current_phase must be non-null when status is {status!r}")
    if status not in _PHASE_REQUIRED_STATES and phase is not None:
        # current_phase must be null in all other states
        raise ValueError(f"current_phase must be null when status is {status!r}, got {phase!r}")


async def _determine_next_node(pool: asyncpg.Pool, mind_map_id: str) -> str | None:
    """Return the ID of the highest-priority frontier node, or None."""
    nodes = await mind_map_frontier(pool, mind_map_id)
    if nodes:
        return str(nodes[0]["id"])
    return None


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
    # Create the mind map
    mind_map_id = await mind_map_create(pool, topic)

    # Store goal in metadata if provided
    if goal is not None:
        await pool.execute(
            """
            UPDATE education.mind_maps
            SET metadata = metadata || $1::jsonb, updated_at = now()
            WHERE id = $2
            """,
            json.dumps({"goal": goal}),
            mind_map_id,
        )

    # Initialize flow state at PENDING
    initial_state = _initial_flow_state(mind_map_id)
    await state_set(pool, _flow_key(mind_map_id), initial_state)

    # Immediately advance to DIAGNOSING
    return await teaching_flow_advance(pool, mind_map_id)


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

    Valid transitions:
    - pending → diagnosing
    - diagnosing → planning
    - planning → teaching (sets current_node_id from frontier, phase=explaining)
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

    elif current_status == "diagnosing":
        new_state["status"] = "planning"
        new_state["current_node_id"] = None
        new_state["current_phase"] = None

    elif current_status == "planning":
        # Advance to teaching: find first frontier node
        next_node_id = await _determine_next_node(pool, mind_map_id)
        if next_node_id is None:
            raise ValueError(
                f"Cannot advance from planning to teaching: no frontier nodes "
                f"found for mind_map_id {mind_map_id!r}"
            )
        new_state["status"] = "teaching"
        new_state["current_node_id"] = next_node_id
        new_state["current_phase"] = "explaining"

    elif current_status == "teaching":
        new_state["status"] = "quizzing"
        new_state["current_phase"] = None
        # current_node_id stays the same

    elif current_status == "quizzing":
        # Branch: check frontier
        if await _all_nodes_mastered(pool, mind_map_id):
            new_state["status"] = "completed"
            new_state["current_node_id"] = None
            new_state["current_phase"] = None
            # Update mind map to completed
            await mind_map_update_status(pool, mind_map_id, "completed")
        else:
            next_node_id = await _determine_next_node(pool, mind_map_id)
            if next_node_id is not None:
                new_state["status"] = "teaching"
                new_state["current_node_id"] = next_node_id
                new_state["current_phase"] = "explaining"
            else:
                # No frontier but not all mastered: go to reviewing
                new_state["status"] = "reviewing"
                new_state["current_phase"] = None
                # current_node_id unchanged

    elif current_status == "reviewing":
        # Branch: check all mastered or frontier
        if await _all_nodes_mastered(pool, mind_map_id):
            new_state["status"] = "completed"
            new_state["current_node_id"] = None
            new_state["current_phase"] = None
            await mind_map_update_status(pool, mind_map_id, "completed")
        else:
            next_node_id = await _determine_next_node(pool, mind_map_id)
            if next_node_id is not None:
                new_state["status"] = "teaching"
                new_state["current_node_id"] = next_node_id
                new_state["current_phase"] = "explaining"
            else:
                # Remain in reviewing (no teachable frontier yet)
                new_state["status"] = "reviewing"
                new_state["current_phase"] = None

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
    state, version = await _get_state_with_version(pool, mind_map_id)
    if state is None:
        raise ValueError(f"No flow found for mind_map_id {mind_map_id!r}")

    current_status = state["status"]
    if current_status in _TERMINAL_STATES:
        raise ValueError(
            f"Cannot abandon flow {mind_map_id!r}: already in terminal state {current_status!r}"
        )

    new_state = dict(state)
    new_state["status"] = "abandoned"
    new_state["last_session_at"] = _now_iso()
    new_state["current_phase"] = None

    await _write_state_cas(pool, mind_map_id, new_state, expected_version=version)

    # Update mind map status
    try:
        await mind_map_update_status(pool, mind_map_id, "abandoned")
    except ValueError:
        logger.warning("Mind map %s not found when abandoning flow", mind_map_id)

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
    """Weekly staleness check — auto-abandon flows inactive for > stale_days.

    Scans all active (non-terminal) flows. Any flow whose last_session_at is
    more than stale_days in the past is abandoned.

    Completed and abandoned flows are skipped.

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
    cutoff = datetime.now(tz=UTC) - timedelta(days=stale_days)

    # Fetch all mind maps with active flows
    rows = await pool.fetch(
        "SELECT id FROM education.mind_maps",
    )

    abandoned: list[str] = []

    for row in rows:
        map_id = str(row["id"])
        flow_state = await state_get(pool, _flow_key(map_id))
        if flow_state is None:
            continue

        status = flow_state.get("status", "unknown")
        if status in _TERMINAL_STATES:
            continue

        last_session_at_str = flow_state.get("last_session_at")
        if last_session_at_str is None:
            continue

        try:
            last_session_at = datetime.fromisoformat(last_session_at_str)
            # Ensure timezone-aware
            if last_session_at.tzinfo is None:
                last_session_at = last_session_at.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            logger.warning("Could not parse last_session_at for flow %s", map_id)
            continue

        if last_session_at < cutoff:
            logger.info(
                "Abandoning stale flow %s (last_session_at=%s, cutoff=%s)",
                map_id,
                last_session_at_str,
                cutoff.isoformat(),
            )
            try:
                await teaching_flow_abandon(pool, map_id, schedule_delete=schedule_delete)
                abandoned.append(map_id)
            except Exception:
                logger.exception("Failed to abandon stale flow %s", map_id)

    return abandoned
