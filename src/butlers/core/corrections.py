"""Error-recovery correction system for butler sessions.

Provides the `correct` core MCP tool implementation, enabling LLM sessions to
fix mistakes made by previous sessions. Supports four correction types:

- data_correction: Fix incorrect data in the state store
- memory_deletion: Retract a wrong fact, episode, or rule from memory
- misroute: Re-dispatch a message that was sent to the wrong butler
- action_reversal: Reverse or cancel a mistaken action (best-effort)

This module is append-only: all correction attempts (success or failure) are
recorded to the corrections table for audit.

Rate limiting: max 10 corrections per correcting_session per rolling hour.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any


def _json_default(obj: Any) -> Any:
    """JSON serialiser for types not handled by the stdlib encoder."""
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _dumps(obj: Any) -> str:
    """JSON-encode *obj*, handling UUIDs and datetimes."""
    return json.dumps(obj, default=_json_default)


def _json_safe(obj: Any) -> Any:
    """Convert *obj* to a JSON-safe Python value (dicts, lists, scalars).

    Handles UUIDs and datetimes by converting them to strings so the result
    can be passed directly to asyncpg's JSONB codec without a ``::jsonb`` cast.
    """
    return json.loads(_dumps(obj))


# ---------------------------------------------------------------------------
# Canonical tool description (spec § "Canonical Tool Description Text")
# ---------------------------------------------------------------------------

CORRECT_TOOL_DESCRIPTION = """\
correct: Fix mistakes from previous butler sessions. Use ONLY to correct past
errors, not for normal updates.

Types:
- data_correction: Fix incorrect data in state store (wrong value recorded)
- memory_deletion: Retract a wrong fact, episode, or rule from memory
- misroute: Message was sent to the wrong butler — reroute to correct one
- action_reversal: Reverse/cancel a mistaken action (best-effort)

NOT for: normal state updates (use state_set), routine memory management
(use memory tools), or new actions.

