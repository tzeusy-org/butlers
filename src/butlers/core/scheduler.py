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

import asyncpg
from croniter import croniter
from opentelemetry import trace

from butlers.core.metrics import ButlerMetrics
from butlers.core.model_routing import Complexity

logger = logging.getLogger(__name__)

_DEFAULT_MAX_STAGGER_SECONDS = 15 * 60
_DISPATCH_MODE_PROMPT = "prompt"
_DISPATCH_MODE_JOB = "job"
_ALLOWED_DISPATCH_MODES = {_DISPATCH_MODE_PROMPT, _DISPATCH_MODE_JOB}
_ALLOWED_COMPLEXITY_VALUES = {c.value for c in Complexity}
_DEFAULT_COMPLEXITY = Complexity.MEDIUM.value

# Pattern to find candidate skill names in prompt text (kebab-case words).
_SKILL_NAME_PATTERN = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b")


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
            "Unknown complexity value %r for task %s; defaulting to medium",
            raw_complexity,
            task_name,
        )
        return Complexity.MEDIUM


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


def _next_run(
    cron: str,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    now: datetime | None = None,
) -> datetime:
    """Compute the next run time for a cron expression from now (UTC)."""
    anchor = now or datetime.now(UTC)
    next_run = croniter(cron, anchor).get_next(datetime).replace(tzinfo=UTC)
    offset_seconds = _stagger_offset_seconds(
        cron,
        stagger_key=stagger_key,
        max_stagger_seconds=max_stagger_seconds,
        now=anchor,
    )
    if offset_seconds:
        return next_run + timedelta(seconds=offset_seconds)
    return next_run


def _result_to_jsonb(result: Any) -> str | None:
    """Convert a dispatch result to a JSON string suitable for JSONB storage.

    Handles dataclass-like objects (SpawnerResult) by extracting their __dict__,
    plain dicts, and falls back to string representation.
    """
    if result is None:
        return None
    if hasattr(result, "__dict__") and not isinstance(result, type):
        return json.dumps(result.__dict__, default=str)
    if isinstance(result, dict):
        return json.dumps(result, default=str)
    return json.dumps({"result": str(result)}, default=str)


