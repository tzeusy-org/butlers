"""Task scheduler — cron-driven task dispatch with TOML sync.

On startup, syncs [[butler.schedule]] entries from TOML config to the
scheduled_tasks table. At each tick(), evaluates cron expressions via
croniter and dispatches due task prompts to the LLM CLI spawner serially.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg
from croniter import croniter
from opentelemetry import trace

from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import Complexity

logger = logging.getLogger(__name__)

_DEFAULT_MAX_STAGGER_SECONDS = 15 * 60
# Fallback timezone for cron evaluation when no per-schedule or owner timezone
# resolves.  Hour-pinned crons are interpreted in this zone.
_DEFAULT_SCHEDULE_TIMEZONE = "UTC"
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
    if "notify" in prompt.lower():
        return

    if skills_dir is not None and skills_dir.is_dir():
        for match in _SKILL_NAME_PATTERN.finditer(prompt):
            skill_name = match.group(1)
            skill_md = skills_dir / skill_name / "SKILL.md"
            if skill_md.is_file():
                try:
                    if "notify" in skill_md.read_text(encoding="utf-8").lower():
                        return
                except OSError as exc:
                    logger.debug(
                        "Could not read skill file %s for notify check: %s",
                        skill_md,
                        exc,
                    )

    logger.warning(
        "Scheduled task %r has dispatch_mode=prompt but prompt/skill does not reference"
        " notify() — task results may not reach the user",
        task_name,
    )


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
    """Normalize and validate a complexity value; default to medium when None."""
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


def _parse_complexity_from_db_row(row: asyncpg.Record, task_name: str) -> Complexity:
    """Parse complexity from a DB row, falling back to MEDIUM on missing or invalid values.

    Logs a warning on invalid values — complexity is DB-serialized by this codebase but
    could be stale (e.g. old migration, manual edit), so a visible warning is appropriate.
    """
    raw_complexity = row.get("complexity") or _DEFAULT_COMPLEXITY
    try:
        return Complexity(raw_complexity)
    except ValueError:
        logger.warning(
            "Unknown complexity value %r for task %s; defaulting to workhorse",
            raw_complexity,
            task_name,
        )
        return Complexity.WORKHORSE


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


def _cron_interval_seconds(cron: str, *, now: datetime | None = None) -> int:
    """Return the interval between the next two occurrences for ``cron``."""
    anchor = now or datetime.now(UTC)
    it = croniter(cron, anchor)
    first = it.get_next(datetime).replace(tzinfo=UTC)
    second = it.get_next(datetime).replace(tzinfo=UTC)
    return max(1, int((second - first).total_seconds()))


def _stagger_offset_seconds(
    cron: str,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    now: datetime | None = None,
) -> int:
    """Compute a deterministic offset that never exceeds the cron cadence."""
    if not stagger_key or max_stagger_seconds <= 0:
        return 0

    cadence_seconds = _cron_interval_seconds(cron, now=now)
    max_safe_offset = min(max_stagger_seconds, cadence_seconds - 1)
    if max_safe_offset <= 0:
        return 0

    digest = hashlib.sha256(stagger_key.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return bucket % (max_safe_offset + 1)


def _effective_schedule_timezone(stored: str | None, default_timezone: str) -> str:
    """Resolve the timezone a schedule's cron should be evaluated in.

    The ``scheduled_tasks.timezone`` column is ``NOT NULL DEFAULT 'UTC'`` (added
    in core_112), so TOML/unset schedules carry the literal ``'UTC'``.  That
    sentinel — like ``None``/empty — means "follow the owner's configured
    general timezone" (*default_timezone*).  Any other stored value (e.g.
    ``'America/New_York'``) is an explicit per-schedule override and wins.

    Consequence: a schedule cannot be pinned to literal UTC while the owner's
    general timezone is non-UTC.  This is acceptable for a single-owner system
    where the owner's timezone is the universal default; pin a non-UTC zone
    explicitly when a different local time is required.
    """
    candidate = (stored or "").strip()
    if not candidate or candidate.upper() == "UTC":
        return default_timezone
    return candidate


def _coerce_schedule_zone(timezone: str | None) -> ZoneInfo:
    """Resolve a schedule timezone string to a ``ZoneInfo``, failing open to UTC.

    ``None``/empty resolves to UTC.  An unknown IANA name is logged and treated
    as UTC so a typo'd timezone never wedges dispatch.
    """
    candidate = (timezone or "").strip()
    if not candidate or candidate.upper() == "UTC":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("Unknown schedule timezone %r; interpreting cron in UTC", timezone)
        return ZoneInfo("UTC")


def _next_run(
    cron: str,
    *,
    timezone: str | None = None,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    now: datetime | None = None,
) -> datetime:
    """Compute the next run time for a cron expression, returned in UTC.

    The cron fields are interpreted in *timezone* (an IANA name).  When
    *timezone* is ``None``/empty/``"UTC"`` the cron is evaluated in UTC, which
    preserves historical behaviour.  Anchoring croniter to a timezone-aware
    datetime makes croniter return occurrences in that zone (DST-correct); the
    result is converted to UTC for storage in ``scheduled_tasks.next_run_at``.
    """
    zone = _coerce_schedule_zone(timezone)
    anchor = (now or datetime.now(UTC)).astimezone(zone)
    next_run = croniter(cron, anchor).get_next(datetime).astimezone(UTC)
    offset_seconds = _stagger_offset_seconds(
        cron,
        stagger_key=stagger_key,
        max_stagger_seconds=max_stagger_seconds,
        now=anchor,
    )
    if offset_seconds:
        return next_run + timedelta(seconds=offset_seconds)
    return next_run


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


def _prepend_seasonal_context(prompt: str, active_seasons: list[dict[str, Any]] | None) -> str:
    """Prepend a seasonal context prefix to *prompt* when active seasons exist.

    Returns the original prompt unchanged when *active_seasons* is empty/None.
    Used by both the deadline dispatch pass and the cron dispatch pass so that
    the prefix format stays consistent across all prompt-mode dispatches.
    """
    if not active_seasons:
        return prompt
    season_names = ", ".join(s["name"] for s in active_seasons)
    seasonal_prefix = f"[Seasonal context: active periods: {season_names}]"
    return f"{seasonal_prefix}\n\n{prompt}"


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


async def sync_schedules(
    pool: asyncpg.Pool,
    schedules: list[dict[str, Any]],
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    skills_dir: Path | None = None,
    default_timezone: str = _DEFAULT_SCHEDULE_TIMEZONE,
) -> None:
    """Sync TOML ``[[butler.schedule]]`` entries to the ``scheduled_tasks`` DB table.

    - Insert new tasks with ``source='toml'``
    - Update changed tasks (cron or prompt changed)
    - Mark removed tasks (present in DB but not in TOML) by setting ``enabled=false``
    - Match by ``name`` field
    - Compute ``next_run_at`` via croniter for each synced task

    For ``dispatch_mode=prompt`` tasks, emits a WARNING if neither the prompt
    text nor any skill SKILL.md referenced by the prompt contains ``notify``
    (case-insensitive).  Pass *skills_dir* (``roster/{butler}/.agents/skills/``)
    to enable skill-content scanning.

    Args:
        pool: asyncpg connection pool.
        schedules: List of dicts with schedule fields.
        skills_dir: Optional path to the butler's skills directory for notify
            reference checking.  When provided, SKILL.md files for any
            kebab-case skill names found in the prompt are also inspected.
        default_timezone: IANA timezone used to interpret cron fields for TOML
            schedules (which have no per-row timezone).  Defaults to UTC; the
            daemon passes the owner's configured general timezone so hour-pinned
            crons fire at the intended local time.
    """
    # Determine whether the DB schema includes temporal intelligence columns.
    _has_task_type = await _has_column(pool, "scheduled_tasks", "task_type")

    normalized_schedules: list[dict[str, Any]] = []
    for i, schedule in enumerate(schedules):
        schedule_path = f"schedules[{i}]"
        if not isinstance(schedule, dict):
            raise ValueError(f"{schedule_path} must be a dict/object")

        name = schedule.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{schedule_path}.name must be a non-empty string")

        cron = schedule.get("cron")
        if not isinstance(cron, str) or not cron.strip():
            raise ValueError(f"{schedule_path}.cron must be a non-empty string")
        if not croniter.is_valid(cron):
            raise ValueError(f"Invalid {schedule_path}.cron: {cron!r}")

        task_type = schedule.get("task_type", "cron")
        if task_type not in ("cron", "deadline"):
            raise ValueError(
                f"{schedule_path}.task_type must be 'cron' or 'deadline' (got {task_type!r})"
            )

        dispatch_mode, prompt, job_name, job_args, complexity = _normalize_schedule_dispatch(
            dispatch_mode=schedule.get("dispatch_mode", _DISPATCH_MODE_PROMPT),
            prompt=schedule.get("prompt"),
            job_name=schedule.get("job_name"),
            job_args=schedule.get("job_args"),
            complexity=schedule.get("complexity"),
            context=schedule_path,
        )

        # Warn if a prompt-mode task omits notify() — task results will be silent otherwise.
        if dispatch_mode == _DISPATCH_MODE_PROMPT and prompt is not None:
            _check_notify_reference(
                task_name=name,
                prompt=prompt,
                skills_dir=skills_dir,
            )

        # Deadline-specific fields
        deadline_fields: dict[str, Any] = {}
        if task_type == "deadline":
            from datetime import date as _date

            raw_target = schedule.get("target_date")
            if raw_target is None:
                raise ValueError(
                    f"{schedule_path}.target_date is required when task_type='deadline'"
                )
            if isinstance(raw_target, str):
                try:
                    from datetime import date as _date2

                    raw_target = _date2.fromisoformat(raw_target)
                except ValueError as exc:
                    raise ValueError(
                        f"{schedule_path}.target_date must be a YYYY-MM-DD date string"
                    ) from exc
            if not isinstance(raw_target, _date):
                raise ValueError(
                    f"{schedule_path}.target_date must be a date value (got {raw_target!r})"
                )

            lead_time_days = schedule.get("lead_time_days")
            if not isinstance(lead_time_days, int) or lead_time_days <= 0:
                raise ValueError(
                    f"{schedule_path}.lead_time_days must be a positive integer "
                    f"(got {lead_time_days!r})"
                )

            alert_thresholds = schedule.get("alert_thresholds")
            if not isinstance(alert_thresholds, list) or not alert_thresholds:
                raise ValueError(
                    f"{schedule_path}.alert_thresholds must be a non-empty list of threshold dicts"
                )

            deadline_fields = {
                "target_date": raw_target,
                "lead_time_days": lead_time_days,
                "alert_thresholds": alert_thresholds,
            }

        raw_budget = schedule.get("max_token_budget")
        max_token_budget: int | None = None
        if raw_budget is not None:
            max_token_budget = int(raw_budget) if isinstance(raw_budget, (int, float)) else None

        normalized_schedules.append(
            {
                "name": name,
                "cron": cron,
                "task_type": task_type,
                "dispatch_mode": dispatch_mode,
                "prompt": prompt,
                "job_name": job_name,
                "job_args": job_args,
                "complexity": complexity,
                "max_token_budget": max_token_budget,
                # Deadline-specific fields — validated above for deadline tasks,
                # None for cron tasks; always present so the needs_update check
                # can compare them without KeyError.
                "target_date": deadline_fields.get("target_date"),
                "lead_time_days": deadline_fields.get("lead_time_days"),
                "alert_thresholds": deadline_fields.get("alert_thresholds"),
            }
        )

    toml_names = {s["name"] for s in normalized_schedules}

    # When the schema includes temporal intelligence columns, fetch them so the
    # needs_update check can detect changes to deadline-specific fields.
    _temporal_select = (
        ", task_type, target_date, lead_time_days, alert_thresholds" if _has_task_type else ""
    )

    # Detect whether the max_token_budget column exists (added in core_050).
    _has_budget = await _has_column(pool, "scheduled_tasks", "max_token_budget")
    _budget_select = ", max_token_budget" if _has_budget else ""

    # Fetch existing tasks whose names match any TOML schedule (regardless of source).
    # A runtime-created task (source='db') may share a name with a TOML schedule;
    # TOML takes ownership on next startup to avoid unique-constraint violations.
    rows = await pool.fetch(
        f"""
        SELECT id, name, source, cron, prompt, dispatch_mode, job_name, job_args,
               complexity, enabled{_temporal_select}{_budget_select}
        FROM scheduled_tasks
        WHERE name = ANY($1::text[])
        """,
        list(toml_names),
    )
    # Also fetch all remaining toml-sourced tasks (for the disable-removed-tasks pass below).
    toml_only_rows = await pool.fetch(
        f"""
        SELECT id, name, cron, prompt, dispatch_mode, job_name, job_args,
               complexity, enabled{_temporal_select}{_budget_select}
        FROM scheduled_tasks
        WHERE source = 'toml' AND name != ALL($1::text[])
        """,
        list(toml_names),
    )
    db_by_name: dict[str, asyncpg.Record] = {row["name"]: row for row in rows}
    # Include leftover toml-sourced tasks so the disable pass can find them.
    for row in toml_only_rows:
        db_by_name.setdefault(row["name"], row)

    for entry in normalized_schedules:
        name = entry["name"]
        cron = entry["cron"]
        task_type = entry["task_type"]
        prompt = entry["prompt"]
        dispatch_mode = entry["dispatch_mode"]
        job_name = entry["job_name"]
        job_args = entry["job_args"]
        complexity = entry["complexity"]
        max_token_budget = entry["max_token_budget"]
        target_date = entry["target_date"]
        lead_time_days = entry["lead_time_days"]
        alert_thresholds = entry["alert_thresholds"]
        next_run_at = _next_run(
            cron,
            timezone=default_timezone,
            stagger_key=stagger_key,
            max_stagger_seconds=max_stagger_seconds,
        )

        if name in db_by_name:
            existing = db_by_name[name]
            existing_job_args = _jsonb_to_dict(
                existing["job_args"],
                context=f"scheduled_tasks[{name}]",
            )
            existing_complexity = existing.get("complexity") or _DEFAULT_COMPLEXITY
            # Reclaim from 'db' source, or update if schedule payload changed / disabled.
            needs_update = (
                existing.get("source", "toml") != "toml"
                or existing["cron"] != cron
                or existing["dispatch_mode"] != dispatch_mode
                or existing["prompt"] != prompt
                or existing["job_name"] != job_name
                or existing_job_args != job_args
                or existing_complexity != complexity
                or not existing["enabled"]
            )
            # Also detect changes to max_token_budget when schema supports it.
            if not needs_update and _has_budget:
                needs_update = existing.get("max_token_budget") != max_token_budget
            # Also detect changes to deadline-specific fields when schema supports them.
            # Restrict to deadline tasks to avoid spurious updates on cron tasks that
            # may carry stale deadline-column values from a prior task_type migration.
            if not needs_update and _has_task_type and task_type == "deadline":
                existing_alert_thresholds = existing.get("alert_thresholds")
                if isinstance(existing_alert_thresholds, str):
                    existing_alert_thresholds = json.loads(existing_alert_thresholds)
                needs_update = (
                    existing.get("target_date") != target_date
                    or existing.get("lead_time_days") != lead_time_days
                    or existing_alert_thresholds != alert_thresholds
                )
            if needs_update:
                _budget_set = ", max_token_budget = $13" if _has_budget else ""
                if _has_task_type and task_type == "deadline":
                    base_args = [
                        existing["id"],
                        cron,
                        dispatch_mode,
                        prompt,
                        job_name,
                        _dict_to_jsonb(job_args),
                        complexity,
                        next_run_at,
                        task_type,
                        target_date,
                        lead_time_days,
                        alert_thresholds,
                    ]
                    if _has_budget:
                        base_args.append(max_token_budget)
                    await pool.execute(
                        f"""
                        UPDATE scheduled_tasks
                        SET cron = $2,
                            dispatch_mode = $3,
                            prompt = $4,
                            job_name = $5,
                            job_args = $6,
                            complexity = $7,
                            next_run_at = $8,
                            source = 'toml',
                            enabled = true,
                            task_type = $9,
                            target_date = $10,
                            lead_time_days = $11,
                            alert_thresholds = $12{_budget_set},
                            updated_at = now()
                        WHERE id = $1
                        """,
                        *base_args,
                    )
                else:
                    _budget_set_cron = ", max_token_budget = $9" if _has_budget else ""
                    base_args = [
                        existing["id"],
                        cron,
                        dispatch_mode,
                        prompt,
                        job_name,
                        _dict_to_jsonb(job_args),
                        complexity,
                        next_run_at,
                    ]
                    if _has_budget:
                        base_args.append(max_token_budget)
                    await pool.execute(
                        f"""
                        UPDATE scheduled_tasks
                        SET cron = $2,
                            dispatch_mode = $3,
                            prompt = $4,
                            job_name = $5,
                            job_args = $6,
                            complexity = $7,
                            next_run_at = $8,
                            source = 'toml',
                            enabled = true{_budget_set_cron},
                            updated_at = now()
                        WHERE id = $1
                        """,
                        *base_args,
                    )
                logger.info("Updated TOML schedule: %s", name)
        else:
            # Insert new TOML task
            _budget_col = ", max_token_budget" if _has_budget else ""
            if _has_task_type and task_type == "deadline":
                _budget_val = ", $13" if _has_budget else ""
                base_args = [
                    name,
                    cron,
                    dispatch_mode,
                    prompt,
                    job_name,
                    _dict_to_jsonb(job_args),
                    complexity,
                    next_run_at,
                    task_type,
                    target_date,
                    lead_time_days,
                    alert_thresholds,
                ]
                if _has_budget:
                    base_args.append(max_token_budget)
                await pool.execute(
                    f"""
                    INSERT INTO scheduled_tasks (
                        name,
                        cron,
                        dispatch_mode,
                        prompt,
                        job_name,
                        job_args,
                        complexity,
                        source,
                        enabled,
                        next_run_at,
                        task_type,
                        target_date,
                        lead_time_days,
                        alert_thresholds{_budget_col}
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'toml', true, $8,
                            $9, $10, $11, $12{_budget_val})
                    """,
                    *base_args,
                )
            else:
                _budget_val_cron = ", $9" if _has_budget else ""
                base_args = [
                    name,
                    cron,
                    dispatch_mode,
                    prompt,
                    job_name,
                    _dict_to_jsonb(job_args),
                    complexity,
                    next_run_at,
                ]
                if _has_budget:
                    base_args.append(max_token_budget)
                await pool.execute(
                    f"""
                    INSERT INTO scheduled_tasks (
                        name,
                        cron,
                        dispatch_mode,
                        prompt,
                        job_name,
                        job_args,
                        complexity,
                        source,
                        enabled,
                        next_run_at{_budget_col}
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'toml', true, $8{_budget_val_cron})
                    """,
                    *base_args,
                )
            logger.info("Inserted TOML schedule: %s", name)

    # Disable TOML tasks no longer present in config
    for name, row in db_by_name.items():
        if name not in toml_names and row["enabled"]:
            await pool.execute(
                """
                UPDATE scheduled_tasks SET enabled = false, updated_at = now()
                WHERE id = $1
                """,
                row["id"],
            )
            logger.info("Disabled removed TOML schedule: %s", name)


async def _has_column(pool: asyncpg.Pool, table: str, column: str) -> bool:
    """Return True if *column* exists in *table* within the pool's current schema.

    Filtering by ``current_schema()`` is critical: butler pools share the
    Postgres database and only differ by their ``search_path``.  Without the
    schema filter, a check against (e.g.) ``chronicler.scheduled_tasks``
    spuriously returns True because another butler's schema has the column,
    and the subsequent query then fails with "column does not exist".
    """
    result = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = $1
              AND column_name = $2
        )
        """,
        table,
        column,
    )
    return bool(result)