Required: correction_type, target_session_id (UUID of session that made the
mistake), description (what was wrong and why)
Optional: target_butler (query another butler's schema for cross-butler
corrections), correct_butler (for misroute), state_key/corrected_value
(for data_correction), memory_type/memory_id (for memory_deletion),
action_description (for action_reversal)
"""

# ---------------------------------------------------------------------------
# Failure message dictionary (spec § "Failure Message Dictionary")
# ---------------------------------------------------------------------------

FAILURE_MESSAGES: dict[str, str] = {
    "session_not_found": (
        "Session {id} does not exist. Run sessions_list to find the correct session UUID."
    ),
    "state_key_not_found": ("State key '{key}' not found. Use state_list to see available keys."),
    "memory_already_retracted": ("Memory {id} was already retracted on {date}. No action needed."),
    "memory_superseded": (
        "Memory {id} was superseded by {successor_id}. Correct the newer version instead."
    ),
    "butler_not_registered": (
        "Butler '{name}' not registered. Available butlers: {comma_separated_list}."
    ),
    "ingestion_event_expired": (
        "Original message expired (>30 days). Ask the user to re-send to butler '{correct_butler}'."
    ),
    "action_not_reversible": (
        "Action type '{type}' cannot be reversed. Reversible types: {comma_separated_list}."
    ),
    "unknown_correction_type": (
        "Unknown correction_type '{type}'. Valid types: data_correction, memory_deletion,"
        " misroute, action_reversal."
    ),
    "missing_required_parameter": (
        "Parameter '{param}' is required for correction_type '{type}'."
        " See tool description for required parameters."
    ),
    "session_no_ingestion_event": (
        "Session {id} was not triggered by an ingestion event"
        " (trigger_source='{source}'). Misroute corrections require a session"
        " spawned from message routing."
    ),
    "memory_not_found": (
        "Memory {id} of type '{memory_type}' not found."
        " Use memory_recall to verify the memory ID and type."
    ),
    "switchboard_unreachable": (
        "Cannot reach Switchboard for misroute re-dispatch."
        " Try again later or escalate to the user."
    ),
    "invalid_json_corrected_value": (
        "corrected_value is not valid JSON."
        " Provide a JSON-serializable value (dict, list, string, number, boolean, or null)."
    ),
}

# ---------------------------------------------------------------------------
# CorrectionType enum
# ---------------------------------------------------------------------------


class CorrectionType(StrEnum):
    """Supported correction types for the `correct` core MCP tool."""

    DATA_CORRECTION = "data_correction"
    MEMORY_DELETION = "memory_deletion"
    MISROUTE = "misroute"
    ACTION_REVERSAL = "action_reversal"


# ---------------------------------------------------------------------------
# Correction type decision tree (spec § "Correction Type Decision Tree")
# ---------------------------------------------------------------------------


def get_correction_type_for_situation(
    *,
    stored_data_wrong: bool,
    memory_wrong: bool,
    wrong_butler: bool,
    action_mistake: bool,
) -> CorrectionType | None:
    """Return the appropriate CorrectionType given a situational description.

    Decision tree (first-match wins):

    Is the mistake about STORED DATA (a state_get/state_set value is wrong)?
      YES → correction_type = data_correction
      NO  ↓

    Is the mistake about a MEMORY (a fact, episode, or rule that is wrong)?
      YES → correction_type = memory_deletion
      NO  ↓

    Did the message go to the WRONG BUTLER entirely?
      YES → correction_type = misroute
      NO  ↓

    Did the butler TAKE AN ACTION we want to undo (sent message, created event,
    set reminder, etc.)?
      YES → correction_type = action_reversal
      NO  ↓

    None of the above match.
      → You probably do not need the correct tool.
        For normal data updates, use state_set.
        For normal memory management, use memory tools directly.
        For new actions, use the appropriate action tool.
        If unsure, ask the user what specifically was wrong.
    """
    if stored_data_wrong:
        return CorrectionType.DATA_CORRECTION
    if memory_wrong:
        return CorrectionType.MEMORY_DELETION
    if wrong_butler:
        return CorrectionType.MISROUTE
    if action_mistake:
        return CorrectionType.ACTION_REVERSAL
    return None


# ---------------------------------------------------------------------------
# Rate limiting helpers
# ---------------------------------------------------------------------------

_RATE_LIMIT = 10
_RATE_WINDOW_HOURS = 1


def _validate_identifier(name: str | None) -> None:
    """Raise ValueError if *name* is not a safe SQL identifier (alphanumeric + underscore).

    This guards against SQL injection when schema or table names are interpolated
    directly into query strings.  Only call this for names that originate from
    caller-supplied (LLM-provided) input.
    """
    if name is None:
        return
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(
            f"Unsafe SQL identifier: {name!r}. "
            "Schema and butler names must contain only letters, digits, and underscores."
        )


async def _check_rate_limit(
    pool: Any,
    correcting_session_id: uuid.UUID,
    schema: str | None = None,
) -> str | None:
    """Return an error string if the correcting session has exceeded the rate limit.

    Counts corrections made by *correcting_session_id* within the last hour.
    Returns None if within limit.
    """
    _validate_identifier(schema)
    corrections_table = f"{schema}.corrections" if schema else "corrections"
    since = datetime.now(UTC) - timedelta(hours=_RATE_WINDOW_HOURS)
    count = await pool.fetchval(
        f"SELECT COUNT(*) FROM {corrections_table}"
        " WHERE correcting_session_id = $1 AND created_at >= $2",
        correcting_session_id,
        since,
    )
    if count is None:
        count = 0
    if count >= _RATE_LIMIT:
        return (
            f"Rate limit exceeded: {count} corrections in the past hour"
            f" (limit: {_RATE_LIMIT}). This may indicate a correction loop."
            " Ask the user to confirm before continuing."
        )
    return None


def _is_session_not_found_error(error_msg: str) -> bool:
    """Return True if *error_msg* is a 'session not found' precondition failure.

    When the target session does not exist the corrections table's FK constraint
    (target_session_id REFERENCES sessions) will reject any INSERT.  Handlers
    must skip the audit record in that case.
    """
    msg_lower = error_msg.lower()
    return "does not exist" in msg_lower or ("not found" in msg_lower and "session" in msg_lower)


# ---------------------------------------------------------------------------
# Append-only correction record insertion
# ---------------------------------------------------------------------------


async def create_correction(
    pool: Any,
    *,
    correction_type: CorrectionType,
    target_session_id: uuid.UUID,
    correcting_session_id: uuid.UUID,
    description: str,
    status: str,
    summary: str,
    original_data_snapshot: dict[str, Any] | None,
    correction_details: dict[str, Any] | None,
    schema: str | None = None,
) -> uuid.UUID:
    """Insert an immutable correction record and return the new correction UUID.

    This function ONLY performs INSERTs — no UPDATE or DELETE on the
    corrections table is ever issued. Callers must not bypass this function
    to mutate existing rows.

    Args:
        pool: asyncpg connection pool.
        correction_type: One of the four CorrectionType values.
        target_session_id: UUID of the session whose output is being corrected.
        correcting_session_id: UUID of the session performing the correction.
        description: Human-readable explanation of what was wrong and why.
        status: One of 'applied', 'partially_applied', 'failed'.
        summary: Human-readable outcome description.
        original_data_snapshot: The original data before correction (for audit).
        correction_details: Type-specific details of what was changed.
        schema: Optional schema name prefix (e.g. 'finance'). If None, uses
            the search_path default.

    Returns:
        UUID of the newly inserted correction record.
    """
    correction_id = uuid.uuid4()
    table = f"{schema}.corrections" if schema else "corrections"

    snapshot = _json_safe(original_data_snapshot) if original_data_snapshot is not None else None
    details = _json_safe(correction_details) if correction_details is not None else None

    await pool.execute(
        f"""
        INSERT INTO {table} (
            id,
            correction_type,
            target_session_id,
            correcting_session_id,
            description,
            status,
            summary,
            original_data_snapshot,
            correction_details,
            created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        correction_id,
        correction_type.value if isinstance(correction_type, CorrectionType) else correction_type,
        target_session_id,
        correcting_session_id,
        description,
        status,
        summary,
        snapshot,
        details,
        datetime.now(UTC),
    )
    return correction_id


# ---------------------------------------------------------------------------
# Precondition checkers
# ---------------------------------------------------------------------------


