"""Dormant owning scheduled-row/source writer (RFC0038 D5).

The internal schedule_create source branch persists actual runtime row/source
material together. Its authenticated D7 input producer is not delivered here.
Values authenticate neither Git input, context, catalog nor a creator. Current
ordinary OFF create/sync/tick paths never perform this module's database IO.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from butlers.core.goal_admission import digest
from butlers.core.obligation_sources import (
    DefinitionRevision,
    ObligationSourceRepository,
    RuntimeCreationSnapshot,
    SourceConflict,
)
from butlers.core.obligations import (
    DeclarationError,
    ObligationDeclaration,
    project_loaded_schedule,
)

if TYPE_CHECKING:
    from butlers.config import ScheduleSpec

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
    elif (
        row["prompt"] is not None
        or not row["job_name"]
        or (row["job_args"] is not None and type(row["job_args"]) is not dict)
    ):
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
    # Actual registered Python identity, never a name/bool supplied over MCP.
    job_handler: object = field(default=None, kw_only=True)

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
            if self.job_handler is not None:
                from butlers.scheduled_jobs import classify_schedule_job

                if (
                    self.schedule["dispatch_mode"] != "job"
                    or classify_schedule_job(self.job_handler) != "model_backed"
                ):
                    raise DeclarationError("unclassified_model_job")
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
        if self.schedule["dispatch_mode"] != "prompt" and self.job_handler is None:
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
            | (
                {
                    "model_job_handler": self.job_handler.__module__
                    + "."
                    + self.job_handler.__qualname__
                }
                if self.job_handler is not None
                else {}
            )
        )


def compile_git_schedule_definition(
    *,
    owner_domain: str,
    normalized_schedule: dict,
    declaration: ObligationDeclaration | None,
    context_material: dict | None,
    job_handler: object = None,
) -> GitScheduleDefinition:
    """Copy full normalized source inputs; the value itself authenticates nothing."""
    try:
        return GitScheduleDefinition(
            owner_domain,
            _row_material(normalized_schedule),
            declaration,
            _json(context_material) if context_material is not None else None,
            job_handler=job_handler,
        )
    except (TypeError, ValueError, RecursionError):
        raise DeclarationError("invalid_schedule_material") from None


@dataclass(frozen=True)
class RuntimeScheduleDefinition(GitScheduleDefinition):
    """Server-selected creation material; a value NEVER verifies an invocation.

    The MCP scheduling schema cannot construct/pass this internal argument.
    The real D7 verifier must eventually supply the immutable creation facts.
    Owning-row persistence and ceiling checks here do not stand in for it.
    """

    creator: RuntimeCreationSnapshot
    dependencies: tuple[str, ...] = ()
    deadline_status: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if type(self.creator) is not RuntimeCreationSnapshot or self.declaration is None:
            raise DeclarationError("creator_snapshot_required")
        self.creator.check(self.declaration)
        if type(self.dependencies) is not tuple or len(set(self.dependencies)) != len(
            self.dependencies
        ):
            raise DeclarationError("invalid_deadline_dependencies")
        for value in self.dependencies:
            if type(value) is not str:
                raise DeclarationError("invalid_deadline_dependencies")
            try:
                if str(UUID(value)) != value:
                    raise ValueError
            except ValueError:
                raise DeclarationError("invalid_deadline_dependencies") from None
        if self.deadline_status is not None and (
            self.schedule["task_type"] != "deadline"
            or self.deadline_status
            not in {"pending", "alerted", "escalated", "completed", "expired"}
        ):
            raise DeclarationError("invalid_deadline_status")
        if self.schedule["task_type"] != "deadline" and self.dependencies:
            raise DeclarationError("invalid_deadline_dependencies")

    @property
    def material_digest(self) -> str:
        material = {"schedule": super().material_digest, "depends_on": self.dependencies}
        if self.deadline_status is not None:
            material["deadline_status"] = self.deadline_status
        return digest(material)


def compile_runtime_schedule_definition(
    *,
    owner_domain: str,
    normalized_schedule: dict,
    declaration: ObligationDeclaration,
    context_material: dict | None,
    creator: RuntimeCreationSnapshot,
    depends_on: tuple[str, ...] = (),
    deadline_status: str | None = None,
) -> RuntimeScheduleDefinition:
    """Freeze actual selected material, rejecting rather than clipping widening."""
    return RuntimeScheduleDefinition(
        owner_domain,
        _row_material(normalized_schedule),
        declaration,
        _json(context_material) if context_material is not None else None,
        creator,
        depends_on,
        deadline_status,
    )


@dataclass(frozen=True)
class StagedScheduleDefinition:
    """Historical replay or staged result; commit/release and later fences remain."""

    definition: DefinitionRevision


# Keep the earlier internal spelling import-compatible; runtime values no longer
# pretend that their origin was Git.
StagedGitScheduleDefinition = StagedScheduleDefinition


@dataclass(frozen=True)
class GitScheduleSyncCommand:
    """Internal prepared write, never Git/context/catalog authentication.

    A selected source set is explicit. Omission is not a deletion request or
    proof that the full daemon configuration has been reconciled.
    """

    command_id: UUID
    definition: GitScheduleDefinition
    source_id: UUID | None = None
    expected_revision: str | None = None

    def __post_init__(self):
        if (
            not isinstance(self.command_id, UUID)
            or type(self.definition) is not GitScheduleDefinition
            or (self.source_id is not None and not isinstance(self.source_id, UUID))
            or (
                self.expected_revision is not None
                and (
                    self.source_id is None
                    or type(self.expected_revision) is not str
                    or not re.fullmatch(r"[0-9a-f]{64}", self.expected_revision)
                )
            )
        ):
            raise SourceConflict("invalid_git_sync_command")


def bind_git_schedule_sync(
    schedules: list[ScheduleSpec], commands: tuple[GitScheduleSyncCommand, ...]
) -> str:
    """Bind every selected typed input before IO or historical replay.

    This is selected-source persistence, not a second TOML parser, automatic
    deletion pass or the missing production policy/context factory. Fields
    unavailable in ScheduleSpec cannot be smuggled in through a prepared value.
    """
    if (
        type(commands) is not tuple
        or not commands
        or any(type(command) is not GitScheduleSyncCommand for command in commands)
    ):
        raise SourceConflict("invalid_git_sync_command")
    names = [command.definition.schedule["name"] for command in commands]
    owners = {command.definition.owner_domain for command in commands}
    sources = [command.source_id for command in commands if command.source_id is not None]
    if (
        len(owners) != 1
        or len(set(names)) != len(names)
        or len({command.command_id for command in commands}) != len(commands)
        or len(set(sources)) != len(sources)
        or len(schedules) != len(commands)
    ):
        raise SourceConflict("git_sync_set_mismatch")
    by_name = dict(zip(names, commands, strict=True))
    seen = set()
    for spec in schedules:
        projection = project_loaded_schedule(spec)
        name = projection["name"]
        command = by_name.get(name)
        if command is None or name in seen:
            raise SourceConflict("git_sync_set_mismatch")
        seen.add(name)
        material = dict.fromkeys(SCHEDULE_FIELDS)
        material.update(dict(projection.fields))
        material.update(
            enabled=True,
            complexity=material["complexity"] or "workhorse",
            timezone=material["timezone"] or "UTC",
        )
        if (
            _row_material(material) != command.definition.row_json
            or projection.declaration.declaration != command.definition.declaration
        ):
            raise SourceConflict("git_sync_material_mismatch")
    return next(iter(owners))


async def stage_git_schedule_sync(
    repository: ObligationSourceRepository,
    commands: tuple[GitScheduleSyncCommand, ...],
) -> tuple[StagedScheduleDefinition, ...]:
    """Stage a bound set in one owning TX, command -> source -> row order.

    All command locks precede every source lock; both sets use stable ordering
    so overlapping batches do not reverse the existing single-source order.
    The scheduler validates the submitted typed material before entering here.
    """
    for command in sorted(commands, key=lambda value: value.command_id.int):
        await repository.read_command(command.command_id)
    for source_id in sorted(
        (command.source_id for command in commands if command.source_id is not None),
        key=lambda value: value.int,
    ):
        await repository.lock_source(source_id)
    # Consistent name order also applies to newly inserted unique names. Return
    # receipts in the caller's order without interpreting them as current authority.
    results = {}
    for command in sorted(commands, key=lambda value: value.definition.schedule["name"]):
        results[command.command_id] = await stage_git_schedule_definition(
            repository,
            command_id=command.command_id,
            definition=command.definition,
            source_id=command.source_id,
            expected_revision=command.expected_revision,
        )
    return tuple(results[command.command_id] for command in commands)


async def _stage_schedule_definition(
    repository: ObligationSourceRepository,
    *,
    command_id: UUID,
    expected_revision: str | None,
    definition: GitScheduleDefinition | RuntimeScheduleDefinition,
    source_id: UUID | None = None,
    next_run_at: datetime | None = None,
) -> StagedScheduleDefinition:
    """Write actual schedule + source revision/cancel/outbox in ONE owning TX.

    A savepoint prevents partial scheduled-row changes if a caller catches a
    refusal. It does not commit the outer transaction. Existing nominal/run
    fields are preserved; a new dormant row has NULL next_run_at. This is not
    the missing current-config/scheduler/cutover registration.
    """
    if (
        type(definition) not in (GitScheduleDefinition, RuntimeScheduleDefinition)
        or definition.owner_domain != repository.owner_domain
    ):
        raise SourceConflict("source_owner_mismatch")
    if source_id is not None and not isinstance(source_id, UUID):
        raise SourceConflict("invalid_source_id")
    if expected_revision is not None and (
        type(expected_revision) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_revision)
    ):
        raise SourceConflict("source_revision_conflict")
    runtime = type(definition) is RuntimeScheduleDefinition
    origin, row_source = ("runtime", "db") if runtime else ("git", "toml")
    creator = definition.creator if runtime else None
    if (
        runtime
        and source_id is None
        and definition.schedule["task_type"] == "cron"
        and (not isinstance(next_run_at, datetime) or next_run_at.utcoffset() is None)
    ):
        raise SourceConflict("nominal_schedule_slot_required")
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
        family = (
            "model_job"
            if definition.job_handler is not None
            else "deadline"
            if definition.schedule["task_type"] == "deadline"
            else "schedule"
        )
        state = "active" if definition.available else "disabled"
        if previous is not None:
            request, old_result = previous
            if (
                request != digest([expected_revision, old_result.encode()])
                or (source_id is not None and source_id != old_result.source_id)
                or old_result.material_digest != definition.material_digest
                or old_result.family != family
                or old_result.origin != origin
                or old_result.creator != creator
                or old_result.state != state
                or (
                    definition.declaration is not None
                    and old_result.declaration != definition.declaration
                )
            ):
                raise SourceConflict("command_binding_conflict")
            return StagedScheduleDefinition(old_result)
        columns = ",".join(SCHEDULE_FIELDS)
        table = f'"{repository.owner_domain}".scheduled_tasks'
        old = None
        if source_id is not None:
            await repository.lock_source(source_id)
            row = await connection.fetchrow(
                f"SELECT id,source,{columns} FROM {table} WHERE id=$1 FOR UPDATE", source_id
            )
            if row is None or row["source"] != row_source:
                raise SourceConflict(
                    "runtime_schedule_required" if runtime else "git_schedule_required"
                )
            old = await repository.read(source_id)
        if (old is None and expected_revision is not None) or (
            old is not None and (expected_revision != old.revision or old.state == "deleted")
        ):
            raise SourceConflict("source_revision_conflict")
        if old is not None and (
            old.origin != origin or old.family != family or old.creator != creator
        ):
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
            return StagedScheduleDefinition(old)
        if source_id is None:
            markers = ",".join(f"${index}" for index in range(1, len(values) + 1))
            slot_column = ",next_run_at" if runtime else ""
            slot_value = f",${len(values) + 1}" if runtime else ""
            extras = [next_run_at] if runtime else []
            if runtime and family == "deadline":
                slot_column += ",deadline_status,fired_thresholds,depends_on"
                slot_value += f",'pending','[]'::jsonb,${len(values) + 2}"
                extras.append(list(definition.dependencies))
            row = await connection.fetchrow(
                f"INSERT INTO {table} ({columns},source{slot_column}) "
                f"VALUES ({markers},'{row_source}'{slot_value}) RETURNING id,source,{columns}",
                *values,
                *extras,
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
        if runtime and definition.deadline_status is not None:
            progress = await connection.fetchrow(
                f"SELECT deadline_status FROM {table} WHERE id=$1", source_id
            )
            if progress is None or progress["deadline_status"] != definition.deadline_status:
                raise SourceConflict("schedule_material_write_mismatch")
        value = DefinitionRevision(
            repository.owner_domain,
            family,
            source_id,
            old.generation + 1 if old else 1,
            definition.material_digest,
            definition.declaration or old.declaration,
            origin,
            creator=creator,
            state=state,
        )
        result = await repository.revise(command_id, value, expected_revision=expected_revision)
        return StagedScheduleDefinition(result)


async def stage_git_schedule_definition(
    repository: ObligationSourceRepository,
    *,
    command_id: UUID,
    expected_revision: str | None,
    definition: GitScheduleDefinition,
    source_id: UUID | None = None,
) -> StagedScheduleDefinition:
    if type(definition) is not GitScheduleDefinition:
        raise SourceConflict("source_owner_mismatch")
    return await _stage_schedule_definition(
        repository,
        command_id=command_id,
        expected_revision=expected_revision,
        definition=definition,
        source_id=source_id,
    )


async def stage_runtime_schedule_definition(
    repository: ObligationSourceRepository,
    *,
    command_id: UUID,
    definition: RuntimeScheduleDefinition,
    next_run_at: datetime,
) -> StagedScheduleDefinition:
    """Stage the real new runtime row and source atomically, never activate a goal.

    This creation-only entrypoint cannot revise a caller-selected existing row
    or replace its original creator snapshot. Reconciliation retains command ID.
    """
    if type(definition) is not RuntimeScheduleDefinition:
        raise SourceConflict("creator_snapshot_required")
    return await _stage_schedule_definition(
        repository,
        command_id=command_id,
        expected_revision=None,
        definition=definition,
        next_run_at=next_run_at,
    )


async def revise_runtime_schedule_definition(
    repository: ObligationSourceRepository,
    *,
    command_id: UUID,
    source_id: UUID,
    expected_revision: str,
    definition: RuntimeScheduleDefinition,
    submitted_fields: dict,
    mutate_row: Callable[[object], Awaitable[None]],
) -> StagedScheduleDefinition:
    """Run the existing row mutator under the source/row locks, then revise.

    This is an internal owning adapter, not an authenticated request boundary.
    The callback must use only this connection; no second pool or network call
    belongs under these locks. A mismatch rolls the entire row mutation back.
    Exact command reconciliation never reexecutes the callback.
    """
    if type(definition) is not RuntimeScheduleDefinition:
        raise SourceConflict("creator_snapshot_required")
    if definition.owner_domain != repository.owner_domain:
        raise SourceConflict("source_owner_mismatch")
    connection = repository.connection
    selected_fields = {k: v for k, v in submitted_fields.items() if k in SCHEDULE_FIELDS}
    if set(submitted_fields) - set(SCHEDULE_FIELDS) - {"depends_on", "deadline_status"}:
        raise SourceConflict("schedule_material_mismatch")
    if (
        "depends_on" in submitted_fields
        and tuple(submitted_fields["depends_on"] or ()) != definition.dependencies
        or "deadline_status" in submitted_fields
        and submitted_fields["deadline_status"] != definition.deadline_status
    ):
        raise SourceConflict("schedule_material_mismatch")
    async with connection.transaction():
        previous = await repository.read_command(command_id)
        if previous is not None:
            # Validate the actual normalized request, not just its prepared
            # definition. Do not rerun a mutation against a newer live row.
            if _row_material(definition.schedule | selected_fields) != definition.row_json:
                raise SourceConflict("schedule_material_mismatch")
        if previous is None:
            await repository.lock_source(source_id)
            old = await repository.read(source_id)
            if (
                old is None
                or old.revision != expected_revision
                or old.state == "deleted"
                or old.origin != "runtime"
                or old.creator != definition.creator
            ):
                raise SourceConflict("creation_binding_immutable")
            # The row lock precedes all existing row-validation and update SQL.
            row = await connection.fetchrow(
                f"SELECT id,source,depends_on,{','.join(SCHEDULE_FIELDS)} FROM "
                f'"{repository.owner_domain}".scheduled_tasks WHERE id=$1 FOR UPDATE',
                source_id,
            )
            if row is None or row["source"] != "db":
                raise SourceConflict("runtime_schedule_required")
            await mutate_row(connection)
            row = await connection.fetchrow(
                f"SELECT id,source,depends_on,deadline_status,{','.join(SCHEDULE_FIELDS)} FROM "
                f'"{repository.owner_domain}".scheduled_tasks WHERE id=$1 FOR UPDATE',
                source_id,
            )
            if (
                row is None
                or _row_material({key: row[key] for key in SCHEDULE_FIELDS}) != definition.row_json
                or (
                    old.family == "deadline"
                    and (
                        tuple(row["depends_on"] or ()) != definition.dependencies
                        or (
                            definition.deadline_status is not None
                            and row["deadline_status"] != definition.deadline_status
                        )
                    )
                )
            ):
                raise SourceConflict("schedule_material_mismatch")
        return await _stage_schedule_definition(
            repository,
            command_id=command_id,
            source_id=source_id,
            expected_revision=expected_revision,
            definition=definition,
        )


async def delete_runtime_schedule_definition(
    repository: ObligationSourceRepository,
    *,
    command_id: UUID,
    source_id: UUID,
    expected_revision: str,
    definition: RuntimeScheduleDefinition,
    delete_row: Callable[[object], Awaitable[None]],
) -> StagedScheduleDefinition:
    """Delete the actual row and retain its immutable source tombstone together.

    Only an exact historical delete command can reconcile a missing row. No
    lookup absence is turned into a successful deletion or creation authority.
    """
    if type(definition) is not RuntimeScheduleDefinition:
        raise SourceConflict("creator_snapshot_required")
    if definition.owner_domain != repository.owner_domain:
        raise SourceConflict("source_owner_mismatch")
    connection = repository.connection
    async with connection.transaction():
        previous = await repository.read_command(command_id)
        if previous is not None:
            request, result = previous
            if (
                result.source_id != source_id
                or result.state != "deleted"
                or result.origin != "runtime"
                or result.creator != definition.creator
                or result.material_digest != definition.material_digest
                or request != digest([expected_revision, result.encode()])
            ):
                raise SourceConflict("command_binding_conflict")
            return StagedScheduleDefinition(result)
        await repository.lock_source(source_id)
        old = await repository.read(source_id)
        if (
            old is None
            or old.revision != expected_revision
            or old.state == "deleted"
            or old.origin != "runtime"
            or old.creator != definition.creator
            or old.material_digest != definition.material_digest
        ):
            raise SourceConflict("source_revision_conflict")
        table = f'"{repository.owner_domain}".scheduled_tasks'
        row = await connection.fetchrow(
            f"SELECT id,source,depends_on,{','.join(SCHEDULE_FIELDS)} FROM {table} "
            "WHERE id=$1 FOR UPDATE",
            source_id,
        )
        if (
            row is None
            or row["source"] != "db"
            or _row_material({key: row[key] for key in SCHEDULE_FIELDS}) != definition.row_json
            or (
                old.family == "deadline"
                and tuple(row["depends_on"] or ()) != definition.dependencies
            )
        ):
            raise SourceConflict("schedule_material_mismatch")
        await delete_row(connection)
        if await connection.fetchrow(f"SELECT id FROM {table} WHERE id=$1", source_id) is not None:
            raise SourceConflict("schedule_delete_unconfirmed")
        value = replace(old, generation=old.generation + 1, state="deleted")
        result = await repository.revise(command_id, value, expected_revision=expected_revision)
        return StagedScheduleDefinition(result)