async def _has_table(pool: asyncpg.Pool, table: str) -> bool:
    """Return True if *table* exists in the pool's current schema."""
    result = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = $1
        )
        """,
        table,
    )
    return bool(result)


async def _has_columns(pool: asyncpg.Pool, table: str, columns: set[str]) -> bool:
    """Return True if *table* has every named column in the pool's current schema."""
    found = await _existing_columns(pool, table, columns)
    return columns.issubset(found)


async def _existing_columns(pool: asyncpg.Pool, table: str, columns: set[str]) -> set[str]:
    """Return the requested columns that exist in *table* within the current schema."""
    if not columns:
        return set()
    rows = await pool.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = $1
          AND column_name = ANY($2::text[])
        """,
        table,
        sorted(columns),
    )
    return {row["column_name"] for row in rows}


async def _tick_deadline_pass(
    pool: asyncpg.Pool,
    dispatch_fn,
    *,
    now: datetime,
    has_task_type_col: bool | None = None,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    metrics: ButlerMetrics | None = None,
    active_seasons: list[dict[str, Any]] | None = None,
) -> tuple[int, int]:
    """Evaluate deadline tasks: fire due thresholds and handle expiry.

    Runs before the cron dispatch pass.  For each enabled deadline task:
      1. If target_date < today → transition to 'expired', disable the task.
      2. Otherwise → compute days_remaining, check for unfired thresholds,
         dispatch a prompt if a threshold fires, record it.

    Args:
        has_task_type_col: Optional pre-computed result from _has_column() check.
            Pass this from tick() to avoid redundant schema round-trips.

    Returns:
        (deadlines_evaluated, deadlines_dispatched) counts.
    """
    from butlers.core.temporal.deadlines import (
        build_deadline_prompt_context,
        compute_days_remaining,
        compute_expiry_transition,
        compute_next_deadline_status,
        find_unfired_threshold,
        is_deadline_blocked,
    )

    # Skip the deadline pass if the schema hasn't been fully migrated yet.
    # Long-lived databases can briefly hold a partial temporal schema after a
    # failed or interrupted migration; tick() must keep cron dispatch alive.
    required_deadline_columns = {
        "task_type",
        "target_date",
        "lead_time_days",
        "alert_thresholds",
        "deadline_status",
        "fired_thresholds",
        "depends_on",
    }
    if has_task_type_col is None:
        has_task_type_col = await _has_column(pool, "scheduled_tasks", "task_type")
    if not has_task_type_col or not await _has_columns(
        pool, "scheduled_tasks", required_deadline_columns
    ):
        return 0, 0

    deadline_rows = await pool.fetch(
        """
        SELECT id, name, prompt, dispatch_mode, task_type,
               target_date, lead_time_days, alert_thresholds,
               deadline_status, fired_thresholds, depends_on, complexity
        FROM scheduled_tasks
        WHERE enabled = true AND task_type = 'deadline'
        ORDER BY target_date
        """,
    )

    evaluated = 0
    dispatched = 0

    for row in deadline_rows:
        evaluated += 1
        task_id = row["id"]
        name = row["name"]
        target_date = row["target_date"]
        current_status = row["deadline_status"] or "pending"

        # Parse JSONB fields (asyncpg returns them as python objects already)
        raw_alert = row["alert_thresholds"]
        alert_thresholds: list[dict[str, Any]] = (
            raw_alert if isinstance(raw_alert, list) else json.loads(raw_alert or "[]")
        )
        raw_fired = row["fired_thresholds"]
        fired_thresholds: list[dict[str, Any]] = (
            raw_fired if isinstance(raw_fired, list) else json.loads(raw_fired or "[]")
        )
        raw_depends = row["depends_on"]
        depends_on: list[str] = (
            raw_depends if isinstance(raw_depends, list) else json.loads(raw_depends or "[]")
        )

        # Step 1: check expiry
        new_status, should_disable = compute_expiry_transition(
            current_status=current_status,
            target_date=target_date,
        )
        if should_disable:
            await pool.execute(
                """
                UPDATE scheduled_tasks
                SET deadline_status = $2, enabled = false, updated_at = now()
                WHERE id = $1
                """,
                task_id,
                new_status,
            )
            logger.info("Deadline task %s expired (target_date=%s); disabled", name, target_date)
            continue

        # Step 2: skip if blocked by incomplete dependencies
        if depends_on:
            # Fetch dependency statuses
            dep_rows = await pool.fetch(
                """
                SELECT id::text, deadline_status FROM scheduled_tasks
                WHERE id::text = ANY($1::text[])
                """,
                depends_on,
            )
            dep_statuses = {r["id"]: r["deadline_status"] or "pending" for r in dep_rows}
            if is_deadline_blocked(depends_on=depends_on, dependency_statuses=dep_statuses):
                logger.debug("Deadline task %s blocked by incomplete dependencies; skipping", name)
                continue

        # Step 3: compute days remaining and look for unfired threshold
        days_remaining = compute_days_remaining(target_date=target_date)
        threshold = find_unfired_threshold(
            days_remaining=days_remaining,
            alert_thresholds=alert_thresholds,
            fired_thresholds=fired_thresholds,
        )

        if threshold is None:
            continue

        # Step 4: dispatch the deadline prompt
        prompt = row["prompt"] or f"Deadline approaching: {name}"
        task_complexity = _parse_complexity_from_db_row(row, name)
        augmented_prompt = build_deadline_prompt_context(
            original_prompt=prompt,
            target_date=target_date,
            days_remaining=days_remaining,
            fired_threshold=threshold,
            deadline_status=current_status,
            all_thresholds=alert_thresholds,
        )
        # Prepend seasonal context when active seasons exist (mirrors cron pass behaviour).
        augmented_prompt = _prepend_seasonal_context(augmented_prompt, active_seasons)
        try:
            await dispatch_fn(
                prompt=augmented_prompt,
                trigger_source=f"deadline:{name}",
                complexity=task_complexity,
            )
            dispatched += 1
            if metrics is not None:
                metrics.record_task_dispatched(
                    butler=stagger_key or "unknown",
                    task_name=name,
                    outcome="success",
                )
            logger.info("Dispatched deadline task: %s (days_remaining=%d)", name, days_remaining)
        except Exception:
            logger.exception("Failed to dispatch deadline task: %s", name)
            if metrics is not None:
                metrics.record_task_dispatched(
                    butler=stagger_key or "unknown",
                    task_name=name,
                    outcome="failure",
                )
            # Skip threshold recording on dispatch failure to preserve retry semantics
            continue

        # Step 5: record the fired threshold and update status
        new_fired = [*fired_thresholds, threshold]
        new_status = compute_next_deadline_status(
            current_status=current_status,
            fired_threshold=threshold,
        )
        await pool.execute(
            """
            UPDATE scheduled_tasks
            SET deadline_status = $2, fired_thresholds = $3,
                last_run_at = $4, updated_at = now()
            WHERE id = $1
            """,
            task_id,
            new_status,
            json.dumps(new_fired),
            now,
        )

    return evaluated, dispatched


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
    import json as _json

    from butlers.core.temporal.event_chains import materialize_chain_actions

    tasks = materialize_chain_actions(
        chain_name=chain_name,
        actions=actions,
        fired_at=now,
    )

    for task in tasks:
        try:
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
                _json.dumps(task.get("job_args")) if task.get("job_args") else None,
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
    - calendar_event_end: calendar_projection events that ended before now
    - deadline_passed: deadline tasks that transitioned to 'expired' or 'completed'
    - deadline_threshold: deadline tasks where a matching severity threshold has fired

    Returns the number of chains fired.
    """
    import json as _json

    from butlers.core.temporal.event_chains import should_fire_chain

    chains_fired = 0

    # event_chains is a prerequisite for every trigger in this pass.  Bail out
    # early so we avoid unnecessary schema probes for calendar_projection and
    # scheduled_tasks when the core table is missing.
    has_event_chains_table = await _has_table(pool, "event_chains")
    if not has_event_chains_table:
        return 0

    # --- Trigger: calendar_event_end ---
    # Only evaluated when the optional calendar_projection table exists with
    # the legacy projection columns this pass reads.
    calendar_projection_ready = await _has_columns(
        pool,
        "calendar_projection",
        {"event_id", "butler_name", "end_at", "chain_triggered"},
    )

    if calendar_projection_ready:
        # Find calendar events that ended before now and haven't triggered chains yet
        ended_events = await pool.fetch(
            """
            SELECT event_id, butler_name
            FROM calendar_projection
            WHERE end_at <= $1 AND chain_triggered = false
            """,
            now,
        )

        for event_row in ended_events:
            event_id = event_row["event_id"]
            butler_name = event_row["butler_name"]

            chains = await pool.fetch(
                """
                SELECT id, name, actions
                FROM event_chains
                WHERE trigger_type = 'calendar_event_end'
                  AND trigger_reference = $1
                  AND status = 'active'
                  AND butler_name = $2
                """,
                event_id,
                butler_name,
            )

            for chain_row in chains:
                chain_id = chain_row["id"]
                chain_name = chain_row["name"]
                actions = chain_row["actions"]
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
                    trigger_label=f"calendar event {event_id!r}",
                )
                chains_fired += 1

            # Mark calendar event as chain_triggered regardless of whether chains fired
            await pool.execute(
                """
                UPDATE calendar_projection SET chain_triggered = true
                WHERE event_id = $1 AND butler_name = $2
                """,
                event_id,
                butler_name,
            )

    # --- Trigger: deadline_passed ---
    # Detect active chains whose referenced deadline has reached a terminal status
    # (expired or completed).  The deadline_status was already updated in Pass 1
    # (_tick_deadline_pass) earlier in the same tick() call.
    #
    # Guard: skip if scheduled_tasks lacks deadline columns (pre-migration schema).
    has_deadline_status_col = await _has_column(pool, "scheduled_tasks", "deadline_status")
    if has_deadline_status_col:
        # Fetch deadline chains without casting trigger_reference in SQL.
        # trigger_reference is user-authored text, and malformed rows must not
        # abort the entire scheduler tick.
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
            deadline_fired_thresholds = {
                row["id"]: row["fired_thresholds"] for row in threshold_rows
            }

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