async def check_data_correction_preconditions(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    state_key: str,
    corrected_value: Any,
    schema: str | None = None,
) -> str | None:
    """Validate preconditions for a data_correction.

    Returns None on success, or an error message string on failure.
    """
    sessions_table = f"{schema}.sessions" if schema else "sessions"
    state_table = f"{schema}.state" if schema else "state"

    session_row = await pool.fetchrow(
        f"SELECT id FROM {sessions_table} WHERE id = $1",
        target_session_id,
    )
    if session_row is None:
        return FAILURE_MESSAGES["session_not_found"].format(id=str(target_session_id))

    state_row = await pool.fetchrow(
        f"SELECT key FROM {state_table} WHERE key = $1",
        state_key,
    )
    if state_row is None:
        return FAILURE_MESSAGES["state_key_not_found"].format(key=state_key)

    try:
        _dumps(corrected_value)
    except (TypeError, ValueError):
        return FAILURE_MESSAGES["invalid_json_corrected_value"]

    return None


async def check_memory_deletion_preconditions(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    memory_type: str,
    memory_id: uuid.UUID,
    schema: str | None = None,
) -> str | None:
    """Validate preconditions for a memory_deletion correction.

    Returns None on success, or an error message string on failure.
    """
    sessions_table = f"{schema}.sessions" if schema else "sessions"

    session_row = await pool.fetchrow(
        f"SELECT id FROM {sessions_table} WHERE id = $1",
        target_session_id,
    )
    if session_row is None:
        return FAILURE_MESSAGES["session_not_found"].format(id=str(target_session_id))

    # Determine memory table by type — only allowlisted types are accepted to
    # prevent SQL injection via a caller-supplied memory_type string.
    memory_table_map = {
        "fact": "facts",
        "episode": "episodes",
        "rule": "rules",
    }
    if memory_type not in memory_table_map:
        return FAILURE_MESSAGES["memory_not_found"].format(
            id=str(memory_id), memory_type=memory_type
        )
    raw_table = memory_table_map[memory_type]
    memory_table = f"{schema}.{raw_table}" if schema else raw_table

    # Query the memory row — each type has a different schema:
    # - facts: has validity (active/retracted/superseded/expired) and supersedes_id
    # - episodes: have expires_at; "forgotten" = expires_at in the past
    # - rules: have metadata JSONB; "forgotten" = metadata->>'forgotten' == 'true'
    # Use type-specific queries (uses "memory" token so mock dispatchers can match)
    if memory_type == "fact":
        memory_row = await pool.fetchrow(
            f"SELECT id, validity, supersedes_id FROM {memory_table}"
            f" /* memory validity check */ WHERE id = $1",
            memory_id,
        )
        if memory_row is None:
            return FAILURE_MESSAGES["memory_not_found"].format(
                id=str(memory_id), memory_type=memory_type
            )
        validity = memory_row.get("validity", "active")
        if validity == "retracted":
            date_str = "unknown"
            return FAILURE_MESSAGES["memory_already_retracted"].format(
                id=str(memory_id), date=date_str
            )
        if validity == "superseded":
            successor_id = memory_row.get("supersedes_id", "unknown")
            return FAILURE_MESSAGES["memory_superseded"].format(
                id=str(memory_id), successor_id=str(successor_id)
            )
    elif memory_type == "episode":
        memory_row = await pool.fetchrow(
            f"SELECT id, expires_at FROM {memory_table} /* memory validity check */ WHERE id = $1",
            memory_id,
        )
        if memory_row is None:
            return FAILURE_MESSAGES["memory_not_found"].format(
                id=str(memory_id), memory_type=memory_type
            )
        # Episodes are "forgotten" if expires_at is already in the past
        expires_at = memory_row.get("expires_at")
        if expires_at is not None and expires_at <= datetime.now(UTC):
            return FAILURE_MESSAGES["memory_already_retracted"].format(
                id=str(memory_id), date=expires_at.isoformat()
            )
    else:  # rule
        memory_row = await pool.fetchrow(
            f"SELECT id, metadata FROM {memory_table} /* memory validity check */ WHERE id = $1",
            memory_id,
        )
        if memory_row is None:
            return FAILURE_MESSAGES["memory_not_found"].format(
                id=str(memory_id), memory_type=memory_type
            )
        import json as _json

        metadata_raw = memory_row.get("metadata") or {}
        metadata = _json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
        if metadata.get("forgotten"):
            return FAILURE_MESSAGES["memory_already_retracted"].format(
                id=str(memory_id), date="unknown"
            )

    return None


async def check_misroute_preconditions(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    correct_butler: str,
    registered_butlers: list[str] | None,
    schema: str | None = None,
) -> str | None:
    """Validate preconditions for a misroute correction.

    Returns None on success, or an error message string on failure.
    """
    sessions_table = f"{schema}.sessions" if schema else "sessions"

    session_row = await pool.fetchrow(
        f"SELECT id, trigger_source, ingestion_event_id FROM {sessions_table} WHERE id = $1",
        target_session_id,
    )
    if session_row is None:
        return FAILURE_MESSAGES["session_not_found"].format(id=str(target_session_id))

    ingestion_event_id = session_row.get("ingestion_event_id")
    if not ingestion_event_id:
        trigger_source = session_row.get("trigger_source", "unknown")
        return FAILURE_MESSAGES["session_no_ingestion_event"].format(
            id=str(target_session_id), source=trigger_source
        )

    if registered_butlers is not None and correct_butler not in registered_butlers:
        return FAILURE_MESSAGES["butler_not_registered"].format(
            name=correct_butler,
            comma_separated_list=", ".join(sorted(registered_butlers)),
        )

    return None


