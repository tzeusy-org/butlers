"""Server-held Git schedule material and owning full-set reconciliation.

A local final registry and a held runtime policy describe source material. They
are NOT an authenticated fleet catalog, current DB-policy proof, D7 creator
binding or an admission-mode selector. Startup captures this material without
source IO while OFF; admission must independently obtain its verified evidence
and catalog. No model/public MCP method accepts these objects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from butlers.config import ButlerConfig, obligation_config_material_digest
from butlers.core.goal_admission import digest
from butlers.core.obligation_admission import (
    DarkSubmission,
    prepare_dark_submission,
    run_source_transaction,
)
from butlers.core.obligation_schedule_sources import (
    SCHEDULE_FIELDS,
    GitScheduleDefinition,
    compile_git_schedule_definition,
    stage_git_schedule_definition,
)
from butlers.core.obligation_sources import SourceConflict
from butlers.core.obligations import DeclarationError, project_loaded_schedule
from butlers.core.runtime_config import RuntimeConfig
from butlers.core.tool_catalog import ToolCatalog

if TYPE_CHECKING:
    from butlers.core.obligation_sources import DefinitionRevision


def _registered_model_job_handler(owner_domain: str, job_name: str | None):
    # Read the exact registry used by background.dispatch_scheduled_task. A
    # copied job name or a replacement callback is not the traced D18 closure.
    from butlers.background import _deterministic_jobs_for_butler
    from butlers.scheduled_jobs import classify_schedule_job

    handler = _deterministic_jobs_for_butler(owner_domain).get(job_name)
    return handler if classify_schedule_job(handler) == "model_backed" else None


@dataclass(frozen=True)
class LoadedGitSources:
    owner_domain: str
    source_sha256: str
    config_digest: str
    context_json: str
    definitions: tuple[GitScheduleDefinition, ...]

    @property
    def digest(self) -> str:
        return digest(
            {
                "source": self.source_sha256,
                "config": self.config_digest,
                "context": json.loads(self.context_json),
                "definitions": [item.material_digest for item in self.definitions],
            }
        )


def capture_loaded_git_sources(
    config: ButlerConfig, runtime: RuntimeConfig, registry: ToolCatalog
) -> LoadedGitSources:
    """Copy the actual startup inputs; never reread/reparse TOML or query DB.

    Held policy can include legacy defaults or stale-cache fallback. Its scope
    is deliberately explicit; neither timestamps nor a config hash upgrade it
    to fresh persisted policy. Local registration is likewise not fleet proof.
    """
    if (
        type(config) is not ButlerConfig
        or type(runtime) is not RuntimeConfig
        or type(registry) is not ToolCatalog
        or runtime.butler_name != config.name
        or any(item.owner_domain != config.name for item in registry.descriptors)
    ):
        raise DeclarationError("source_owner_mismatch")
    if (
        type(config.source_sha256) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", config.source_sha256)
        or config.source_material_sha256 is None
    ):
        raise DeclarationError("loaded_source_unavailable")
    if obligation_config_material_digest(config) != config.source_material_sha256:
        raise DeclarationError("loaded_source_changed")
    context = {
        "version": 1,
        "config_sha256": config.source_sha256,
        "typed_config_digest": config.source_material_sha256,
        "catalog_scope": "local_registration",
        "registry_generation": registry.generation_digest,
        "classification_complete": registry.classification_complete,
        "runtime_policy_scope": "held_effective",
        "runtime_policy": {
            "core_groups": runtime.core_groups,
            "core_groups_narrowing_reason_digest": digest(runtime.core_groups_narrowing_reason),
            "catalog_read_sensitivity": runtime.catalog_read_sensitivity,
            "tool_exposure_policy": runtime.tool_exposure_policy,
        },
    }
    context_json = json.dumps(context, sort_keys=True, separators=(",", ":"), allow_nan=False)
    definitions = []
    seen = set()
    for spec in config.schedules:
        projection = project_loaded_schedule(spec)
        if spec.name in seen:
            raise DeclarationError("duplicate_git_schedule")
        seen.add(spec.name)
        row = dict.fromkeys(SCHEDULE_FIELDS)
        row.update(dict(projection.fields))
        row.update(
            enabled=True,
            complexity=row["complexity"] or "workhorse",
            timezone=row["timezone"] or "UTC",
        )
        definitions.append(
            compile_git_schedule_definition(
                owner_domain=config.name,
                normalized_schedule=row,
                declaration=projection.declaration.declaration,
                context_material=context if registry.classification_complete else None,
                job_handler=(
                    _registered_model_job_handler(config.name, spec.job_name)
                    if row["dispatch_mode"] == "job"
                    else None
                ),
            )
        )
    return LoadedGitSources(
        config.name,
        config.source_sha256,
        config.source_material_sha256,
        context_json,
        tuple(definitions),
    )


async def reconcile_loaded_git_sources(
    pool, snapshot: LoadedGitSources
) -> tuple[DefinitionRevision, ...]:
    """Reconcile a complete held config in ONE owning source transaction.

    This internal dark adapter does not select/enable an admission mode. The
    actual startup performs zero B2 IO while OFF. Its explicit dark selector
    owns this complete captured config before registering the scheduler source
    consumer. No caller/model hash authenticates this snapshot.

    Reconciliation reads current committed/staged rows; it is NOT historical
    command replay. After an unconfirmed commit the caller must explicitly
    reconcile/read back this full desired state, never infer success from a
    hash. Selected prepared-command replay retains its separate exact contract.
    """
    if type(snapshot) is not LoadedGitSources:
        raise SourceConflict("loaded_source_required")
    names = [item.schedule["name"] for item in snapshot.definitions]
    if len(names) != len(set(names)) or any(
        item.owner_domain != snapshot.owner_domain for item in snapshot.definitions
    ):
        raise SourceConflict("git_sync_set_mismatch")

    async def reconcile(repository):
        connection = repository.connection
        # Owning guard and full-inventory lock precede discovery. This lock is
        # never used to claim a runtime creator or a cross-domain schema.
        await repository.lock_git_inventory()
        table = f'"{repository.owner_domain}".scheduled_tasks'
        rows = await connection.fetch(
            f"SELECT id,name,source FROM {table} WHERE source='toml' ORDER BY name,id"
        )
        by_name = {row["name"]: row for row in rows}
        if len(by_name) != len(rows):
            raise SourceConflict("duplicate_git_schedule")
        identities = await connection.fetch(
            "SELECT id FROM obligation_definitions WHERE origin='git' "
            "AND family IN ('schedule','deadline','model_job') AND state<>'deleted' ORDER BY id"
        )
        source_ids = {row["id"] for row in rows} | {row["id"] for row in identities}
        # All command locks precede ALL source locks, including removals.
        commands = {name: uuid4() for name in sorted(set(names) | set(by_name))}
        for command in sorted(commands.values(), key=lambda value: value.int):
            await repository.read_command(command)
        for source in sorted(source_ids, key=lambda value: value.int):
            await repository.lock_source(source)
        current = {source: await repository.read(source) for source in source_ids}
        # A dangling active Git definition is an integrity error, not proof of
        # deletion. A real full-config removal below requires its actual row.
        row_ids = {row["id"] for row in rows}
        if any(row["id"] not in row_ids for row in identities):
            raise SourceConflict("git_schedule_row_missing")
        results = []
        for definition in sorted(snapshot.definitions, key=lambda item: item.schedule["name"]):
            name = definition.schedule["name"]
            row = by_name.get(name)
            source = row["id"] if row else None
            old = current.get(source)
            if source is not None:
                locked = await connection.fetchrow(
                    f"SELECT id,name,source FROM {table} WHERE id=$1 FOR UPDATE", source
                )
                if locked is None or locked["source"] != "toml" or locked["name"] != name:
                    raise SourceConflict("git_inventory_changed")
            if old is not None and (old.origin != "git" or old.state == "deleted"):
                raise SourceConflict("creation_binding_immutable")
            if not definition.available and old is None:
                # Missing declarations/context and deterministic jobs cannot
                # create an active obligation or delete an ordinary job row.
                continue
            result = await stage_git_schedule_definition(
                repository,
                command_id=commands[name],
                definition=definition,
                source_id=source,
                expected_revision=old.revision if old else None,
            )
            results.append(result.definition)
        for name in sorted(set(by_name) - set(names)):
            row = by_name[name]
            old = current[row["id"]]
            if old is not None and old.origin != "git":
                raise SourceConflict("creation_binding_immutable")
            # Full inventory, unlike selected sync, explicitly owns removal of
            # stale Git rows. Preserve every db/runtime row and its provenance.
            locked = await connection.fetchrow(
                f"SELECT id,name,source FROM {table} WHERE id=$1 FOR UPDATE", row["id"]
            )
            if locked is None or locked["source"] != "toml" or locked["name"] != name:
                raise SourceConflict("git_schedule_required")
            await connection.execute(f"DELETE FROM {table} WHERE id=$1", row["id"])
            # A source tombstone may already be committed while its old TOML
            # row remains. Remove that stale row without attempting a forbidden
            # successor revision or duplicating immutable history/outbox.
            if old is not None and old.state != "deleted":
                removed = replace(old, generation=old.generation + 1, state="deleted")
                results.append(
                    await repository.revise(commands[name], removed, expected_revision=old.revision)
                )
        return tuple(results)

    return await run_source_transaction(pool, snapshot.owner_domain, reconcile)


@dataclass(frozen=True)
class ScheduleClaim:
    """A committed legacy CAS, with optional staged (not admitted) material."""

    source_id: UUID
    submission: DarkSubmission | None


@dataclass(frozen=True)
class GitScheduleRuntime:
    """Lifecycle-owned source staging; never public/model or D7 authority.

    Only sources reconciled from the complete actual startup snapshot enter
    this immutable binding. Runtime-created rows await their real authenticated
    creator producer. No network/model call occurs while owning locks are held.
    """

    owner_domain: str
    bindings: tuple[tuple[DefinitionRevision, GitScheduleDefinition], ...]

    def supports(self, row) -> bool:
        return any(
            definition.source_id == row["id"]
            and (
                row["dispatch_mode"] == "prompt"
                or (
                    row["dispatch_mode"] == "job"
                    and material.job_handler is not None
                    and _registered_model_job_handler(self.owner_domain, row["job_name"])
                    is material.job_handler
                )
            )
            for definition, material in self.bindings
        )

    async def _locked(self, repository, selected, *, command=None):
        source_id = selected["id"]
        binding = next((pair for pair in self.bindings if pair[0].source_id == source_id), None)
        if binding is None or repository.owner_domain != self.owner_domain:
            raise SourceConflict("schedule_source_unregistered")
        expected, material = binding
        if material.job_handler is not None and (
            _registered_model_job_handler(self.owner_domain, material.schedule["job_name"])
            is not material.job_handler
        ):
            raise SourceConflict("model_job_registration_changed")
        # Match full reconciliation: inventory -> command -> source -> row.
        # Recheck the relation on every transaction, including temp shadows.
        await repository.lock_git_inventory()
        if command is not None:
            await repository.read_command(command)
        await repository.lock_source(source_id)
        current = await repository.read(source_id)
        table = f'"{repository.owner_domain}".scheduled_tasks'
        row = await repository.connection.fetchrow(
            f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE",
            source_id,
        )
        if (
            current != expected
            or current.state != "active"
            or row is None
            or row["source"] != "toml"
            or any(key not in row or row[key] != value for key, value in material.schedule.items())
            or any(
                key in SCHEDULE_FIELDS and row[key] != value
                for key, value in dict(selected).items()
            )
        ):
            raise SourceConflict("schedule_material_changed")
        return current, row

    async def _capture(self, repository, definition, slot):
        occurrence = await repository.capture(definition.source_id, definition.revision, slot=slot)
        token = uuid4()
        if not await repository.claim(definition.source_id, occurrence, token):
            raise SourceConflict("occurrence_already_claimed")
        return await prepare_dark_submission(repository, definition.source_id, occurrence, token)

    async def read_committed_submission(self, pool, submitted: DarkSubmission) -> DarkSubmission:
        """Reread the exact claim and registered material in a fresh owning TX.

        A previously returned object or command is not a current receipt. This
        source-side read validates its full tuple against the actual row,
        current revision and claim, and returns only after transaction/pool
        release. It never authenticates a transport caller, loads a fleet
        catalog, or submits admission. Runtime creator and feed/cause adapters
        remain separate; this registered Git runtime cannot stand in for them.
        """
        if type(submitted) is not DarkSubmission:
            raise SourceConflict("source_submission_required")

        async def read(repository):
            definition, _ = await self._locked(repository, {"id": submitted.definition.source_id})
            current = await prepare_dark_submission(
                repository,
                definition.source_id,
                submitted.occurrence_id,
                submitted.claim_token,
            )
            if current != submitted:
                raise SourceConflict("source_submission_changed")
            return current

        # Do not let a nested savepoint promote a caller's uncommitted write
        # into the independent committed readback promised by this API.
        return await run_source_transaction(pool, self.owner_domain, read, require_fresh=True)

    async def claim_cron(
        self, pool, selected, *, next_run_at, disable: bool
    ) -> ScheduleClaim | None:
        """Source -> row lock, original nominal CAS + occurrence in ONE TX.

        Nothing is returned until commit and pool release. A transaction with
        an unknown commit is never retried as a second legacy CAS here. OFF
        does not construct/call this object. Auto-disable retains the existing
        last legacy dispatch but retires, rather than creates, dark work.
        """
        slot = selected["next_run_at"]
        if type(slot) is not datetime or slot.utcoffset() is None or type(disable) is not bool:
            raise SourceConflict("invalid_nominal_slot")
        if not disable and (
            type(next_run_at) is not datetime
            or next_run_at.utcoffset() is None
            or next_run_at <= slot
        ):
            raise SourceConflict("invalid_nominal_slot")
        command = uuid4()

        async def claim(repository):
            definition, row = await self._locked(
                repository, selected, command=command if disable else None
            )
            table = f'"{repository.owner_domain}".scheduled_tasks'
            if row["task_type"] != "cron":
                raise SourceConflict("invalid_nominal_slot")
            if disable:
                claimed = await repository.connection.fetchrow(
                    f"UPDATE {table} SET enabled=false,next_run_at=NULL,updated_at=now() "
                    "WHERE id=$1 AND next_run_at IS NOT DISTINCT FROM $2 RETURNING id",
                    row["id"],
                    slot,
                )
            else:
                claimed = await repository.connection.fetchrow(
                    f"UPDATE {table} SET next_run_at=$2,updated_at=now() "
                    "WHERE id=$1 AND next_run_at IS NOT DISTINCT FROM $3 RETURNING id",
                    row["id"],
                    next_run_at,
                    slot,
                )
            if claimed is None:
                return None
            if disable:
                await repository.revise(
                    command,
                    replace(definition, generation=definition.generation + 1, state="disabled"),
                    expected_revision=definition.revision,
                )
                return ScheduleClaim(row["id"], None)
            staged = await self._capture(repository, definition, "cron:" + slot.isoformat())
            return ScheduleClaim(row["id"], staged)

        return await run_source_transaction(pool, self.owner_domain, claim)

    async def expire_deadline(self, pool, selected, *, status, as_of):
        """Disable the real expired row and cancel staged work in the same TX."""
        command = uuid4()

        async def expire(repository):
            definition, row = await self._locked(repository, selected, command=command)
            if (
                row["task_type"] != "deadline"
                or status != "expired"
                or type(as_of) is not date
                or row["target_date"] >= as_of
            ):
                raise SourceConflict("invalid_deadline_expiry")
            table = f'"{repository.owner_domain}".scheduled_tasks'
            expired = await repository.connection.fetchrow(
                f"UPDATE {table} SET deadline_status=$2,enabled=false,updated_at=now() "
                "WHERE id=$1 AND target_date=$3 "
                "AND deadline_status IS NOT DISTINCT FROM $4 RETURNING id",
                row["id"],
                status,
                selected["target_date"],
                selected["deadline_status"],
            )
            if expired is None:
                return None
            return await repository.revise(
                command,
                replace(definition, generation=definition.generation + 1, state="disabled"),
                expected_revision=definition.revision,
            )

        return await run_source_transaction(pool, self.owner_domain, expire)

    async def record_deadline(self, pool, selected, *, threshold, status, fired, run_at):
        """Capture only AFTER successful legacy dispatch, beside threshold CAS.

        A failed dispatch never calls this method and leaves its threshold
        unfired. The stable slot is the actual target date plus selected
        threshold, not the recording clock. Concurrent/stale observations do
        not overwrite newer threshold progress or manufacture another source.
        """
        from butlers.core.temporal.deadlines import (
            compute_next_deadline_status,
            find_unfired_threshold,
        )

        if type(run_at) is not datetime or run_at.utcoffset() is None:
            raise SourceConflict("invalid_deadline_threshold")

        async def record(repository):
            definition, row = await self._locked(repository, selected)
            if any(row[key] != selected[key] for key in ("fired_thresholds", "deadline_status")):
                return None
            if (
                row["task_type"] != "deadline"
                or threshold not in row["alert_thresholds"]
                or threshold in row["fired_thresholds"]
                or fired != [*row["fired_thresholds"], threshold]
                or threshold
                != find_unfired_threshold(
                    days_remaining=(row["target_date"] - run_at.date()).days,
                    alert_thresholds=row["alert_thresholds"],
                    fired_thresholds=row["fired_thresholds"],
                )
                or status
                != compute_next_deadline_status(
                    current_status=row["deadline_status"] or "pending", fired_threshold=threshold
                )
            ):
                raise SourceConflict("invalid_deadline_threshold")
            table = f'"{repository.owner_domain}".scheduled_tasks'
            recorded = await repository.connection.fetchrow(
                f"UPDATE {table} SET deadline_status=$2,fired_thresholds=$3,"
                "last_run_at=$4,updated_at=now() WHERE id=$1 AND enabled=true "
                "AND target_date=$5 AND fired_thresholds=$6 "
                "AND deadline_status IS NOT DISTINCT FROM $7 RETURNING id",
                row["id"],
                status,
                fired,
                run_at,
                row["target_date"],
                row["fired_thresholds"],
                row["deadline_status"],
            )
            if recorded is None:
                return None
            slot = "deadline:" + row["target_date"].isoformat() + ":" + digest(threshold)
            return await self._capture(repository, definition, slot)

        return await run_source_transaction(pool, self.owner_domain, record)


async def start_schedule_sources(
    pool, snapshot: LoadedGitSources | None, *, mode: str
) -> GitScheduleRuntime | None:
    """Default-OFF lifecycle selector for durable staging, NOT admission.

    No roster setting is enabled by this implementation. `dark` requires the
    actual owning schema migration and full reconciliation before registration;
    missing/failed source input refuses startup instead of claiming coverage.
    Neither mode supplies verified fleet/current invocation/creator authority.
    """
    if type(mode) is not str or mode not in {"off", "dark"}:
        raise SourceConflict("invalid_source_mode")
    if mode == "off":
        return None
    if type(snapshot) is not LoadedGitSources:
        raise SourceConflict("loaded_source_required")
    revisions = await reconcile_loaded_git_sources(pool, snapshot)
    by_name = {item.schedule["name"]: item for item in snapshot.definitions}
    bindings = []
    # Reconciliation results carry source identities, not names. Match the
    # entire material digest rather than guessing source/name from a caller.
    for revision in revisions:
        if revision.state != "active":
            continue
        matches = [
            item for item in by_name.values() if item.material_digest == revision.material_digest
        ]
        if len(matches) != 1 or not matches[0].available:
            raise SourceConflict("loaded_source_binding_invalid")
        bindings.append((revision, matches[0]))
    return GitScheduleRuntime(snapshot.owner_domain, tuple(bindings))