async def _tick_deferred_notification_pass(
    pool: asyncpg.Pool,
    now: datetime,
    *,
    notify_fn=None,
) -> int:
    """Flush deferred notifications: expire old ones and deliver due ones.

    - Marks pending notifications > 24h past deliver_at as expired.
    - Delivers pending notifications with deliver_at <= now via the standard
      notify pipeline (``notify_fn``), re-using the stored ``notify.v1``
      envelope rather than re-prompting the LLM spawner.

    ``notify_fn`` must be an async callable that accepts a single positional
    argument — the ``notify.v1`` envelope dict — and raises on failure.
    Example signature::

        async def notify_fn(envelope: dict) -> None: ...

    When ``notify_fn`` is ``None`` (e.g., unit tests that don't need real
    delivery), due notifications are counted but left ``pending`` so they are
    retried on the next tick once a real ``notify_fn`` is wired in.

    Returns the number of notifications delivered.
    """
    # Check if deferred_notifications table exists
    table_exists = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'deferred_notifications'
        )
        """
    )
    if not table_exists:
        return 0

    import json as _json

    # Expire stale pending notifications (> 24h past deliver_at)
    await pool.execute(
        """
        UPDATE deferred_notifications
        SET status = 'expired'
        WHERE status = 'pending'
          AND deliver_at < $1
        """,
        now - timedelta(hours=24),
    )

    # Fetch due notifications (pending, deliver_at <= now)
    due_rows = await pool.fetch(
        """
        SELECT id, channel, message, priority, envelope
        FROM deferred_notifications
        WHERE status = 'pending' AND deliver_at <= $1
        ORDER BY deliver_at
        """,
        now,
    )

    if not due_rows:
        return 0

    if notify_fn is None:
        # No delivery callable wired — leave rows pending for next tick.
        logger.warning(
            "_tick_deferred_notification_pass: %d due notification(s) skipped"
            " because notify_fn is not configured; will retry on next tick.",
            len(due_rows),
        )
        return 0

    delivered = 0
    for row in due_rows:
        notif_id = row["id"]
        channel = row["channel"]
        envelope = row["envelope"]
        if isinstance(envelope, str):
            envelope = _json.loads(envelope)

        try:
            # Deliver via the standard notify pipeline using the stored
            # notify.v1 envelope, NOT via the LLM spawner.
            await notify_fn(envelope)
            # Mark as delivered
            await pool.execute(
                """
                UPDATE deferred_notifications
                SET status = 'delivered', delivered_at = $2
                WHERE id = $1
                """,
                notif_id,
                now,
            )
            delivered += 1
            logger.info("Delivered deferred notification %s on channel %s", notif_id, channel)
        except Exception:
            logger.exception("Failed to deliver deferred notification %s", notif_id)
            # Keep status=pending for next-tick retry

    return delivered


async def _butler_dispatch_gated(
    eligibility_pool: asyncpg.Pool | None,
    butler_name: str | None,
) -> str | None:
    """Return a gate reason when scheduled dispatch must be suppressed.

    A butler that has been paused/quarantined (or has gone stale) in the
    Switchboard's ``butler_registry`` must NOT have its scheduled cron/deadline
    ticks fire — otherwise pausing a butler in the dashboard leaves its cron
    ticks running (the bug this guards against).

    The canonical eligibility decision lives in the Switchboard registry, so we
    reuse :func:`resolve_routing_target` (the same accessor the routing path
    uses) rather than re-deriving the state here.  A target is gated for
    scheduled dispatch under exactly the same default policy the router applies
    to inbound routing: ``quarantined`` and ``stale`` are both gated, ``active``
    is allowed.

    Returns ``None`` when dispatch should proceed (eligible, or no gating
    context available), or a human-readable reason string when dispatch must be
    skipped.  Registry/lookup failures fail OPEN (return ``None``) so a
    transient registry hiccup never silently freezes a healthy butler's
    schedule.
    """
    if eligibility_pool is None or not butler_name:
        return None

    try:
        from butlers.tools.switchboard.registry.registry import resolve_routing_target

        target, error = await resolve_routing_target(
            eligibility_pool,
            butler_name,
            allow_stale=False,
            allow_quarantined=False,
        )
    except Exception:
        # Registry unreachable / schema missing — fail open so scheduling keeps
        # working for healthy butlers; do not gate on infrastructure errors.
        logger.warning(
            "Scheduler eligibility check failed for butler %r; proceeding with dispatch",
            butler_name,
            exc_info=True,
        )
        return None

    if target is None:
        # error is the registry's own reason string (e.g. quarantined/stale).
        return error or f"Butler {butler_name!r} is not eligible for scheduled dispatch"
    return None


async def tick(
    pool: asyncpg.Pool,
    dispatch_fn,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    metrics: ButlerMetrics | None = None,
    butler_name: str | None = None,
    notify_fn=None,
    completion_hooks: dict[str, Any] | None = None,
    eligibility_pool: asyncpg.Pool | None = None,
    default_timezone: str = _DEFAULT_SCHEDULE_TIMEZONE,
) -> int:
    """Evaluate due tasks and dispatch them.

    Passes executed in order:
      1. Deadline evaluation pass — fire due threshold alerts, expire past-target tasks.
      2. Cron dispatch pass — standard cron-scheduled tasks.
      3. Event chain trigger detection pass — fire chains whose triggers have occurred.
      4. Deferred notification flush pass — deliver/expire deferred notifications.

    Queries ``scheduled_tasks`` WHERE ``enabled=true AND next_run_at <= now()``
    for cron tasks.  Deadline tasks are handled separately.
    For each due cron task, calls
    ``dispatch_fn(prompt=..., trigger_source="schedule:<task-name>")``.
    After dispatch, updates ``next_run_at``, ``last_run_at``, and ``last_result``.
    If dispatch fails, logs the error and stores the error in ``last_result``,
    but continues to the next task.

    Creates a ``butler.tick`` span with attributes:
      - ``tasks_due`` — count of due cron tasks
      - ``tasks_run`` — count of successfully dispatched cron tasks
      - ``deadlines_evaluated`` — count of deadline tasks evaluated
      - ``chains_fired`` — count of event chains fired
      - ``deferred_flushed`` — count of deferred notifications delivered/expired

    When *butler_name* is provided, ``get_active_seasons()`` is queried once
    per tick.  If active seasonal periods exist, their names are prepended to
    the prompt text as a ``[Seasonal context: ...]`` prefix for every
    prompt-mode dispatch.  This allows butlers to adjust behaviour based on
    which seasonal periods are currently active without requiring any change to
    the ``dispatch_fn`` signature.  Job-mode dispatches are unaffected (prompt
    is not used for job dispatch).

    Args:
        pool: asyncpg connection pool.
        dispatch_fn: Async callable matching ``Spawner.trigger`` signature.
        butler_name: Butler instance name used to query ``seasonal_periods``.
            When ``None`` (default), seasonal context injection is skipped.
        notify_fn: Optional async callable used by the deferred notification
            flush pass to deliver stored ``notify.v1`` envelopes via the
            standard notify pipeline.  Signature::

                async def notify_fn(envelope: dict) -> None: ...

            When ``None``, due notifications are left ``pending`` and retried
            on the next tick.  The scheduler loop in the daemon wires this to
            the butler's own notify delivery path.
        completion_hooks: Optional mapping of ``task_name → async callable``
            invoked after a prompt-mode task dispatches (regardless of
            success/failure).  Signature::

                async def hook(*, task_name: str, result: Any, run_at: datetime) -> None: ...

            Hooks are called with the task name, the dispatch result (a
            SpawnerResult or None), and the wall-clock time the tick fired.
            Hook exceptions are logged and swallowed — a hook failure never
            blocks task progression or advances to the next tick.
            Job-mode tasks do NOT trigger completion hooks.
        eligibility_pool: Optional asyncpg pool scoped to the Switchboard schema
            (``butler_registry``).  When provided alongside *butler_name*, the
            butler's eligibility is checked via the canonical
            :func:`resolve_routing_target` accessor before any dispatch.  If the
            butler is paused/quarantined (or stale), the deadline- and
            cron-dispatch passes are SKIPPED — due ticks are dropped, not
            queued, so a paused butler does not stampede catch-up dispatches on
            resume.  ``next_run_at`` is left untouched while gated, so the
            schedule resumes naturally on the next eligible tick.  When ``None``
            (e.g. unit tests, or a context without a registry), no gating is
            applied and all passes run as before.

    Returns:
        The number of tasks successfully dispatched (cron + deadline).
    """
    from butlers.core.seasonal import get_active_seasons as _get_active_seasons

    tracer = trace.get_tracer("butlers")
    with tracer.start_as_current_span("butler.tick") as span:
        now = datetime.now(UTC)

        # Hoist schema probes: deadline and cron dispatch share these results.
        scheduled_task_columns = await _existing_columns(
            pool,
            "scheduled_tasks",
            {"task_type", "max_token_budget", "until_at"},
        )
        _has_task_type_col = "task_type" in scheduled_task_columns
        _has_budget_col = "max_token_budget" in scheduled_task_columns
        _has_until_at_col = "until_at" in scheduled_task_columns
        if not _has_until_at_col:
            logger.warning(
                "scheduled_tasks.until_at column missing in current schema; "
                "using NULL projection until core migrations are applied"
            )

        # ------------------------------------------------------------------
        # Seasonal context — queried once per tick, injected into ALL dispatches
        # (both deadline pass and cron pass).  Must be queried before Pass 1 so
        # that deadline dispatches receive the same seasonal prefix as cron tasks.
        # ------------------------------------------------------------------
        active_seasons: list[dict[str, Any]] = []
        if butler_name:
            try:
                active_seasons = await _get_active_seasons(pool, butler_name)
            except Exception:
                logger.warning(
                    "Failed to query active seasons for butler %r; skipping seasonal context",
                    butler_name,
                    exc_info=True,
                )

        # ------------------------------------------------------------------
        # Eligibility gate — a paused/quarantined (or stale) butler must NOT
        # fire its scheduled deadline/cron ticks.  We consult the canonical
        # Switchboard registry accessor (resolve_routing_target) so this matches
        # the routing path's notion of "eligible".  When gated, both dispatch
        # passes are SKIPPED outright (no catch-up queue): next_run_at is left
        # untouched so the schedule resumes naturally once the butler is
        # un-paused.  Event-chain bookkeeping (Pass 3) and deferred-notification
        # flush (Pass 4) still run — neither spawns the gated butler.
        # ------------------------------------------------------------------
        dispatch_gate_reason = await _butler_dispatch_gated(eligibility_pool, butler_name)
        span.set_attribute("dispatch_gated", dispatch_gate_reason is not None)

        if dispatch_gate_reason is not None:
            logger.info(
                "Scheduler: skipping deadline/cron dispatch for butler %r — %s",
                butler_name,
                dispatch_gate_reason,
            )
            deadlines_evaluated = 0
            deadline_dispatched = 0
            span.set_attribute("deadlines_evaluated", 0)
            span.set_attribute("deadline_dispatched", 0)
        else:
            # --- Pass 1: Deadline evaluation (before cron dispatch) ---
            deadlines_evaluated, deadline_dispatched = await _tick_deadline_pass(
                pool,
                dispatch_fn,
                now=now,
                has_task_type_col=_has_task_type_col,
                stagger_key=stagger_key,
                max_stagger_seconds=max_stagger_seconds,
                metrics=metrics,
                active_seasons=active_seasons,
            )
            span.set_attribute("deadlines_evaluated", deadlines_evaluated)
            span.set_attribute("deadline_dispatched", deadline_dispatched)

        # --- Pass 2: Cron dispatch ---
        # If task_type column doesn't exist (legacy schema), treat all rows as cron.
        # If it does exist, skip deadline tasks (handled in pass 1).
        # When the butler is gated (paused/quarantined/stale), skip the cron
        # fetch entirely so no rows are dispatched and next_run_at is preserved.
        rows: list[asyncpg.Record] = []
        if dispatch_gate_reason is None:
            if _has_task_type_col:
                cron_filter = "AND COALESCE(task_type, 'cron') = 'cron'"
            else:
                cron_filter = ""
            _budget_col_select = ", max_token_budget" if _has_budget_col else ""
            _until_at_select = (
                ", until_at" if _has_until_at_col else ", NULL::timestamptz AS until_at"
            )
            rows = await pool.fetch(
                f"""
                SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
                       complexity, timezone, next_run_at{_until_at_select}{_budget_col_select}
                FROM scheduled_tasks
                WHERE enabled = true
                  {cron_filter}
                  AND next_run_at <= $1
                ORDER BY next_run_at
                """,
                now,
            )

        tasks_due = len(rows)
        span.set_attribute("tasks_due", tasks_due)

        dispatched = 0
        for row in rows:
            task_id = row["id"]
            original_next_run_at = row["next_run_at"]
            name = row["name"]
            prompt = row["prompt"]
            cron = row["cron"]
            dispatch_mode = row["dispatch_mode"]
            job_name = row["job_name"]
            job_args = _jsonb_to_dict(row["job_args"], context=f"scheduled_tasks[{name}]")
            task_complexity = _parse_complexity_from_db_row(row, name)
            max_token_budget: int | None = row["max_token_budget"] if _has_budget_col else None

            # Effective cron timezone: a non-UTC per-row value overrides; the
            # default 'UTC'/NULL sentinel follows the owner's general timezone.
            # row.get keeps legacy/pre-core_112 rows (no column) tolerant.
            task_timezone = _effective_schedule_timezone(row.get("timezone"), default_timezone)

            until_at = row["until_at"]

            # --- Compute next_run_at before claiming ---
            # We need it in the claim UPDATE so the DB transition is atomic.
            next_run_at = _next_run(
                cron,
                timezone=task_timezone,
                stagger_key=stagger_key,
                max_stagger_seconds=max_stagger_seconds,
            )
            should_auto_disable = until_at is not None and next_run_at > until_at

            # --- Claim this occurrence atomically (idempotency guard) ---
            # Use next_run_at as an optimistic version field.  The UPDATE succeeds
            # only when next_run_at still matches the value we read, meaning no
            # concurrent tick (MCP tick tool, force-tick API, or a rapid second
            # loop iteration) has already claimed this occurrence.
            # If the claim returns nothing, skip silently — the occurrence was
            # handled elsewhere.
            if should_auto_disable:
                claim_row = await pool.fetchrow(
                    """
                    UPDATE scheduled_tasks
                    SET enabled = false, next_run_at = NULL, updated_at = now()
                    WHERE id = $1 AND next_run_at IS NOT DISTINCT FROM $2
                    RETURNING id
                    """,
                    task_id,
                    original_next_run_at,
                )
            else:
                claim_row = await pool.fetchrow(
                    """
                    UPDATE scheduled_tasks
                    SET next_run_at = $2, updated_at = now()
                    WHERE id = $1 AND next_run_at IS NOT DISTINCT FROM $3
                    RETURNING id
                    """,
                    task_id,
                    next_run_at,
                    original_next_run_at,
                )

            if claim_row is None:
                logger.debug(
                    "Scheduled task %r already claimed by a concurrent tick; skipping",
                    name,
                )
                continue

            if should_auto_disable:
                logger.info(
                    "Scheduled task %s has passed until_at (%s); auto-disabling", name, until_at
                )

            result_json: str | None = None
            dispatch_result: Any = None
            try:
                if dispatch_mode == _DISPATCH_MODE_PROMPT:
                    # Prepend seasonal context to the prompt when active seasons exist.
                    # We prepend as a prompt prefix rather than passing a separate kwarg
                    # so that dispatch_fn (Spawner.trigger / _dispatch_scheduled_task)
                    # does not need to be aware of seasonal periods.
                    dispatched_prompt = _prepend_seasonal_context(prompt, active_seasons)
                    dispatch_kwargs: dict[str, Any] = {
                        "prompt": dispatched_prompt,
                        "trigger_source": f"schedule:{name}",
                        "complexity": task_complexity,
                    }
                    if max_token_budget is not None:
                        dispatch_kwargs["max_token_budget"] = max_token_budget
                    dispatch_result = await dispatch_fn(**dispatch_kwargs)
                    result = dispatch_result
                elif dispatch_mode == _DISPATCH_MODE_JOB:
                    result = await dispatch_fn(
                        job_name=job_name,
                        job_args=job_args,
                        trigger_source=f"schedule:{name}",
                    )
                else:
                    raise RuntimeError(
                        f"Unsupported dispatch_mode {dispatch_mode!r} for scheduled task {name!r}"
                    )
                result_json = _result_to_jsonb(result)
                dispatched += 1
                if metrics is not None:
                    metrics.record_task_dispatched(
                        butler=stagger_key or "unknown",
                        task_name=name,
                        outcome="success",
                    )
                logger.info("Dispatched scheduled task: %s", name)
            except Exception as exc:
                logger.exception("Failed to dispatch scheduled task: %s", name)
                result_json = _result_to_jsonb({"error": str(exc)})
                if metrics is not None:
                    metrics.record_task_dispatched(
                        butler=stagger_key or "unknown",
                        task_name=name,
                        outcome="failure",
                    )

            # Fire completion hook for prompt-mode tasks (after success or failure).
            # Hooks are butler-specific post-processing (e.g. cache writes).
            # A hook exception is logged and swallowed — it never blocks task progression.
            if dispatch_mode == _DISPATCH_MODE_PROMPT and completion_hooks:
                hook = completion_hooks.get(name)
                if hook is not None:
                    try:
                        await hook(task_name=name, result=dispatch_result, run_at=now)
                    except Exception:
                        logger.exception(
                            "Completion hook for scheduled task %r raised; continuing", name
                        )

            # Record the dispatch outcome.  next_run_at was already advanced in the
            # claim step above; only last_run_at and last_result need updating here.
            await pool.execute(
                """
                UPDATE scheduled_tasks
                SET last_run_at = $2, last_result = $3, updated_at = now()
                WHERE id = $1
                """,
                task_id,
                now,
                result_json,
            )

        span.set_attribute("tasks_run", dispatched)

        # --- Pass 3: Event chain trigger detection (after cron/deadline dispatch) ---
        chains_fired = await _tick_event_chain_pass(pool, dispatch_fn, now)
        span.set_attribute("chains_fired", chains_fired)

        # --- Pass 4: Deferred notification flush (after chain detection) ---
        deferred_flushed = await _tick_deferred_notification_pass(pool, now, notify_fn=notify_fn)
        span.set_attribute("deferred_flushed", deferred_flushed)

        return dispatched + deadline_dispatched


async def schedule_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return all scheduled tasks.

    Returns:
        List of task records as dicts.
    """
    rows = await pool.fetch(
        """
        SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
               complexity,
               timezone, start_at, end_at, until_at, display_title, calendar_event_id,
               source, enabled,
               next_run_at, last_run_at, last_result,
               created_at, updated_at
        FROM scheduled_tasks
        ORDER BY name
        """
    )
    tasks: list[dict[str, Any]] = []
    for row in rows:
        task = dict(row)
        task["job_args"] = _jsonb_to_dict(
            task.get("job_args"),
            context=f"scheduled_tasks[{task['name']}]",
        )
        tasks.append(task)
    return tasks