async def check_action_reversal_preconditions(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    action_description: str,
    schema: str | None = None,
) -> str | None:
    """Validate preconditions for an action_reversal correction.

    Returns None on success, or an error message string on failure.
    """
    sessions_table = f"{schema}.sessions" if schema else "sessions"

    session_row = await pool.fetchrow(
        f"SELECT id, tool_calls FROM {sessions_table} WHERE id = $1",
        target_session_id,
    )
    if session_row is None:
        return FAILURE_MESSAGES["session_not_found"].format(id=str(target_session_id))

    return None


# ---------------------------------------------------------------------------
# Query helpers (audit)
# ---------------------------------------------------------------------------


async def corrections_by_session(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    schema: str | None = None,
) -> list[dict[str, Any]]:
    """Return all corrections targeting *target_session_id*, ordered by created_at asc."""
    table = f"{schema}.corrections" if schema else "corrections"
    rows = await pool.fetch(
        f"SELECT * FROM {table} WHERE target_session_id = $1 ORDER BY created_at ASC",
        target_session_id,
    )
    return [dict(r) for r in rows]


async def corrections_for_session(
    pool: Any,
    *,
    correcting_session_id: uuid.UUID,
    schema: str | None = None,
) -> list[dict[str, Any]]:
    """Return all corrections initiated by *correcting_session_id*, ordered by created_at asc."""
    table = f"{schema}.corrections" if schema else "corrections"
    rows = await pool.fetch(
        f"SELECT * FROM {table} WHERE correcting_session_id = $1 ORDER BY created_at ASC",
        correcting_session_id,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Action reversal internals
# ---------------------------------------------------------------------------

# Tools that are considered reversible (best-effort set)
_REVERSIBLE_TOOL_NAMES: frozenset[str] = frozenset(
    {"remind", "schedule_create", "schedule_update", "schedule_delete"}
)


async def _attempt_action_reversal(
    tool_calls: list[dict[str, Any]],
    action_description: str,
) -> dict[str, list[str]]:
    """Attempt to categorise tool calls as reversed or irreversible.

    Returns a dict with keys:
        reversed: list of tool names that were (conceptually) reversed
        irreversible: list of tool names that cannot be reversed
    """
    reversed_tools: list[str] = []
    irreversible_tools: list[str] = []

    for call in tool_calls:
        tool_name = call.get("tool", "unknown")
        if tool_name in _REVERSIBLE_TOOL_NAMES:
            reversed_tools.append(tool_name)
        else:
            irreversible_tools.append(tool_name)

    return {"reversed": reversed_tools, "irreversible": irreversible_tools}


# ---------------------------------------------------------------------------
# Correction handlers
# ---------------------------------------------------------------------------


async def handle_data_correction(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    correcting_session_id: uuid.UUID,
    description: str,
    state_key: str,
    corrected_value: Any,
    target_butler: str | None = None,
    target_pool: Any | None = None,
    registered_butlers: list[str] | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    """Handle a data_correction: update a state key and record the correction.

    Cross-schema support: if *target_butler* is provided, reads and writes the
    state via *target_pool* (the target butler's DB pool). The correction record
    is always written to the CURRENT butler's corrections table (*pool*).

    Args:
        pool: The correcting butler's connection pool (correction record goes here).
        target_session_id: Session whose state is being corrected.
        correcting_session_id: Session performing the correction.
        description: What was wrong and why.
        state_key: The state key to correct.
        corrected_value: The new correct value.
        target_butler: If provided, the name of the butler schema to target.
        target_pool: If provided, the asyncpg pool for the target butler.
        registered_butlers: List of registered butler names (for validation).
        schema: Optional schema prefix for the current butler.

    Returns:
        Dict with keys: status, correction_id, summary, original_data_snapshot,
        correction_details.
    """
    # Rate limit check
    rate_error = await _check_rate_limit(pool, correcting_session_id, schema=schema)
    if rate_error:
        cid = await create_correction(
            pool,
            correction_type=CorrectionType.DATA_CORRECTION,
            target_session_id=target_session_id,
            correcting_session_id=correcting_session_id,
            description=description,
            status="failed",
            summary=rate_error,
            original_data_snapshot=None,
            correction_details=None,
            schema=schema,
        )
        return {
            "status": "failed",
            "correction_id": str(cid),
            "summary": rate_error,
            "original_data_snapshot": None,
            "correction_details": None,
        }

    # Cross-schema validation: if target_butler is given but no target_pool, validate
    # that the butler exists in the registered list before proceeding.
    if target_butler is not None:
        _validate_identifier(target_butler)
        effective_pool = target_pool if target_pool is not None else pool
        if registered_butlers is not None and target_butler not in registered_butlers:
            err = FAILURE_MESSAGES["butler_not_registered"].format(
                name=target_butler,
                comma_separated_list=", ".join(sorted(registered_butlers)),
            )
            cid = await create_correction(
                pool,
                correction_type=CorrectionType.DATA_CORRECTION,
                target_session_id=target_session_id,
                correcting_session_id=correcting_session_id,
                description=description,
                status="failed",
                summary=err,
                original_data_snapshot=None,
                correction_details={"target_butler": target_butler},
                schema=schema,
            )
            return {
                "status": "failed",
                "correction_id": str(cid),
                "summary": err,
                "original_data_snapshot": None,
                "correction_details": {"target_butler": target_butler},
            }
    else:
        effective_pool = pool

    # Precondition checks
    target_schema = target_butler if target_butler else schema
    precond_error = await check_data_correction_preconditions(
        effective_pool,
        target_session_id=target_session_id,
        state_key=state_key,
        corrected_value=corrected_value,
        schema=target_schema,
    )
    if precond_error:
        # Skip audit record when target session doesn't exist: the FK constraint
        # (corrections.target_session_id REFERENCES sessions) would reject the INSERT.
        if not _is_session_not_found_error(precond_error):
            cid = await create_correction(
                pool,
                correction_type=CorrectionType.DATA_CORRECTION,
                target_session_id=target_session_id,
                correcting_session_id=correcting_session_id,
                description=description,
                status="failed",
                summary=precond_error,
                original_data_snapshot=None,
                correction_details=None,
                schema=schema,
            )
            correction_id_str = str(cid)
        else:
            correction_id_str = ""
        return {
            "status": "failed",
            "correction_id": correction_id_str,
            "summary": precond_error,
            "original_data_snapshot": None,
            "correction_details": None,
        }

    # Snapshot original value
    state_table = f"{target_schema}.state" if target_schema else "state"
    state_row = await effective_pool.fetchrow(
        f"SELECT key, value FROM {state_table} WHERE key = $1",
        state_key,
    )
    original_value = dict(state_row) if state_row else None

    # Apply the correction (update state)
    now = datetime.now(UTC)
    await effective_pool.execute(
        f"UPDATE {state_table} SET value = $1, updated_at = $2 WHERE key = $3",
        corrected_value,
        now,
        state_key,
    )

    snapshot = {state_key: original_value.get("value") if original_value else None}
    correction_details: dict[str, Any] = {
        "state_key": state_key,
        "new_value": corrected_value,
    }
    if target_butler:
        correction_details["target_butler"] = target_butler

    cid = await create_correction(
        pool,
        correction_type=CorrectionType.DATA_CORRECTION,
        target_session_id=target_session_id,
        correcting_session_id=correcting_session_id,
        description=description,
        status="applied",
        summary=f"State key '{state_key}' corrected successfully.",
        original_data_snapshot=snapshot,
        correction_details=correction_details,
        schema=schema,
    )
    return {
        "status": "applied",
        "correction_id": str(cid),
        "summary": f"State key '{state_key}' corrected successfully.",
        "original_data_snapshot": snapshot,
        "correction_details": correction_details,
    }


async def handle_memory_deletion(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    correcting_session_id: uuid.UUID,
    description: str,
    memory_type: str,
    memory_id: uuid.UUID,
    target_butler: str | None = None,
    target_pool: Any | None = None,
    registered_butlers: list[str] | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    """Handle a memory_deletion: retract a memory and record the correction.

    Cross-schema support: if *target_butler* is provided, reads and retracts the
    memory via *target_pool* (the target butler's DB pool). The correction record
    is always written to the CURRENT butler's corrections table (*pool*).

    Args:
        pool: The correcting butler's connection pool (correction record goes here).
        target_session_id: Session whose memory is being corrected.
        correcting_session_id: Session performing the correction.
        description: What was wrong and why.
        memory_type: One of 'fact', 'episode', 'rule'.
        memory_id: UUID of the memory to retract.
        target_butler: If provided, the name of the butler schema to target.
        target_pool: If provided, the asyncpg pool for the target butler.
        registered_butlers: List of registered butler names (for validation).
        schema: Optional schema prefix for the current butler.

    Returns:
        Dict with keys: status, correction_id, summary, original_data_snapshot,
        correction_details.
    """
    # Rate limit check
    rate_error = await _check_rate_limit(pool, correcting_session_id, schema=schema)
    if rate_error:
        cid = await create_correction(
            pool,
            correction_type=CorrectionType.MEMORY_DELETION,
            target_session_id=target_session_id,
            correcting_session_id=correcting_session_id,
            description=description,
            status="failed",
            summary=rate_error,
            original_data_snapshot=None,
            correction_details=None,
            schema=schema,
        )
        return {
            "status": "failed",
            "correction_id": str(cid),
            "summary": rate_error,
            "original_data_snapshot": None,
            "correction_details": None,
        }

    # Cross-schema validation: if target_butler is given, validate it exists.
    if target_butler is not None:
        _validate_identifier(target_butler)
        effective_pool = target_pool if target_pool is not None else pool
        if registered_butlers is not None and target_butler not in registered_butlers:
            err = FAILURE_MESSAGES["butler_not_registered"].format(
                name=target_butler,
                comma_separated_list=", ".join(sorted(registered_butlers)),
            )
            cid = await create_correction(
                pool,
                correction_type=CorrectionType.MEMORY_DELETION,
                target_session_id=target_session_id,
                correcting_session_id=correcting_session_id,
                description=description,
                status="failed",
                summary=err,
                original_data_snapshot=None,
                correction_details={"target_butler": target_butler},
                schema=schema,
            )
            return {
                "status": "failed",
                "correction_id": str(cid),
                "summary": err,
                "original_data_snapshot": None,
                "correction_details": {"target_butler": target_butler},
            }
    else:
        effective_pool = pool

    # Precondition checks
    target_schema = target_butler if target_butler else schema
    precond_error = await check_memory_deletion_preconditions(
        effective_pool,
        target_session_id=target_session_id,
        memory_type=memory_type,
        memory_id=memory_id,
        schema=target_schema,
    )
    if precond_error:
        if not _is_session_not_found_error(precond_error):
            cid = await create_correction(
                pool,
                correction_type=CorrectionType.MEMORY_DELETION,
                target_session_id=target_session_id,
                correcting_session_id=correcting_session_id,
                description=description,
                status="failed",
                summary=precond_error,
                original_data_snapshot=None,
                correction_details=None,
                schema=schema,
            )
            correction_id_str = str(cid)
        else:
            correction_id_str = ""
        return {
            "status": "failed",
            "correction_id": correction_id_str,
            "summary": precond_error,
            "original_data_snapshot": None,
            "correction_details": None,
        }

    # Snapshot original memory — memory_type is already validated by
    # check_memory_deletion_preconditions, so only allowlisted values reach here.
    memory_table_map = {"fact": "facts", "episode": "episodes", "rule": "rules"}
    raw_table = memory_table_map.get(memory_type, "facts")  # fallback is unreachable
    memory_table = f"{target_schema}.{raw_table}" if target_schema else raw_table

    # Snapshot original memory content before retraction
    memory_row = await effective_pool.fetchrow(
        f"SELECT * FROM {memory_table} /* memory snapshot */ WHERE id = $1",
        memory_id,
    )
    snapshot = dict(memory_row) if memory_row else {}

    # Retract the memory via memory_forget
    await memory_forget(effective_pool, memory_type, str(memory_id))

    correction_details = {
        "memory_type": memory_type,
        "memory_id": str(memory_id),
    }
    if target_butler:
        correction_details["target_butler"] = target_butler
    cid = await create_correction(
        pool,
        correction_type=CorrectionType.MEMORY_DELETION,
        target_session_id=target_session_id,
        correcting_session_id=correcting_session_id,
        description=description,
        status="applied",
        summary=f"Memory {memory_id} ({memory_type}) retracted successfully.",
        original_data_snapshot=snapshot,
        correction_details=correction_details,
        schema=schema,
    )
    return {
        "status": "applied",
        "correction_id": str(cid),
        "summary": f"Memory {memory_id} ({memory_type}) retracted successfully.",
        "original_data_snapshot": snapshot,
        "correction_details": correction_details,
    }


async def handle_misroute(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    correcting_session_id: uuid.UUID,
    description: str,
    correct_butler: str,
    registered_butlers: list[str] | None = None,
    switchboard_client: Any,
    original_butler: str | None = None,
    target_butler: str | None = None,
    target_pool: Any | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    """Handle a misroute correction: re-dispatch a message to the correct butler.

    Cross-schema support: if *target_butler* is provided, reads the session from
    *target_pool* (the target butler's DB pool). The correction record is always
    written to the CURRENT butler's corrections table (*pool*).

    Args:
        pool: The correcting butler's connection pool (correction record goes here).
        target_session_id: Session that received the incorrectly-routed message.
        correcting_session_id: Session performing the correction.
        description: What was wrong and why.
        correct_butler: The butler that should have received the message.
        registered_butlers: List of registered butler names.
        switchboard_client: Client with a call_tool(tool_name, **kwargs) method.
        original_butler: Name of the butler that originally received the message.
        target_butler: If provided, the name of the butler schema to read the session from.
        target_pool: If provided, the asyncpg pool for the target butler.
        schema: Optional schema prefix for the current butler.

    Returns:
        Dict with keys: status, correction_id, summary, original_data_snapshot,
        correction_details.
    """
    # Rate limit check
    rate_error = await _check_rate_limit(pool, correcting_session_id, schema=schema)
    if rate_error:
        cid = await create_correction(
            pool,
            correction_type=CorrectionType.MISROUTE,
            target_session_id=target_session_id,
            correcting_session_id=correcting_session_id,
            description=description,
            status="failed",
            summary=rate_error,
            original_data_snapshot=None,
            correction_details=None,
            schema=schema,
        )
        return {
            "status": "failed",
            "correction_id": str(cid),
            "summary": rate_error,
            "original_data_snapshot": None,
            "correction_details": None,
        }

    # Cross-schema validation: if target_butler is given, validate it exists.
    if target_butler is not None:
        _validate_identifier(target_butler)
        effective_pool = target_pool if target_pool is not None else pool
        if registered_butlers is not None and target_butler not in registered_butlers:
            err = FAILURE_MESSAGES["butler_not_registered"].format(
                name=target_butler,
                comma_separated_list=", ".join(sorted(registered_butlers)),
            )
            cid = await create_correction(
                pool,
                correction_type=CorrectionType.MISROUTE,
                target_session_id=target_session_id,
                correcting_session_id=correcting_session_id,
                description=description,
                status="failed",
                summary=err,
                original_data_snapshot=None,
                correction_details={"target_butler": target_butler},
                schema=schema,
            )
            return {
                "status": "failed",
                "correction_id": str(cid),
                "summary": err,
                "original_data_snapshot": None,
                "correction_details": {"target_butler": target_butler},
            }
    else:
        effective_pool = pool

    # Precondition checks
    target_schema = target_butler if target_butler else schema
    precond_error = await check_misroute_preconditions(
        effective_pool,
        target_session_id=target_session_id,
        correct_butler=correct_butler,
        registered_butlers=registered_butlers,
        schema=target_schema,
    )
    if precond_error:
        if not _is_session_not_found_error(precond_error):
            cid = await create_correction(
                pool,
                correction_type=CorrectionType.MISROUTE,
                target_session_id=target_session_id,
                correcting_session_id=correcting_session_id,
                description=description,
                status="failed",
                summary=precond_error,
                original_data_snapshot=None,
                correction_details=None,
                schema=schema,
            )
            correction_id_str = str(cid)
        else:
            correction_id_str = ""
        return {
            "status": "failed",
            "correction_id": correction_id_str,
            "summary": precond_error,
            "original_data_snapshot": None,
            "correction_details": None,
        }

    # Snapshot original routing
    sessions_table = f"{target_schema}.sessions" if target_schema else "sessions"
    session_row = await effective_pool.fetchrow(
        f"SELECT id, trigger_source, ingestion_event_id FROM {sessions_table} WHERE id = $1",
        target_session_id,
    )
    original_request_id = None
    if session_row:
        original_request_id = str(session_row.get("ingestion_event_id", ""))

    snapshot = {
        "original_butler": original_butler,
        "original_request_id": original_request_id,
    }

    # Call Switchboard correct_route
    try:
        result = await switchboard_client.call_tool(
            "correct_route",
            target_session_id=str(target_session_id),
            correct_butler=correct_butler,
        )
    except Exception as exc:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "Switchboard call_tool('correct_route') failed: %s", exc, exc_info=True
        )
        err = FAILURE_MESSAGES["switchboard_unreachable"]
        cid = await create_correction(
            pool,
            correction_type=CorrectionType.MISROUTE,
            target_session_id=target_session_id,
            correcting_session_id=correcting_session_id,
            description=description,
            status="failed",
            summary=err,
            original_data_snapshot=snapshot,
            correction_details=None,
            schema=schema,
        )
        return {
            "status": "failed",
            "correction_id": str(cid),
            "summary": err,
            "original_data_snapshot": snapshot,
            "correction_details": None,
        }

    # Check if switchboard reported an expired event
    if isinstance(result, dict) and result.get("status") == "expired":
        err = FAILURE_MESSAGES["ingestion_event_expired"].format(correct_butler=correct_butler)
        correction_details: dict[str, Any] = {
            "correct_butler": correct_butler,
            "original_butler": original_butler,
            "original_request_id": original_request_id,
        }
        cid = await create_correction(
            pool,
            correction_type=CorrectionType.MISROUTE,
            target_session_id=target_session_id,
            correcting_session_id=correcting_session_id,
            description=description,
            status="failed",
            summary=err,
            original_data_snapshot=snapshot,
            correction_details=correction_details,
            schema=schema,
        )
        return {
            "status": "failed",
            "correction_id": str(cid),
            "summary": err,
            "original_data_snapshot": snapshot,
            "correction_details": correction_details,
        }

    # Success: extract new_session_id
    new_session_id = result.get("new_session_id") if isinstance(result, dict) else None
    correction_details = {
        "correct_butler": correct_butler,
        "new_session_id": new_session_id,
        "original_butler": original_butler,
        "original_request_id": original_request_id,
    }
    if target_butler:
        correction_details["target_butler"] = target_butler
    summary = f"Message re-dispatched to butler '{correct_butler}'. New session: {new_session_id}."
    cid = await create_correction(
        pool,
        correction_type=CorrectionType.MISROUTE,
        target_session_id=target_session_id,
        correcting_session_id=correcting_session_id,
        description=description,
        status="applied",
        summary=summary,
        original_data_snapshot=snapshot,
        correction_details=correction_details,
        schema=schema,
    )
    return {
        "status": "applied",
        "correction_id": str(cid),
        "summary": summary,
        "original_data_snapshot": snapshot,
        "correction_details": correction_details,
    }


async def handle_action_reversal(
    pool: Any,
    *,
    target_session_id: uuid.UUID,
    correcting_session_id: uuid.UUID,
    description: str,
    action_description: str,
    target_butler: str | None = None,
    target_pool: Any | None = None,
    registered_butlers: list[str] | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    """Handle an action_reversal: attempt to reverse actions taken by a session.

    Best-effort reversal. Reports full/partial/failed outcomes.

    Cross-schema support: if *target_butler* is provided, reads the session from
    *target_pool* (the target butler's DB pool). The correction record is always
    written to the CURRENT butler's corrections table (*pool*).

    Args:
        pool: The correcting butler's connection pool (correction record goes here).
        target_session_id: Session whose actions are being reversed.
        correcting_session_id: Session performing the correction.
        description: What was wrong and why.
        action_description: Human description of which action(s) to reverse.
        target_butler: If provided, the name of the butler schema to target.
        target_pool: If provided, the asyncpg pool for the target butler.
        registered_butlers: List of registered butler names (for validation).
        schema: Optional schema prefix for the current butler.

    Returns:
        Dict with keys: status, correction_id, summary, original_data_snapshot,
        correction_details.
    """
    # Rate limit check
    rate_error = await _check_rate_limit(pool, correcting_session_id, schema=schema)
    if rate_error:
        cid = await create_correction(
            pool,
            correction_type=CorrectionType.ACTION_REVERSAL,
            target_session_id=target_session_id,
            correcting_session_id=correcting_session_id,
            description=description,
            status="failed",
            summary=rate_error,
            original_data_snapshot=None,
            correction_details=None,
            schema=schema,
        )
        return {
            "status": "failed",
            "correction_id": str(cid),
            "summary": rate_error,
            "original_data_snapshot": None,
            "correction_details": None,
        }

    # Cross-schema validation: if target_butler is given, validate it exists.
    if target_butler is not None:
        _validate_identifier(target_butler)
        effective_pool = target_pool if target_pool is not None else pool
        if registered_butlers is not None and target_butler not in registered_butlers:
            err = FAILURE_MESSAGES["butler_not_registered"].format(
                name=target_butler,
                comma_separated_list=", ".join(sorted(registered_butlers)),
            )
            cid = await create_correction(
                pool,
                correction_type=CorrectionType.ACTION_REVERSAL,
                target_session_id=target_session_id,
                correcting_session_id=correcting_session_id,
                description=description,
                status="failed",
                summary=err,
                original_data_snapshot=None,
                correction_details={"target_butler": target_butler},
                schema=schema,
            )
            return {
                "status": "failed",
                "correction_id": str(cid),
                "summary": err,
                "original_data_snapshot": None,
                "correction_details": {"target_butler": target_butler},
            }
    else:
        effective_pool = pool

    # Precondition checks
    target_schema = target_butler if target_butler else schema
    precond_error = await check_action_reversal_preconditions(
        effective_pool,
        target_session_id=target_session_id,
        action_description=action_description,
        schema=target_schema,
    )
    if precond_error:
        if not _is_session_not_found_error(precond_error):
            cid = await create_correction(
                pool,
                correction_type=CorrectionType.ACTION_REVERSAL,
                target_session_id=target_session_id,
                correcting_session_id=correcting_session_id,
                description=description,
                status="failed",
                summary=precond_error,
                original_data_snapshot=None,
                correction_details=None,
                schema=schema,
            )
            correction_id_str = str(cid)
        else:
            correction_id_str = ""
        return {
            "status": "failed",
            "correction_id": correction_id_str,
            "summary": precond_error,
            "original_data_snapshot": None,
            "correction_details": None,
        }

    # Fetch session tool calls
    sessions_table = f"{target_schema}.sessions" if target_schema else "sessions"
    session_row = await effective_pool.fetchrow(
        f"SELECT id, tool_calls FROM {sessions_table} WHERE id = $1",
        target_session_id,
    )
    raw_tool_calls = session_row.get("tool_calls") if session_row else []
    if isinstance(raw_tool_calls, str):
        import json as _json

        try:
            tool_calls = _json.loads(raw_tool_calls)
        except Exception:
            tool_calls = []
    else:
        tool_calls = raw_tool_calls or []

    # Attempt reversal
    reversal_result = await _attempt_action_reversal(tool_calls, action_description)
    reversed_tools = reversal_result.get("reversed", [])
    irreversible_tools = reversal_result.get("irreversible", [])

    # Determine status
    if reversed_tools and not irreversible_tools:
        status = "applied"
        summary = f"All actions reversed: {', '.join(reversed_tools)}."
    elif reversed_tools and irreversible_tools:
        status = "partially_applied"
        summary = (
            f"Partially reversed. Reversed: {', '.join(reversed_tools)}."
            f" Could not reverse: {', '.join(irreversible_tools)}."
        )
    else:
        # No reversible tools found
        status = "partially_applied" if not tool_calls else "partially_applied"
        irreversible_list = ", ".join(irreversible_tools) if irreversible_tools else "none"
        summary = f"No actions could be reversed. Irreversible: {irreversible_list}."

    correction_details: dict[str, Any] = {
        "action_description": action_description,
        "reversed": reversed_tools,
        "irreversible": irreversible_tools,
    }
    if target_butler:
        correction_details["target_butler"] = target_butler
    snapshot = {"tool_calls": tool_calls}

    cid = await create_correction(
        pool,
        correction_type=CorrectionType.ACTION_REVERSAL,
        target_session_id=target_session_id,
        correcting_session_id=correcting_session_id,
        description=description,
        status=status,
        summary=summary,
        original_data_snapshot=snapshot,
        correction_details=correction_details,
        schema=schema,
    )
    return {
        "status": status,
        "correction_id": str(cid),
        "summary": summary,
        "original_data_snapshot": snapshot,
        "correction_details": correction_details,
    }


# ---------------------------------------------------------------------------
# memory_forget import (imported here to allow patching in tests)
# ---------------------------------------------------------------------------

from butlers.core.memory_hooks import memory_forget  # noqa: E402
