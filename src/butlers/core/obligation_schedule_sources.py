"""Dormant owning scheduled-row/source writer (RFC0038 D5).

No production caller is registered. Values here authenticate neither Git input,
context, catalog nor a creator. A future trusted owning config/context adapter
must supply actual normalized material; no caller-provided digest is accepted.
Current OFF sync/tick never import this module or perform its database IO.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from butlers.core.goal_admission import digest
from butlers.core.obligation_sources import (
    DefinitionRevision,
    ObligationSourceRepository,
    SourceConflict,
)
from butlers.core.obligations import DeclarationError, ObligationDeclaration

SCHEDULE_FIELDS = (
    "name",
    "cron",
    "dispatch_mode",
    "prompt",
    "job_name",
    "job_args",
    "complexity",
    "enabled",
    "timezone",
    "continuity",
    "max_token_budget",
    "task_type",
    "target_date",
    "lead_time_days",
    "alert_thresholds",
    "start_at",
    "end_at",
    "until_at",
    "display_title",
    "description",
    "location",
    "calendar_event_id",
)
_TIMESTAMPS = {"start_at", "end_at", "until_at"}
_LIMIT = 131072


def _json(value: object) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise DeclarationError("invalid_schedule_material") from None
    if len(encoded.encode()) > _LIMIT:
        raise DeclarationError("schedule_material_too_large")
    return encoded


def _row_material(value: dict) -> str:
    """Validate an already-normalized complete row, not a second config parser."""
    if type(value) is not dict or set(value) != set(SCHEDULE_FIELDS):
        raise DeclarationError("incomplete_schedule_material")
    row = value.copy()
    if (
        type(row["name"]) is not str
        or not 0 < len(row["name"]) <= 256
        or type(row["cron"]) is not str
        or len(row["cron"]) > 256
        or not croniter.is_valid(row["cron"])
        or row["dispatch_mode"] not in {"prompt", "job"}
        or row["task_type"] not in {"cron", "deadline"}
        or type(row["enabled"]) is not bool
        or type(row["continuity"]) is not bool
        or type(row["complexity"]) is not str
        or not row["complexity"]
        or type(row["timezone"]) is not str
    ):
        raise DeclarationError("invalid_schedule_material")
    try:
        ZoneInfo(row["timezone"])
    except (ValueError, ZoneInfoNotFoundError):
        raise DeclarationError("invalid_schedule_timezone") from None
    for key in (
        "prompt",
        "job_name",
        "display_title",
        "description",
        "location",
        "calendar_event_id",
    ):
        if row[key] is not None and type(row[key]) is not str:
            raise DeclarationError("invalid_schedule_material")
    if row["dispatch_mode"] == "prompt":
        if (
            not row["prompt"]
            or not row["prompt"].strip()
            or row["job_name"] is not None
            or row["job_args"] is not None
        ):
            raise DeclarationError("invalid_schedule_dispatch")
    elif row["prompt"] is not None or not row["job_name"] or type(row["job_args"]) is not dict:
        raise DeclarationError("invalid_schedule_dispatch")
    for key in ("max_token_budget", "lead_time_days"):
        if row[key] is not None and (type(row[key]) is not int or row[key] < 0):
            raise DeclarationError("invalid_schedule_material")
    if row["max_token_budget"] == 0:
        raise DeclarationError("invalid_schedule_material")
    for key in _TIMESTAMPS:
        val = row[key]
        if val is not None:
            if type(val) is str:
                try:
                    val = datetime.fromisoformat(val)
                except ValueError:
                    raise DeclarationError("invalid_schedule_timestamp") from None
            if type(val) is not datetime or val.utcoffset() is None:
                raise DeclarationError("invalid_schedule_timestamp")
            row[key] = val.isoformat()
    target = row["target_date"]
    if target is not None:
        try:
            target = date.fromisoformat(target) if type(target) is str else target
        except ValueError:
            raise DeclarationError("invalid_schedule_date") from None
        if type(target) is not date:
            raise DeclarationError("invalid_schedule_date")
        row["target_date"] = target.isoformat()
    if row["task_type"] == "deadline":
        if (
            target is None
            or row["lead_time_days"] is None
            or type(row["alert_thresholds"]) is not list
        ):
            raise DeclarationError("incomplete_schedule_deadline")
    elif any(row[k] is not None for k in ("target_date", "lead_time_days", "alert_thresholds")):
        raise DeclarationError("invalid_schedule_deadline")
    return _json(row)


@dataclass(frozen=True)
class GitScheduleDefinition:
    """Internal staged material, NOT verified source/caller authority.

    context_json is canonical actual policy material, never an asserted digest
    or a 'verified' flag. Its real producer is not implemented/registered here.
    Tests supplying it prove software behavior only. Missing context never
    produces an active definition, even with an otherwise valid declaration.
    """

    owner_domain: str
    row_json: str
    declaration: ObligationDeclaration | None
    context_json: str | None

    def __post_init__(self):
        if (
            type(self.owner_domain) is not str
            or len(self.owner_domain) > 63
            or not re.fullmatch(r"[a-z][a-z0-9_-]*", self.owner_domain)
            or (
                self.declaration is not None and type(self.declaration) is not ObligationDeclaration
            )
        ):
            raise DeclarationError("invalid_schedule_material")
        try:
            if _row_material(json.loads(self.row_json)) != self.row_json:
                raise DeclarationError("noncanonical_schedule_material")
            if self.context_json is not None:
                context = json.loads(self.context_json)
                if type(context) is not dict or not context or _json(context) != self.context_json:
                    raise DeclarationError("invalid_schedule_context")
        except (TypeError, ValueError, RecursionError):
            raise DeclarationError("invalid_schedule_material") from None

    @property
    def schedule(self) -> dict:
        row = json.loads(self.row_json)
        for key in _TIMESTAMPS:
            if row[key] is not None:
                row[key] = datetime.fromisoformat(row[key])
        if row["target_date"] is not None:
            row["target_date"] = date.fromisoformat(row["target_date"])
        return row

    @property
    def unavailable_reason(self) -> str | None:
        if self.context_json is None:
            return "context_unavailable"
        if self.declaration is None:
            return "declaration_unavailable"
        if self.schedule["dispatch_mode"] != "prompt":
            return "deterministic_job"
        if not self.schedule["enabled"]:
            return "source_disabled"
        return None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    @property
    def material_digest(self) -> str:
        return digest(
            {
                "row": json.loads(self.row_json),
                "context": json.loads(self.context_json) if self.context_json else None,
                "declaration": self.declaration.digest if self.declaration else None,
            }
        )


def compile_git_schedule_definition(
    *,
    owner_domain: str,
    normalized_schedule: dict,
    declaration: ObligationDeclaration | None,
    context_material: dict | None,
) -> GitScheduleDefinition:
    """Copy full normalized source inputs; the value itself authenticates nothing."""
    try:
        return GitScheduleDefinition(
            owner_domain,
            _row_material(normalized_schedule),
            declaration,
            _json(context_material) if context_material is not None else None,
        )
    except (TypeError, ValueError, RecursionError):
        raise DeclarationError("invalid_schedule_material") from None


@dataclass(frozen=True)
class StagedGitScheduleDefinition:
    """Historical replay or staged result; commit/release and later fences remain."""

    definition: DefinitionRevision


async def stage_git_schedule_definition(
    repository: ObligationSourceRepository,
    *,
    command_id: UUID,
    expected_revision: str | None,
    definition: GitScheduleDefinition,
    source_id: UUID | None = None,
) -> StagedGitScheduleDefinition:
    """Write actual schedule + source revision/cancel/outbox in ONE owning TX.

    A savepoint prevents partial scheduled-row changes if a caller catches a
    refusal. It does not commit the outer transaction. Existing nominal/run
    fields are preserved; a new dormant row has NULL next_run_at. This is not
    the missing current-config/scheduler/cutover registration.
    """
    if (
        type(definition) is not GitScheduleDefinition
        or definition.owner_domain != repository.owner_domain
    ):
        raise SourceConflict("source_owner_mismatch")
    if source_id is not None and not isinstance(source_id, UUID):
        raise SourceConflict("invalid_source_id")
    if expected_revision is not None and (
        type(expected_revision) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_revision)
    ):
        raise SourceConflict("source_revision_conflict")
    connection = repository.connection
    if not connection.is_in_transaction():
        raise SourceConflict("owning_transaction_required")
    async with connection.transaction():
        previous = await repository.read_command(command_id)  # command -> source -> actual row
        relation = await connection.fetchrow(
            """SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=$1 AND c.relname='scheduled_tasks' AND c.relkind IN ('r','p'))
               AND NOT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace=pg_my_temp_schema()
                   AND relname='scheduled_tasks') AS scheduled_relation""",
            repository.owner_domain,
        )
        if not relation or not relation["scheduled_relation"]:
            raise SourceConflict("owning_schedule_relation_required")
        family = "deadline" if definition.schedule["task_type"] == "deadline" else "schedule"
        state = "active" if definition.available else "disabled"
        if previous is not None:
            request, old_result = previous
            if (
                request != digest([expected_revision, old_result.encode()])
                or (source_id is not None and source_id != old_result.source_id)
                or old_result.material_digest != definition.material_digest
                or old_result.family != family
                or old_result.origin != "git"
                or old_result.state != state
                or (
                    definition.declaration is not None
                    and old_result.declaration != definition.declaration
                )
            ):
                raise SourceConflict("command_binding_conflict")
            return StagedGitScheduleDefinition(old_result)
        columns = ",".join(SCHEDULE_FIELDS)
        table = f'"{repository.owner_domain}".scheduled_tasks'
        old = None
        if source_id is not None:
            await repository.lock_source(source_id)
            row = await connection.fetchrow(
                f"SELECT id,source,{columns} FROM {table} WHERE id=$1 FOR UPDATE", source_id
            )
            if row is None or row["source"] != "toml":
                raise SourceConflict("git_schedule_required")
            old = await repository.read(source_id)
        if (old is None and expected_revision is not None) or (
            old is not None and (expected_revision != old.revision or old.state == "deleted")
        ):
            raise SourceConflict("source_revision_conflict")
        if old is not None and (old.origin != "git" or old.family != family):
            raise SourceConflict("creation_binding_immutable")
        if old is None and not definition.available:
            raise SourceConflict("source_definition_unavailable")
        material = definition.schedule
        values = [material[key] for key in SCHEDULE_FIELDS]
        if (
            old is not None
            and old.material_digest == definition.material_digest
            and old.state == state
        ):
            # Verify actual row too: a runtime/public legacy writer may have
            # changed it without revising this dormant source metadata.
            if _row_material({key: row[key] for key in SCHEDULE_FIELDS}) != definition.row_json:
                raise SourceConflict("schedule_material_drift")
            await connection.execute(
                "INSERT INTO obligation_source_commands (id,source_id,request_digest,body) "
                "VALUES ($1,$2,$3,$4)",
                command_id,
                source_id,
                digest([expected_revision, old.encode()]),
                old.encode(),
            )
            return StagedGitScheduleDefinition(old)
        if source_id is None:
            markers = ",".join(f"${index}" for index in range(1, len(values) + 1))
            row = await connection.fetchrow(
                f"INSERT INTO {table} ({columns},source) VALUES ({markers},'toml') "
                f"RETURNING id,source,{columns}",
                *values,
            )
            source_id = row["id"]
            await repository.lock_source(source_id)
        else:
            setters = ",".join(f"{key}=${index}" for index, key in enumerate(SCHEDULE_FIELDS, 2))
            row = await connection.fetchrow(
                f"UPDATE {table} SET {setters},updated_at=now() WHERE id=$1 "
                f"RETURNING id,source,{columns}",
                source_id,
                *values,
            )
        if _row_material({key: row[key] for key in SCHEDULE_FIELDS}) != definition.row_json:
            raise SourceConflict("schedule_material_write_mismatch")
        value = DefinitionRevision(
            repository.owner_domain,
            family,
            source_id,
            old.generation + 1 if old else 1,
            definition.material_digest,
            definition.declaration or old.declaration,
            "git",
            state=state,
        )
        result = await repository.revise(command_id, value, expected_revision=expected_revision)
        return StagedGitScheduleDefinition(result)