async def schedule_create(
    pool: asyncpg.Pool,
    name: str,
    cron: str | None = None,
    prompt: str | None = None,
    *,
    task_type: str = "cron",
    dispatch_mode: str = _DISPATCH_MODE_PROMPT,
    job_name: str | None = None,
    job_args: dict[str, Any] | None = None,
    complexity: str | None = None,
    timezone: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    until_at: datetime | None = None,
    display_title: str | None = None,
    description: str | None = None,
    location: str | None = None,
    calendar_event_id: str | None = None,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    # deadline-specific fields (required when task_type='deadline')
    target_date: Any | None = None,
    lead_time_days: int | None = None,
    alert_thresholds: list[dict[str, Any]] | None = None,
    depends_on: list[str] | None = None,
) -> uuid.UUID:
    """Create a runtime scheduled task.

    Validates cron syntax via croniter. Sets ``source='db'``.
    Computes initial ``next_run_at``.

    When ``task_type='deadline'``, deadline-specific fields (``target_date``,
    ``lead_time_days``, ``alert_thresholds``) are required and the call is
    delegated to the deadline_create helper. A cron expression is not required
    for deadline tasks (the deadline evaluation pass drives dispatch).

    Args:
        pool: asyncpg connection pool.
        name: Human-readable task name.
        cron: Cron expression (5-field). Required for cron tasks; ignored for
            deadline tasks (a default daily cron is used internally).
        prompt: Prompt text for prompt-mode schedules.
        task_type: ``'cron'`` (default) or ``'deadline'``.
        target_date: Due date (``datetime.date`` or ISO string). Required when
            ``task_type='deadline'``.
        lead_time_days: Days before target to begin alerting. Required when
            ``task_type='deadline'``.
        alert_thresholds: List of ``{days_before, severity}`` dicts. Required
            when ``task_type='deadline'``.
        depends_on: Optional list of deadline task UUID strings that must reach
            ``'completed'`` before this deadline's thresholds are evaluated.

    Returns:
        The new task's UUID.

    Raises:
        ValueError: If the cron expression is invalid or if the name already exists.
    """
    if task_type not in ("cron", "deadline"):
        raise ValueError(f"task_type must be 'cron' or 'deadline' (got {task_type!r})")

    # --- Deadline branch: delegate to deadline_create -----------------------
    if task_type == "deadline":
        from datetime import date as _date

        from butlers.core.temporal.deadlines import validate_deadline_input
        from butlers.core.temporal.deadlines_db import deadline_create as _dl_create

        if target_date is None:
            raise ValueError("target_date is required when task_type='deadline'")
        if lead_time_days is None:
            raise ValueError("lead_time_days is required when task_type='deadline'")
        if alert_thresholds is None:
            raise ValueError("alert_thresholds is required when task_type='deadline'")

        parsed_date = (
            _date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
        )
        validate_deadline_input(
            target_date=parsed_date,
            lead_time_days=lead_time_days,
            alert_thresholds=alert_thresholds,
        )
        return await _dl_create(
            pool,
            name=name,
            prompt=prompt,
            target_date=parsed_date,
            lead_time_days=lead_time_days,
            alert_thresholds=alert_thresholds,
            depends_on=depends_on,
        )

    # --- Cron branch --------------------------------------------------------
    if cron is None:
        raise ValueError("cron is required when task_type='cron'")
    if not croniter.is_valid(cron):
        raise ValueError(f"Invalid cron expression: {cron!r}")
    dispatch_mode, prompt, job_name, job_args, complexity = _normalize_schedule_dispatch(
        dispatch_mode=dispatch_mode,
        prompt=prompt,
        job_name=job_name,
        job_args=job_args,
        complexity=complexity,
        context="schedule_create",
    )
    (
        timezone,
        start_at,
        end_at,
        until_at,
        display_title,
        calendar_event_id,
    ) = _normalize_schedule_projection_fields(
        timezone=timezone,
        start_at=start_at,
        end_at=end_at,
        until_at=until_at,
        display_title=display_title,
        calendar_event_id=calendar_event_id,
        context="schedule_create",
    )
    if timezone is None:
        timezone = "UTC"

    next_run_at = _next_run(
        cron,
        timezone=timezone,
        stagger_key=stagger_key,
        max_stagger_seconds=max_stagger_seconds,
    )
    try:
        task_id: uuid.UUID = await pool.fetchval(
            """
            INSERT INTO scheduled_tasks (
                name,
                cron,
                dispatch_mode,
                prompt,
                job_name,
                job_args,
                complexity,
                timezone,
                start_at,
                end_at,
                until_at,
                display_title,
                description,
                location,
                calendar_event_id,
                source,
                enabled,
                next_run_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                'db', true, $16
            )
            RETURNING id
            """,
            name,
            cron,
            dispatch_mode,
            prompt,
            job_name,
            _dict_to_jsonb(job_args),
            complexity,
            timezone,
            start_at,
            end_at,
            until_at,
            display_title,
            description,
            location,
            calendar_event_id,
            next_run_at,
        )
    except asyncpg.UniqueViolationError:
        raise ValueError(f"Task name {name!r} already exists")
    logger.info("Created runtime schedule: %s (%s)", name, task_id)
    return task_id


