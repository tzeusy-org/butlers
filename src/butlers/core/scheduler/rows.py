"""Row-field validation, normalization and JSONB coercion helpers for the scheduler."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

from butlers.core.model_routing import Complexity, coerce_complexity_tier

logger = logging.getLogger("butlers.core.scheduler")


_DISPATCH_MODE_PROMPT = "prompt"


_DISPATCH_MODE_JOB = "job"


_ALLOWED_DISPATCH_MODES = {_DISPATCH_MODE_PROMPT, _DISPATCH_MODE_JOB}


_ALLOWED_COMPLEXITY_VALUES = {c.value for c in Complexity}


_DEFAULT_COMPLEXITY = Complexity.WORKHORSE.value


# Pattern to find candidate skill names in prompt text (kebab-case words).
_SKILL_NAME_PATTERN = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b")


def _parse_uuid_reference(value: Any) -> uuid.UUID | None:
    """Parse a user-authored UUID reference, returning None for invalid text."""
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _parse_deadline_threshold_reference(value: Any) -> tuple[uuid.UUID, str] | None:
    """Parse '<deadline-uuid>:<severity>' trigger references."""
    if not isinstance(value, str):
        return None
    deadline_ref, separator, severity = value.partition(":")
    if not separator or not severity.strip():
        return None
    deadline_id = _parse_uuid_reference(deadline_ref)
    if deadline_id is None:
        return None
    return deadline_id, severity.strip()


def _check_notify_reference(
    *,
    task_name: str,
    prompt: str,
    skills_dir: Path | None,
) -> None:
    """Warn if a prompt-mode scheduled task does not reference notify().

    Checks the prompt text directly (case-insensitive).  If *skills_dir* is
    provided, also checks the SKILL.md of any skill whose kebab-case name
    appears as a word in the prompt.

    Emits a WARNING when neither the prompt nor any discovered skill SKILL.md
    contains the string ``notify`` (case-insensitive).  This is a soft check
    only — some tasks legitimately skip notify (e.g., cleanup jobs).
    """
    if _references_notify(prompt, skills_dir):
        return

    logger.warning(
        "Scheduled task %r has dispatch_mode=prompt but prompt/skill does not reference"
        " notify() — task results may not reach the user",
        task_name,
    )


def _references_notify(prompt: str, skills_dir: Path | None) -> bool:
    """True when the prompt, or a skill SKILL.md it names, mentions ``notify``."""
    if "notify" in prompt.lower():
        return True

    if skills_dir is not None and skills_dir.is_dir():
        for match in _SKILL_NAME_PATTERN.finditer(prompt):
            skill_name = match.group(1)
            skill_md = skills_dir / skill_name / "SKILL.md"
            if skill_md.is_file():
                try:
                    if "notify" in skill_md.read_text(encoding="utf-8").lower():
                        return True
                except OSError as exc:
                    logger.debug(
                        "Could not read skill file %s for notify check: %s",
                        skill_md,
                        exc,
                    )
    return False


def _normalize_schedule_projection_fields(
    *,
    timezone: Any,
    start_at: Any,
    end_at: Any,
    until_at: Any,
    display_title: Any,
    calendar_event_id: Any,
    context: str,
) -> tuple[str | None, datetime | None, datetime | None, datetime | None, str | None, str | None]:
    """Validate optional calendar-projection fields used by scheduler rows."""
    normalized_timezone: str | None = None
    if timezone is not None:
        if not isinstance(timezone, str):
            raise ValueError(f"{context}.timezone must be a string when set")
        stripped = timezone.strip()
        if not stripped:
            raise ValueError(f"{context}.timezone must be non-empty when set")
        normalized_timezone = stripped

    normalized_start_at: datetime | None = None
    if start_at is not None:
        if not isinstance(start_at, datetime):
            raise ValueError(f"{context}.start_at must be a datetime when set")
        if start_at.tzinfo is None:
            raise ValueError(f"{context}.start_at must be timezone-aware")
        normalized_start_at = start_at

    normalized_end_at: datetime | None = None
    if end_at is not None:
        if not isinstance(end_at, datetime):
            raise ValueError(f"{context}.end_at must be a datetime when set")
        if end_at.tzinfo is None:
            raise ValueError(f"{context}.end_at must be timezone-aware")
        normalized_end_at = end_at

    normalized_until_at: datetime | None = None
    if until_at is not None:
        if not isinstance(until_at, datetime):
            raise ValueError(f"{context}.until_at must be a datetime when set")
        if until_at.tzinfo is None:
            raise ValueError(f"{context}.until_at must be timezone-aware")
        normalized_until_at = until_at

    if (
        normalized_start_at is not None
        and normalized_end_at is not None
        and normalized_end_at <= normalized_start_at
    ):
        raise ValueError(f"{context}.end_at must be after start_at")
    if (
        normalized_start_at is not None
        and normalized_until_at is not None
        and normalized_until_at < normalized_start_at
    ):
        raise ValueError(f"{context}.until_at must be on/after start_at")

    normalized_display_title: str | None = None
    if display_title is not None:
        if not isinstance(display_title, str):
            raise ValueError(f"{context}.display_title must be a string when set")
        stripped = display_title.strip()
        if not stripped:
            raise ValueError(f"{context}.display_title must be non-empty when set")
        normalized_display_title = stripped

    normalized_calendar_event_id: str | None = None
    if calendar_event_id is not None:
        if isinstance(calendar_event_id, uuid.UUID):
            normalized_calendar_event_id = str(calendar_event_id)
        elif isinstance(calendar_event_id, str):
            stripped = calendar_event_id.strip()
            if not stripped:
                raise ValueError(f"{context}.calendar_event_id must be non-empty when set")
            normalized_calendar_event_id = stripped
        else:
            raise ValueError(f"{context}.calendar_event_id must be a string when set")

    return (
        normalized_timezone,
        normalized_start_at,
        normalized_end_at,
        normalized_until_at,
        normalized_display_title,
        normalized_calendar_event_id,
    )


def _normalize_dispatch_mode(value: Any, *, context: str) -> str:
    """Normalize and validate a schedule dispatch mode value."""
    if not isinstance(value, str):
        raise ValueError(f"{context}.dispatch_mode must be a string")
    normalized = value.strip().lower()
    if normalized not in _ALLOWED_DISPATCH_MODES:
        raise ValueError(
            f"Invalid {context}.dispatch_mode: {value!r}. "
            f"Expected one of {sorted(_ALLOWED_DISPATCH_MODES)!r}."
        )
    return normalized


def _normalize_complexity(value: Any, *, context: str) -> str:
    """Normalize and validate a complexity value; default to workhorse when None."""
    if value is None:
        return _DEFAULT_COMPLEXITY
    if not isinstance(value, str):
        raise ValueError(
            f"{context}.complexity must be a string when set; "
            f"expected one of {sorted(_ALLOWED_COMPLEXITY_VALUES)!r}"
        )
    normalized = value.strip().lower()
    if normalized not in _ALLOWED_COMPLEXITY_VALUES:
        raise ValueError(
            f"Invalid {context}.complexity: {value!r}. "
            f"Expected one of {sorted(_ALLOWED_COMPLEXITY_VALUES)!r}."
        )
    return normalized


def _parse_complexity_from_db_row(row: asyncpg.Record) -> Complexity:
    """Parse complexity from a DB row into a canonical ``Complexity``.

    Delegates to the shared ``coerce_complexity_tier`` (the single source of
    truth for tier normalization) with ``strict=False`` so that a stored value
    is normalized rather than collapsed:

    - Missing/null values default to ``Complexity.WORKHORSE``.
    - Every retired tier — trivial/medium/high/extra_high/discretion/
      self_healing — is remapped to its canonical successor via
      ``_DEPRECATED_TIER_MAP`` (e.g. ``high``/``extra_high`` -> ``reasoning``),
      not silently collapsed to ``workhorse``. This matters on the real
      dispatch paths (cron + deadline loops): the returned tier is handed to
      ``dispatch_fn``/``resolve_model``, so a schedule row persisted with a
      legacy ``high`` tier now correctly dispatches at ``reasoning``.
    - Any other unrecognized junk degrades to ``Complexity.WORKHORSE`` with a
      logged warning rather than crashing — complexity is DB-serialized by this
      codebase but could be stale (old migration, manual edit), so a read path
      must fail open.

    Returns the ``Complexity`` enum (its consumers want the enum for dispatch;
    ``schedule_list`` calls ``.value`` on the result for its LLM-facing dict).
    """
    return coerce_complexity_tier(row.get("complexity"), strict=False)


def _normalize_schedule_dispatch(
    *,
    dispatch_mode: Any,
    prompt: Any,
    job_name: Any,
    job_args: Any,
    complexity: Any = None,
    context: str,
) -> tuple[str, str | None, str | None, dict[str, Any] | None, str]:
    """Validate mode-specific dispatch fields and return normalized values.

    Returns
    -------
    tuple
        ``(mode, prompt, job_name, job_args, complexity)``
    """
    mode = _normalize_dispatch_mode(dispatch_mode, context=context)
    normalized_complexity = _normalize_complexity(complexity, context=context)

    if prompt is not None and not isinstance(prompt, str):
        raise ValueError(f"{context}.prompt must be a string when set")
    if job_name is not None and not isinstance(job_name, str):
        raise ValueError(f"{context}.job_name must be a string when set")
    if job_args is not None and not isinstance(job_args, dict):
        raise ValueError(f"{context}.job_args must be a dict/object when set")

    if mode == _DISPATCH_MODE_PROMPT:
        if prompt is None or not prompt.strip():
            raise ValueError(
                f"{context} with dispatch_mode={_DISPATCH_MODE_PROMPT!r} requires non-empty prompt"
            )
        if job_name is not None:
            raise ValueError(
                f"{context}.job_name is only valid when dispatch_mode={_DISPATCH_MODE_JOB!r}"
            )
        if job_args is not None:
            raise ValueError(
                f"{context}.job_args is only valid when dispatch_mode={_DISPATCH_MODE_JOB!r}"
            )
        return mode, prompt, None, None, normalized_complexity

    if prompt is not None:
        raise ValueError(
            f"{context}.prompt is not allowed when dispatch_mode={_DISPATCH_MODE_JOB!r}"
        )
    if job_name is None or not job_name.strip():
        raise ValueError(
            f"{context} with dispatch_mode={_DISPATCH_MODE_JOB!r} requires non-empty job_name"
        )

    coerced_job_args = dict(job_args) if job_args is not None else None
    return mode, None, job_name.strip(), coerced_job_args, normalized_complexity


def _result_to_jsonb(result: Any) -> Any:
    """Convert a dispatch result to a JSON-safe Python value for JSONB storage.

    Handles dataclass-like objects (SpawnerResult) by extracting their __dict__,
    plain dicts, and falls back to string representation.  Returns a JSON-safe
    Python object (dict/list/scalar) so the asyncpg JSONB codec can encode it
    directly without a ``::jsonb`` cast.
    """
    if result is None:
        return None
    if hasattr(result, "__dict__") and not isinstance(result, type):
        return json.loads(json.dumps(result.__dict__, default=str))
    if isinstance(result, dict):
        return json.loads(json.dumps(result, default=str))
    return {"result": str(result)}


def _dict_to_jsonb(value: dict[str, Any] | None) -> Any:
    """Convert a dict payload to a JSON-safe Python value for JSONB binding.

    Returns the value as-is when it contains only JSON-safe types, or round-trips
    through json.dumps/loads to coerce non-serializable types (e.g. datetimes,
    UUIDs) to strings.
    """
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def _list_to_jsonb(value: list[Any] | None) -> Any:
    """Convert a list payload to a JSON-safe Python value for JSONB binding.

    Mirrors :func:`_dict_to_jsonb` but for list-shaped JSONB columns (e.g.
    ``scheduled_tasks.fired_thresholds``). Bind the returned value directly
    into the query with no ``json.dumps()`` at the call site and no
    ``::jsonb`` cast in the SQL: every asyncpg pool in this codebase
    registers ``register_jsonb_codec()`` (src/butlers/db.py), whose encoder
    expects a Python object and calls ``json.dumps()`` on it itself. Passing
    an already-serialized JSON string double-encodes the value into a
    jsonb-typed STRING instead of an ARRAY (bu-xfcpf).
    """
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def _jsonb_to_dict(value: Any, *, context: str) -> dict[str, Any] | None:
    """Normalize JSONB payloads that may come back as dicts or JSON strings."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{context}.job_args contains invalid JSON") from exc
        if isinstance(decoded, dict):
            return decoded
    raise ValueError(f"{context}.job_args must decode to an object")