def _dict_to_jsonb(value: dict[str, Any] | None) -> str | None:
    """Convert a dict payload to a JSON string suitable for JSONB binding."""
    if value is None:
        return None
    return json.dumps(value, default=str)


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
    """
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

        normalized_schedules.append(
            {
                "name": name,
                "cron": cron,
                "dispatch_mode": dispatch_mode,
                "prompt": prompt,
                "job_name": job_name,
                "job_args": job_args,
                "complexity": complexity,
            }
        )

    toml_names = {s["name"] for s in normalized_schedules}

    # Fetch existing tasks whose names match any TOML schedule (regardless of source).
    # A runtime-created task (source='db') may share a name with a TOML schedule;
    # TOML takes ownership on next startup to avoid unique-constraint violations.
    rows = await pool.fetch(
        """
        SELECT id, name, source, cron, prompt, dispatch_mode, job_name, job_args,
               complexity, enabled
        FROM scheduled_tasks
        WHERE name = ANY($1::text[])
        """,
        list(toml_names),
    )
    # Also fetch all remaining toml-sourced tasks (for the disable-removed-tasks pass below).
    toml_only_rows = await pool.fetch(
        """
        SELECT id, name, cron, prompt, dispatch_mode, job_name, job_args,
               complexity, enabled
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
        prompt = entry["prompt"]
        dispatch_mode = entry["dispatch_mode"]
        job_name = entry["job_name"]
        job_args = entry["job_args"]
        complexity = entry["complexity"]
        next_run_at = _next_run(
            cron,
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
            if needs_update:
                await pool.execute(
                    """
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
                        updated_at = now()
                    WHERE id = $1
                    """,
                    existing["id"],
                    cron,
                    dispatch_mode,
                    prompt,
                    job_name,
                    _dict_to_jsonb(job_args),
                    complexity,
                    next_run_at,
                )
                logger.info("Updated TOML schedule: %s", name)
        else:
            # Insert new TOML task
            await pool.execute(
                """
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
                    next_run_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'toml', true, $8)
                """,
                name,
                cron,
                dispatch_mode,
                prompt,
                job_name,
                _dict_to_jsonb(job_args),
                complexity,
                next_run_at,
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


async def _tick_deadline_pass(
    pool: asyncpg.Pool,
    dispatch_fn,
    now: datetime,
) -> int:
    """Evaluate deadline tasks: check expiry and fire due thresholds.

    - Marks tasks past their target_date as expired and disables them.
    - For tasks with a newly due threshold, dispatches and updates fired_thresholds
      and deadline_status.

    Returns the number of deadline tasks evaluated.
    Returns 0 if the temporal intelligence columns do not exist yet (graceful degradation).
    """
    import json as _json

    from butlers.core.temporal.deadlines import (
        compute_days_remaining,
        compute_expiry_transition,
        compute_next_deadline_status,
        find_unfired_threshold,
        is_deadline_blocked,
    )

    # Check if temporal intelligence columns exist (graceful degradation for older schemas)
    has_temporal_cols = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'scheduled_tasks' AND column_name = 'task_type'
        )
        """
    )
    if not has_temporal_cols:
        return 0

    deadline_rows = await pool.fetch(
        """
        SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
               complexity, until_at,
               target_date, lead_time_days, alert_thresholds,
               deadline_status, fired_thresholds, depends_on
        FROM scheduled_tasks
        WHERE enabled = true AND task_type = 'deadline'
        """,
    )

    evaluated = 0

    for row in deadline_rows:
        evaluated += 1
        task_id = row["id"]
        name = row["name"]
        target_date = row["target_date"]
        current_status = row["deadline_status"] or "pending"

        # Deserialize JSONB fields
        alert_thresholds = row["alert_thresholds"]
        if isinstance(alert_thresholds, str):
            alert_thresholds = _json.loads(alert_thresholds)
        alert_thresholds = alert_thresholds or []

        fired_thresholds = row["fired_thresholds"]
        if isinstance(fired_thresholds, str):
            fired_thresholds = _json.loads(fired_thresholds)
        fired_thresholds = fired_thresholds or []

        depends_on = row["depends_on"]
        if isinstance(depends_on, str):
            depends_on = _json.loads(depends_on)
        depends_on = depends_on or []

        if target_date is None:
            logger.warning("Deadline task %r has no target_date; skipping", name)
            continue

        # Expiry check: if target_date has passed, mark expired and disable
        new_status, should_disable = compute_expiry_transition(
            current_status=current_status,
            target_date=target_date,
        )
        if should_disable:
            logger.info(
                "Deadline task %r has expired (target_date=%s); disabling", name, target_date
            )
            await pool.execute(
                """
                UPDATE scheduled_tasks
                SET deadline_status = $2, enabled = false
                WHERE id = $1
                """,
                task_id,
                new_status,
            )
            continue

        # Skip terminal states
        if current_status in ("completed", "expired"):
            continue

        # Dependency check
        if depends_on:
            # Fetch statuses of dependency tasks
            dep_rows = await pool.fetch(
                """
                SELECT id::text, deadline_status FROM scheduled_tasks
                WHERE id::text = ANY($1::text[])
                """,
                depends_on,
            )
            dependency_statuses = {r["id"]: r["deadline_status"] or "pending" for r in dep_rows}
            if is_deadline_blocked(
                depends_on=depends_on,
                dependency_statuses=dependency_statuses,
            ):
                logger.debug("Deadline task %r is blocked by dependencies; skipping", name)
                continue

        # Threshold evaluation
        days_remaining = compute_days_remaining(target_date=target_date)
        threshold = find_unfired_threshold(
            days_remaining=days_remaining,
            alert_thresholds=alert_thresholds,
            fired_thresholds=fired_thresholds,
        )

        if threshold is None:
            continue

        # A threshold is due — dispatch and update
        dispatch_mode = row["dispatch_mode"]
        prompt = row["prompt"]
        job_name = row["job_name"]
        job_args = _jsonb_to_dict(row["job_args"], context=f"scheduled_tasks[{name}]")
        task_complexity = _parse_complexity_from_db_row(row, name)

        try:
            if dispatch_mode == _DISPATCH_MODE_PROMPT:
                await dispatch_fn(
                    prompt=prompt,
                    trigger_source=f"deadline:{name}",
                    complexity=task_complexity,
                )
            elif dispatch_mode == _DISPATCH_MODE_JOB:
                await dispatch_fn(
                    job_name=job_name,
                    job_args=job_args,
                    trigger_source=f"deadline:{name}",
                )
            logger.info(
                "Dispatched deadline task: %s (threshold=%d days)",
                name,
                threshold["days_before"],
            )
        except Exception:
            logger.exception("Failed to dispatch deadline task: %s", name)
            continue

        # Update fired_thresholds and deadline_status
        new_fired = list(fired_thresholds) + [threshold]
        new_status = compute_next_deadline_status(
            current_status=current_status,
            fired_threshold=threshold,
        )
        await pool.execute(
            """
            UPDATE scheduled_tasks
            SET fired_thresholds = $2::jsonb,
                deadline_status = $3,
                last_run_at = $4
            WHERE id = $1
            """,
            task_id,
            _json.dumps(new_fired),
            new_status,
            now,
        )

    return evaluated


async def _tick_event_chain_pass(
    pool: asyncpg.Pool,
    dispatch_fn,
    now: datetime,
) -> int:
    """Detect event chain triggers and materialize actions.

    Currently handles:
    - calendar_event_end: calendar_projection events that ended before now

    Returns the number of chains fired.
    """
    import json as _json

    from butlers.core.temporal.event_chains import materialize_chain_actions, should_fire_chain

    chains_fired = 0

    # Check if calendar_projection table exists (it's optional)
    table_exists = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'calendar_projection'
        )
        """
    )
    if not table_exists:
        return 0

    # Find calendar events that ended before now and haven't triggered chains yet
    ended_events = await pool.fetch(
        """
        SELECT event_id, butler_name
        FROM calendar_projection
        WHERE end_at <= $1 AND chain_triggered = false
        """,
        now,
    )

    if not ended_events:
        return 0

    # For each ended event, find active chains triggered by calendar_event_end
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

            # Materialize actions into one-shot tasks
            tasks = materialize_chain_actions(
                chain_name=chain_name,
                actions=actions,
                fired_at=now,
            )

            # Insert materialized tasks into scheduled_tasks
            for task in tasks:
                try:
                    await pool.execute(
                        """
                        INSERT INTO scheduled_tasks
                            (name, cron, dispatch_mode, prompt, job_name, job_args,
                             source, next_run_at, until_at, enabled)
                        VALUES
                            ($1, '* * * * *', $2, $3, $4, $5::jsonb,
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

            # Mark chain as fired
            await pool.execute(
                """
                UPDATE event_chains SET status = 'fired'
                WHERE id = $1
                """,
                chain_id,
            )
            chains_fired += 1
            logger.info("Fired event chain %r for calendar event %r", chain_name, event_id)

        # Mark calendar event as chain_triggered
        await pool.execute(
            """
            UPDATE calendar_projection SET chain_triggered = true
            WHERE event_id = $1 AND butler_name = $2
            """,
            event_id,
            butler_name,
        )

    return chains_fired


async def _tick_deferred_notification_pass(
    pool: asyncpg.Pool,
    dispatch_fn,
    now: datetime,
) -> int:
    """Flush deferred notifications: expire old ones and deliver due ones.

    - Marks pending notifications > 24h past deliver_at as expired.
    - Delivers pending notifications with deliver_at <= now.

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

    delivered = 0
    for row in due_rows:
        notif_id = row["id"]
        channel = row["channel"]
        message = row["message"]
        envelope = row["envelope"]
        if isinstance(envelope, str):
            envelope = _json.loads(envelope)

        try:
            await dispatch_fn(
                prompt=message,
                trigger_source=f"deferred_notification:{notif_id}",
            )
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


async def tick(
    pool: asyncpg.Pool,
    dispatch_fn,
    *,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
    metrics: ButlerMetrics | None = None,
) -> int:
    """Evaluate due tasks and dispatch them.

    Runs three passes on each tick:
    1. Deadline pass: evaluates deadline tasks, fires due thresholds, expires past deadlines.
    2. Cron pass: dispatches standard cron-type tasks due for execution.
    3. Event chain pass: detects trigger conditions and materializes chain actions.
    4. Deferred notification pass: expires stale and delivers due deferred notifications.

    Creates a ``butler.tick`` span with attributes:
    - ``tasks_due`` / ``tasks_run``: cron task counts
    - ``deadlines_evaluated``: deadline tasks processed
    - ``chains_fired``: event chains triggered
    - ``deferred_flushed``: deferred notifications delivered

    Args:
        pool: asyncpg connection pool.
        dispatch_fn: Async callable matching ``Spawner.trigger`` signature.

    Returns:
        The number of tasks successfully dispatched (cron pass only).
    """
    tracer = trace.get_tracer("butlers")
    with tracer.start_as_current_span("butler.tick") as span:
        now = datetime.now(UTC)

        # ── Pass 1: Deadline evaluation (before cron dispatch) ──────────────
        deadlines_evaluated = await _tick_deadline_pass(pool, dispatch_fn, now)
        span.set_attribute("deadlines_evaluated", deadlines_evaluated)

        # ── Pass 2: Cron task dispatch ───────────────────────────────────────
        # If task_type column exists, filter to cron tasks only (deadline tasks handled above).
        # Otherwise fall back to all tasks (older schema without temporal intelligence).
        has_task_type_col = await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'scheduled_tasks' AND column_name = 'task_type'
            )
            """
        )
        if has_task_type_col:
            cron_filter = "AND (task_type IS NULL OR task_type = 'cron')"
        else:
            cron_filter = ""

        rows = await pool.fetch(
            f"""
            SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
                   complexity, until_at
            FROM scheduled_tasks
            WHERE enabled = true AND next_run_at <= $1
              {cron_filter}
            ORDER BY next_run_at
            """,
            now,
        )

        tasks_due = len(rows)
        span.set_attribute("tasks_due", tasks_due)

        dispatched = 0
        for row in rows:
            task_id = row["id"]
            name = row["name"]
            prompt = row["prompt"]
            cron = row["cron"]
            dispatch_mode = row["dispatch_mode"]
            job_name = row["job_name"]
            job_args = _jsonb_to_dict(row["job_args"], context=f"scheduled_tasks[{name}]")
            task_complexity = _parse_complexity_from_db_row(row, name)

            until_at = row["until_at"]

            result_json: str | None = None
            try:
                if dispatch_mode == _DISPATCH_MODE_PROMPT:
                    result = await dispatch_fn(
                        prompt=prompt,
                        trigger_source=f"schedule:{name}",
                        complexity=task_complexity,
                    )
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

            # Always advance next_run_at whether dispatch succeeded or failed.
            # If the computed next run would exceed until_at, auto-disable the task.
            next_run_at = _next_run(
                cron,
                stagger_key=stagger_key,
                max_stagger_seconds=max_stagger_seconds,
            )
            if until_at is not None and next_run_at > until_at:
                logger.info(
                    "Scheduled task %s has passed until_at (%s); auto-disabling", name, until_at
                )
                await pool.execute(
                    """
                    UPDATE scheduled_tasks
                    SET enabled = false, next_run_at = NULL,
                        last_run_at = $2, last_result = $3::jsonb,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    task_id,
                    now,
                    result_json,
                )
            else:
                await pool.execute(
                    """
                    UPDATE scheduled_tasks
                    SET next_run_at = $2, last_run_at = $3, last_result = $4::jsonb,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    task_id,
                    next_run_at,
                    now,
                    result_json,
                )

        span.set_attribute("tasks_run", dispatched)

        # ── Pass 3: Event chain trigger detection ────────────────────────────
        chains_fired = await _tick_event_chain_pass(pool, dispatch_fn, now)
        span.set_attribute("chains_fired", chains_fired)

        # ── Pass 4: Deferred notification flush ──────────────────────────────
        deferred_flushed = await _tick_deferred_notification_pass(pool, dispatch_fn, now)
        span.set_attribute("deferred_flushed", deferred_flushed)

        return dispatched


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
    cron: str,
    prompt: str | None = None,
    *,
    dispatch_mode: str = _DISPATCH_MODE_PROMPT,
    job_name: str | None = None,
    job_args: dict[str, Any] | None = None,
    complexity: str | None = None,
    timezone: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    until_at: datetime | None = None,
    display_title: str | None = None,
    calendar_event_id: str | None = None,
    stagger_key: str | None = None,
    max_stagger_seconds: int = _DEFAULT_MAX_STAGGER_SECONDS,
) -> uuid.UUID:
    """Create a runtime scheduled task.

    Validates cron syntax via croniter. Sets ``source='db'``.
    Computes initial ``next_run_at``.

    Args:
        pool: asyncpg connection pool.
        name: Human-readable task name.
        cron: Cron expression (5-field).
        prompt: Prompt text for prompt-mode schedules.

    Returns:
        The new task's UUID.

    Raises:
        ValueError: If the cron expression is invalid or if the name already exists.
    """
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
                calendar_event_id,
                source,
                enabled,
                next_run_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'db', true, $14)
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
    ``start_at``, ``end_at``, ``until_at``, ``display_title``, and
    ``calendar_event_id``.
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
    if "enabled" in normalized_fields:
        if normalized_fields["enabled"]:
            # Enabling: recompute next_run_at from current cron
            next_run_at = _next_run(
                cron,
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