async def schedule_update(
    pool: asyncpg.Pool,
    task_id: uuid.UUID,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    **fields,
) -> None:
    """Update fields on a scheduled task.

    Allowed fields: ``name``, ``cron``, ``dispatch_mode``, ``prompt``,
    ``job_name``, ``job_args``, ``complexity``, ``enabled``, ``timezone``,
    ``start_at``, ``end_at``, ``until_at``, ``display_title``, ``description``,
    ``location``, and ``calendar_event_id``.
    If ``cron`` is updated, recomputes ``next_run_at``.
    If ``enabled`` is set to ``true``, recomputes ``next_run_at``.
    If ``enabled`` is set to ``false``, sets ``next_run_at`` to ``NULL``.

    Args:
        pool: asyncpg connection pool.
        task_id: UUID of the task to update.
        **fields: Field names and new values.

    Raises:
        ValueError: If ``task_id`` is not found or if an invalid field is provided,
            or if the new cron expression is invalid.
    """
    allowed = {
        "name",
        "cron",
        "dispatch_mode",
        "prompt",
        "job_name",
        "job_args",
        "complexity",
        "enabled",
        "timezone",
        "start_at",
        "end_at",
        "until_at",
        "display_title",
        "description",
        "location",
        "calendar_event_id",
    }
    invalid = set(fields.keys()) - allowed
    if invalid:
        raise ValueError(f"Invalid fields: {invalid}")
    if not fields:
        return

    # Validate cron if provided
    if "cron" in fields and not croniter.is_valid(fields["cron"]):
        raise ValueError(f"Invalid cron expression: {fields['cron']!r}")
    if "dispatch_mode" in fields:
        fields["dispatch_mode"] = _normalize_dispatch_mode(
            fields["dispatch_mode"],
            context="schedule_update",
        )
    if "complexity" in fields:
        fields["complexity"] = _normalize_complexity(
            fields["complexity"],
            context="schedule_update",
        )
    # Check task exists and fetch current state
    existing = await pool.fetchrow(
        """
        SELECT id, cron, enabled, dispatch_mode, prompt, job_name, job_args,
               complexity, timezone, start_at, end_at, until_at, display_title,
               calendar_event_id
        FROM scheduled_tasks
        WHERE id = $1
        """,
        task_id,
    )
    if existing is None:
        raise ValueError(f"Task {task_id} not found")

    projection_fields = {
        "timezone",
        "start_at",
        "end_at",
        "until_at",
        "display_title",
        "calendar_event_id",
    }
    if projection_fields & fields.keys():
        if "timezone" in fields and fields["timezone"] is None:
            raise ValueError("schedule_update.timezone cannot be null")
        (
            timezone,
            start_at,
            end_at,
            until_at,
            display_title,
            calendar_event_id,
        ) = _normalize_schedule_projection_fields(
            timezone=fields.get("timezone", existing["timezone"]),
            start_at=fields.get("start_at", existing["start_at"]),
            end_at=fields.get("end_at", existing["end_at"]),
            until_at=fields.get("until_at", existing["until_at"]),
            display_title=fields.get("display_title", existing["display_title"]),
            calendar_event_id=fields.get("calendar_event_id", existing["calendar_event_id"]),
            context="schedule_update",
        )
        normalized_projection = {
            "timezone": timezone,
            "start_at": start_at,
            "end_at": end_at,
            "until_at": until_at,
            "display_title": display_title,
            "calendar_event_id": calendar_event_id,
        }
        for key in projection_fields:
            if key in fields:
                fields[key] = normalized_projection[key]

    existing_job_args = _jsonb_to_dict(existing["job_args"], context=f"scheduled_tasks[{task_id}]")

    normalized_fields: dict[str, Any] = dict(fields)
    dispatch_related = bool(
        {"dispatch_mode", "prompt", "job_name", "job_args"} & normalized_fields.keys()
    )

    if dispatch_related:
        requested_mode = normalized_fields.get("dispatch_mode")
        if requested_mode == _DISPATCH_MODE_PROMPT:
            normalized_fields.setdefault("job_name", None)
            normalized_fields.setdefault("job_args", None)
        elif requested_mode == _DISPATCH_MODE_JOB:
            normalized_fields.setdefault("prompt", None)

        merged = {
            "dispatch_mode": normalized_fields.get("dispatch_mode", existing["dispatch_mode"]),
            "prompt": normalized_fields.get("prompt", existing["prompt"]),
            "job_name": normalized_fields.get("job_name", existing["job_name"]),
            "job_args": normalized_fields.get("job_args", existing_job_args),
        }
        dispatch_mode, prompt, job_name, job_args, _dispatch_complexity = (
            _normalize_schedule_dispatch(
                dispatch_mode=merged["dispatch_mode"],
                prompt=merged["prompt"],
                job_name=merged["job_name"],
                job_args=merged["job_args"],
                context="schedule_update",
            )
        )
        normalized_fields["dispatch_mode"] = dispatch_mode
        normalized_fields["prompt"] = prompt
        normalized_fields["job_name"] = job_name
        normalized_fields["job_args"] = job_args

    # Build dynamic UPDATE with all fields including next_run_at if cron changed
    set_clauses = []
    params: list[Any] = [task_id]
    idx = 2
    for key, value in normalized_fields.items():
        if key == "job_args":
            value = _dict_to_jsonb(value)
        set_clauses.append(f"{key} = ${idx}")
        params.append(value)
        idx += 1

    # Handle next_run_at based on enabled toggle or cron change
    cron = normalized_fields.get("cron", existing["cron"])
    # Effective cron timezone: an updated timezone wins, else the stored one.
    effective_timezone = normalized_fields.get("timezone", existing["timezone"])
    if "enabled" in normalized_fields:
        if normalized_fields["enabled"]:
            # Enabling: recompute next_run_at from current cron
            next_run_at = _next_run(
                cron,
                timezone=effective_timezone,
                stagger_key=stagger_key,
                max_stagger_seconds=max_stagger_seconds,
            )
            set_clauses.append(f"next_run_at = ${idx}")
            params.append(next_run_at)
            idx += 1
        else:
            # Disabling: set next_run_at to NULL
            set_clauses.append(f"next_run_at = ${idx}")
            params.append(None)
            idx += 1
    elif "cron" in normalized_fields:
        # Cron changed (and enabled not explicitly set): recompute next_run_at
        next_run_at = _next_run(
            normalized_fields["cron"],
            timezone=effective_timezone,
            stagger_key=stagger_key,
            max_stagger_seconds=max_stagger_seconds,
        )
        set_clauses.append(f"next_run_at = ${idx}")
        params.append(next_run_at)
        idx += 1

    set_clauses.append("updated_at = now()")

    # Single atomic UPDATE statement
    query = f"UPDATE scheduled_tasks SET {', '.join(set_clauses)} WHERE id = $1"
    await pool.execute(query, *params)

    logger.info("Updated schedule %s: %s", task_id, list(normalized_fields.keys()))


async def schedule_delete(pool: asyncpg.Pool, task_id: uuid.UUID) -> None:
    """Delete a runtime scheduled task.

    TOML-sourced tasks cannot be deleted — they are managed via config sync.

    Args:
        pool: asyncpg connection pool.
        task_id: UUID of the task to delete.

    Raises:
        ValueError: If the task is ``source='toml'`` or if ``task_id`` is not found.
    """
    row = await pool.fetchrow("SELECT source FROM scheduled_tasks WHERE id = $1", task_id)
    if row is None:
        raise ValueError(f"Task {task_id} not found")
    if row["source"] == "toml":
        raise ValueError(f"Cannot delete TOML-sourced task {task_id}; disable it instead")

    await pool.execute("DELETE FROM scheduled_tasks WHERE id = $1", task_id)
    logger.info("Deleted runtime schedule: %s", task_id)
